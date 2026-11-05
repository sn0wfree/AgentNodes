# 功能清单与合并优化

> 2026-09-29 · 基于 code-map + CLI 28 命令 + core 28 包  
> 目标：认全功能、找出可合并/可删的重叠面

---

## 1. 功能全景（8 个域）

| 域 | 能力 | 入口 |
|---|---|---|
| **数据** | 8 源行情、fallback、DuckDB/SQLite、导入/指纹 | `import` / `data_source` / `storage` |
| **因子** | 表达式 DSL、alpha_zoo 470+、IC/6 维/去重、复杂度门 | `validate` / `compute_factor` |
| **回测** | 双引擎（YAML Strategy / Callback）、多市场、metrics 合同 | `run` `evaluate` `reproduce` `engine` |
| **验证** | MC / Bootstrap / Walk-forward / 组合 / 硬阈值验收 | `validate-run` `portfolio` `accept` |
| **Agent** | ReAct 循环、工具、沙箱、压缩、claim 校验、LLM 适配 | `chat` / `agent/` / `llm/` |
| **编排** | Study 长程、Swarm、Goal、Workflow DAG、定时 | `autoresearch` `swarm` `goal` `schedule` |
| **研究状态** | Hypothesis、Memory、Session、Skills、Permissions | `hypothesis` `session` `skills` |
| **交付面** | CLI/TUI/REPL、REST+SSE、WebUI SPA、MCP、导出 | `serve` `api` `webui` `mcp` `export` |

**主价值流**：数据 → 因子 → 回测 → 验证 → 验收，由 Agent/Study 自动驱动。

---

## 2. 重叠与可合并项

### P0 — 用户可见入口过多（先合）

| 现状 | 问题 | 建议 |
|---|---|---|
| `serve` / `webui serve` / `api serve` | 三个起服命令 | **合并为 `serve`**，`--no-ui` / `--api-only` 作模式 |
| `run` / `evaluate` / `reproduce` | 都是跑策略，语义糊 | **合成 `backtest`** 子命令：`run\|rerun\|replay` |
| `validate` / `validate-run` / `accept` | 三层验证各一命令 | **合成 `check`**：`factor` / `run` / `accept` |
| `init` / `llm` / `preflight` | 环境三件套 | 保留，但 `init` 末步并入 `preflight` 一键体检 |

合并后 CLI 从 28 → **约 16**，命令面更接近用户任务。

### P1 — 架构双轨（中成本）

| 双轨 | 影响 | 建议 |
|---|---|---|
| `phase_engine` vs `langgraph_engine` | 研究轮双实现，`engine='dag'` 还是假选项 | 保留 langgraph 为唯一，phase 作 fallback 并标 deprecated |
| `chat_loop` vs `role_factory` 两个 AgentLoop 构造 | 工具/迭代/goal 注入不一致 | 收敛到 **AgentExecutor** 单构造（unified-agent-engine 既有设计） |
| `goal/workflow` vs `workflow/executor` | 两套 DAG | goal 已委托 swarm；**删 goal 侧重复 DAG** |
| `engine/` vs `backtest_engine/` | 两套回测路径 | 以 `backtest_engine` Protocol 为准，engine 只作 adapter |
| `chat.py` vs `web_session.py` | Session/EventBus 接线重复 | 合并 session 装配到 `container.py` 单点 |
| Jinja2 `/webui` vs React SPA | 双前端 | **删 HTMX**，只留 SPA |
| `sandbox/legacy` vs `StaticSandbox` | 能力在 legacy | 迁入 Protocol 实现，删 legacy |

### P2 — 模型/加载器三份（低成本）

| 项 | 建议 |
|---|---|
| `core/backtest_models` + `utils/backtest_models` + `engine/models` | 合成一个 `models.py` |
| `alpha_zoo/loader` vs `alpha_zoo_adapter/convert/yaml` | 只留 `loader`，旧三件归档 |
| 3 套 4 层 config loader（llm / acceptance / goal） | 抽 `config_merge` 单实现 |
| 根 `templates/` 漂移副本 | 删，只留 `src/.../templates` |
| `deepseek.py` / `qwen.py` 近乎相同 | 一个 `OpenAIReasoningFieldAdapter` 配置化 |

### P3 — 状态层名词过多

`Session` / `Study` / `Goal` / `Hypothesis` / `Memory` / `Workspace` / `Run` 七套实体。

**建议语义收敛**：

```text
Workspace
 └── Study（长程研究）
      ├── Round → Run（回测实验）
      ├── Hypothesis（假设/血统）
      └── Goal（完成条件）
Session（对话/工具轨迹）→ 挂在 Study 或独立 chat
Memory（跨会话经验）
```

不必先大改表结构，先统一 API 用词与 ID 前缀。

---

## 3. 建议保留的核心（不要动）

- 量化主链：`data_source → storage → compute_factor → backtest → validation → acceptance`
- Protocol+Registry+Adapter（DataLoader / DataStore / BacktestEngine）
- LLM provider 适配器 + `resolve_max_tokens` 自动策略
- 因子复杂度门 + 血统登记（QuantaAlpha 三道门）
- 测试体系（~560 文件）

---

## 4. 30 天可落地路线

| 周 | 动作 | 产出 |
|---|---|---|
| W1 | CLI 合并：`serve` / `backtest` / `check` | 命令 28→16，文档同步 |
| W2 | 删 HTMX 前端 + 根 templates 副本 + alpha_zoo 旧加载器 | -2k LOC 级 |
| W3 | AgentLoop 单构造 + goal 侧重复 DAG 删除 | 双轨收口 |
| W4 | backtest models 三合一 + config loader 抽取 | 结构清晰 |

---

## 5. 一页结论

**能合并的主要是「入口重复」和「双轨实现」**：  
CLI 三个起服/三套验证/三个跑回测 → 各留一个；  
研究引擎、Agent 构造、DAG、前端、回测协议各留一条主路径。  

量化主链与 Agent 能力本身是强的，优化收益在**减认知负担**，不在加功能。
