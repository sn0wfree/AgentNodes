# AgentNodes 参考手册

> 命令、工具、配置的完整清单。**README 只放框架主体，细节都在这里。**
> 本文的数据在 2026-11-05 实测（`quantnodes-research --help` / `mcp list-tools` / `swarm list`）。
>
> ⚠️ **「移除批次」列的含义**：业务功能正按
> [`design/subtraction-roadmap-v2-20261105.md`](design/subtraction-roadmap-v2-20261105.md)
> 分批移除。标「批次 4/5/6」的条目会在对应批次删除；标「—」的是框架本体，长期保留。

---

## 1. CLI 顶层命令（18 个，2026-11-05 批次 4 后实测）

`quantnodes-research <command> [args]`

> 批次 4 已删除 `run` / `evaluate` / `list` / `engine` / `accept` 五个子命令；
> `backtest` / `check` / `reproduce` 仍在命令表中，但其底层 `apps/backtest`
> 已移除——**执行会报 `ModuleNotFoundError`**，属批次 4 的收尾未完成项。

### 1.1 框架本体

| 命令 | 用途 | 移除批次 |
|---|---|---|
| `init` | 5 步 TTY 向导：provider → model → API key → timeout → (Tushare) | — |
| `llm` | LLM 供应商快速切换（`list` / `use` / `show` / `add-key`） | — |
| `compact` | compact 配置检查（`show` 打印当前生效值 + 派生值） | — |
| `skills` | 技能管理（`list` / `show` / `search`） | — |
| `swarm` | 多智能体协同（`list` / `inspect` / `run` / `cancel`） | — |
| `mcp` | MCP 服务器（`serve` / `list-tools`） | — |
| `serve` | 启动服务（默认 Web UI + API，`--api-only` 仅 API） | — |
| `api` | HTTP API 服务器（`serve`） | — |
| `webui` | Web UI 仪表盘（`serve`） | — |
| `session` | 会话管理（`stats` / `list` / `show` / `search` / `delete`） | — |
| `hangs` | 卡死防护事件报告（`--hours` / `--limit`） | — |
| `schedule` | 定时研究（cron 池） | — |
| `status` | 查看工作区状态 | 部分 |
| `preflight` | 启动前环境检查 | 批次 5 |

### 1.2 业务命令（减法进行中）

| 命令 | 用途 | 状态 |
|---|---|---|
| `backtest` | `run` / `rerun` / `replay` 三态回测 | ⚠ 底层已删，待收尾 |
| `check` | `factor` / `run` / `accept` 校验 | ⚠ 底层已删，待收尾 |
| `reproduce` | 复现历史 run | ⚠ 底层已删，待收尾 |
| `import` | 导入价格数据 | 批次 5 |
| `validate` | 验证因子（IC/IR/6 维评分） | 批次 5 |

**批次 4 已删**：`run` · `evaluate` · `list` · `engine` · `accept`

### 1.3 调用矩阵

```text
quantnodes-research                → TUI（TTY）/ argparse help（非 TTY）
quantnodes-research --repl         → 旧版 prompt_toolkit REPL
quantnodes-research --banner       → 同 --repl（v0.3.x 别名）
quantnodes-research <subcommand>   → argparse 子命令
echo "" | quantnodes-research      → argparse help（无 TTY 不进 TUI）
```

---

## 2. MCP 工具（17 个）

`quantnodes-research mcp serve --transport {stdio,sse} --port 8900`

| 工具 | 参数 | 用途 | 移除批次 |
|---|---|---|---|
| `list_skills` | — | 列出所有可用技能 | — |
| `skill` | `name` | 按名称加载技能内容 | — |
| `search_memory` | `query, limit` | 搜索持久化记忆（keyword + recency） | — |
| `add_memory` | `title, content, memory_type` | 添加持久化记忆 | — |
| `list_sessions` | — | 列出所有会话 | — |
| `search_messages` | `query` | 搜索消息 | — |
| `list_swarm_presets` | — | 列出所有 swarm preset | — |
| `run_swarm` | `preset_name, workspace, task, max_workers, timeout` | 执行 swarm preset | — |
| `get_swarm_status` | `run_id` | 查询 swarm 运行状态 | — |
| `websearch` | `query, max_results` | 网页搜索（MiniMax → DuckDuckGo 回退） | — |
| `webfetch` | `url, max_chars` | 抓取网页 → Markdown | — |
| `read_document` | `path, max_pages` | PDF 文本提取（需 PyMuPDF） | — |
| `run_backtest` | `strategy, action, description, yaml_path, background, workspace` | 从 config.yaml 运行回测 | 批次 4 |
| `compute_factor` | `expression, asset, factor_name, n_samples, workspace` | 计算因子表达式 | 批次 5 |
| `get_market_data` | `codes, start_date, end_date, interval, source, max_rows` | 获取 OHLCV 行情 | 批次 5 |
| `list_data_sources` | — | 列出数据源及可用性 | 批次 5 |
| `search_symbol` | `query, market, limit` | 模糊搜索证券代码 | 批次 5 |

---

## 3. Swarm preset（37 个）

`quantnodes-research swarm run <name> --workspace <path> --task "<描述>"`
定义在 `src/agentnodes/kernel/swarm/presets/*.yaml`（YAML DAG：节点 + 依赖 + 工具白名单）。

| 类别 | preset |
|---|---|
| **研究团队** | `equity_research_team` · `fundamental_research_team` · `quant_research_team` · `quant_research_committee` · `ml_quant_lab` · `pairs_research_lab` · `statistical_arbitrage_desk` · `earnings_research_desk` |
| **投资委员会** | `investment_committee` · `value_investing_committee` · `risk_committee` · `portfolio_review_board` · `asset_allocation_committee` · `global_allocation_committee` |
| **宏观 / 全球** | `macro_strategy_forum` · `macro_rates_fx_desk` · `global_equities_desk` |
| **衍生品 / 加密** | `crypto_trading_desk` · `crypto_lab` · `derivatives_strategy_desk` · `convertible_bond_team` |
| **情绪 / 资金流** | `sentiment_intelligence_team` · `flow_analysis_team` · `social_alpha_team` |
| **技术分析 / 微观** | `technical_analysis_panel` · `market_microstructure_team` |
| **行业 / 主题** | `sector_rotation` · `commodity_research_team` · `etf_allocation_desk` · `event_driven_task_force` |
| **流水线** | `full_pipeline`（10 agent）· `goal_autoresearch_9agent`（11 agent） |
| **目标驱动** | `goal_factor_research` · `goal_market_analysis` · `goal_portfolio_review` · `goal_risk_assessment` · `goal_strategy_review` |

> `goal_*` 系列 preset 的 DAG 本身有效，但其引用的 goal 工具随批次 2 移除，
> 执行时会因 capability 缺失而报错。

---

## 4. 能力注册（22 kind，2026-11-05 实测）

`src/agentnodes/apps/capabilities.py` 的 `CAPABILITIES` 表。kernel 通过
`resolve(kind)` 取实现，不 import apps。

| 域 | kind | 移除批次 |
|---|---|---|
| db | `db.get_connection` · `db.init_db` · `db.save_ohlcv_to_db` · `db.module` | 批次 5 |
| factor | `factor.compute` · `factor.compute_error` · `factor.long_to_wide` | 批次 5 |
| datasource | `datasource.registry` · `datasource.base` · `datasource.detect_market` | 批次 5 |
| data | `data.readiness` · `data.clean` | 批次 5 |
| evolution | `evolution.resolve_harness` · `evolution.holdout_sample` · `evolution.proposal` · `evolution.validation_gate` · `evolution.agent` | — |
| evolution 工具 | `evolution.read_journal` · `evolution.read_scoreboard` · `evolution.read_validation` · `evolution.read_holdout` · `evolution.propose_genome_update` | — |

**已移除（不再注册）**：
- 批次 2：`goal.store` / `goal.models` / `goal.context` / `goal.expression` /
  `hypothesis.registry` / `hypothesis.auto_creator`
- 批次 3：`study.graph` / `study.store` / `study.scheduler` / `study.status` /
  `study.bootstrap` / `study.ticket_sink`（Ticket/Assignment 已内置为 L3 机制）
- 批次 4：`backtest.from_yaml` / `backtest.script` / `backtest.decide` / `config.runner`

---

## 5. 环境变量

### 5.1 LLM

| 变量 | 说明 |
|---|---|
| `OPENAI_API_KEY` | API 密钥（必填，至少一个 provider） |
| `OPENAI_BASE_URL` | 兼容端点，默认 `https://api.openai.com/v1` |
| `OPENAI_MODEL` | 默认模型 |
| `DEEPSEEK_API_KEY` / `KIMI_API_KEY` / `QWEN_API_KEY` / `ANTHROPIC_API_KEY` | 其他 provider |
| `QUANTNODES_LLM_JSON` | `llm.json` 配置路径覆盖 |
| `QUANTNODES__LLM__*` | 嵌套配置覆盖（与环境变量同名映射） |

### 5.2 运行时

| 变量 | 默认 | 说明 |
|---|---|---|
| `SR_AGENT_WALLCLOCK_TIMEOUT` | — | 单次 agent 墙钟超时（秒） |
| `SR_ALLOW_SHELL_TOOLS` | 关闭 | `1` 开启 `run_command`（bash/python）；plan mode 始终禁用 |
| `SR_ALLOW_REGISTRATION` | 关闭 | `1` 开启 API 注册端点 |
| `STRATEGY_RESEARCH_ALLOW_DEV_SECRET` | 关闭 | `1` 允许非生产环境用开发密钥 |
| `QUANTNODES_RESEARCH_GOAL_DB_PATH` | `~/.quantnodes-research/goals.db` | Ticket/Assignment/调度共用库 |

### 5.3 日志

| 变量 | 默认 | 说明 |
|---|---|---|
| `SR_LOG_LEVEL` | `INFO` | 日志级别 |
| `SR_LOG_JSON` | 关闭 | `1` 输出 JSON 格式 |
| `SR_LOG_FILE` | 关闭 | 文件日志路径；不设则不落文件 |

> CLI 是**严格开关语义**：只有 `SR_LOG_JSON` 或 `SR_LOG_FILE` 设置了才装配 stderr handler，
> 默认不干扰 TUI/界面输出。

### 5.4 Web / API

| 变量 | 默认 | 说明 |
|---|---|---|
| `JWT_SECRET` | 自动生成并持久化 | JWT 签名密钥；非本地部署必须显式设置 |
| `CORS_ORIGINS` | `*` | 允许来源，逗号分隔 |
| `STATIC_DIR` | `webui/static/` | 静态文件目录 |

---

## 6. 工作区结构

```
/path/to/workspace/
├── config.yaml            # 数据源 / 回测参数 / 成本 / 风控
├── data.duckdb            # 共享数据库
├── .prompts/              # 角色提示词（框架通用 + 业务角色）
├── .skills/               # 方法论技能包
└── strategies/{name}/
    ├── program.md         # playbook
    ├── prepare.py         # 目标函数（Agent 不改）
    ├── strategy.py        # Agent 唯一可改
    └── runs/run_XXXX/
        ├── metrics.json   # 指标
        ├── run_card.json  # Trust Layer（SHA-256）
        └── run_card.md
```

> 批次 4–7 会收窄工作区：回测产物目录随批次 4 消失，`.skills/` 研究技能随批次 7 归档，
> 最终只保留框架通用模板。改动细节见 `design/subtraction-roadmap-v2-20261105.md`。

---

## 7. 包内模板

`src/agentnodes/templates/`（随包分发，`smart_init_workspace_templates` 复制到工作区）：

| 路径 | 内容 |
|---|---|
| `config.yaml` / `prepare.py` / `program.md` / `strategy.py` / `README.md` | 研究模板（批次 7 归档） |
| `.prompts/` | 24 个角色 + `_common/` |
| `.skills/` | 26 个方法论技能 + `_common/` |
| `workflows/` | 工作流 JSON 定义 |

---

## 8. 数据源（8 个 loader）

`data.source` 字段配置。

| Loader | 市场 | 鉴权 | 自动 fallback |
|---|---|---|---|
| `tencent` | A 股 | 否 | 是 |
| `eastmoney` | A 股 + 港股 | 否 | 是 |
| `akshare` | 全市场 | 否 | 是 |
| `tushare` | A 股 + 期货 + 基金 | 是 | 是 |
| `yfinance` | 美股 + 港股 + 加密 | 否 | 是 |
| `local` | 自定义 CSV/Parquet | 否 | 否（避免静默降级） |
| `fred` | 美国宏观 | 是 | 否 |
| `ifind` | 宏观 + 港美股 | 是 | 否 |

**全部随批次 5 移除。**

---

## 9. 架构门禁命令

```bash
python scripts/arch/check_dangling_imports.py        # 悬空 import（exit 1 = 有问题）
python scripts/arch/check_dangling_imports.py --layers   # 附层方向矩阵
python scripts/arch/check_path_literals.py           # 坏路径拼接
python scripts/arch/sync_architecture_diagram.py     # 架构图是否过时
python scripts/arch/sync_architecture_diagram.py --write # 回写实测数字
python scripts/arch/graph_layer_audit.py <graph.json>   # graphify 图的层矩阵（第二种量纲）
```

工具选型规则（不可互换）：**上行违规只认 `test_layer_matrix`**；graphify 用于拓扑/热点/体量，
两者只比方向与簇分布，不比绝对值。详见 [`guides/`](guides/) 与 `scripts/README.md`。
