# vibe-trading 完整功能调研与借鉴清单

> 调研对象：`vibe-trading-ai` v0.1.11（HKUDS 出品，License MIT）
> 安装位置：`~/vibe_env/lib/python3.11/site-packages/vibe_trading_ai-0.1.11.dist-info/`
> 仓库：https://github.com/HKUDS/Vibe-Trading
> 文档站：https://vibetrading.wiki/docs/
> 调研日期：2026-07-22
>
> 本文档同时承担两个目的：
> 1. **vibe-trading 全功能清单**：作为外部参考资料存档
> 2. **strategy-research 借鉴路线图**：按优先级标记可搬 / 不要搬

---

## 目录

1. [项目定位与统计](#1-项目定位与统计)
2. [顶层架构（5 层）](#2-顶层架构5-层)
3. [CLI 入口（`cli/`）](#3-cli-入口cli)
4. [MCP 服务器（54 个工具）](#4-mcp-服务器54-个工具)
5. [HTTP API（FastAPI）](#5-http-apifastapi)
6. [AgentLoop（5 层 context 压缩）](#6-agentloop5-层-context-压缩)
7. [工具层（~70 个 BaseTool）](#7-工具层70-个-basetool)
8. [Skills 系统（87 个 SKILL.md）](#8-skills-系统87-个-skillmd)
9. [Swarm 多智能体（30 个 preset）](#9-swarm-多智能体30-个-preset)
10. [Goal / Hypothesis / Memory / Session](#10-goal--hypothesis--memory--session)
11. [Scheduled Research](#11-scheduled-research)
12. [Backtest 子系统](#12-backtest-子系统)
13. [Alpha Zoo（460 个因子）](#13-alpha-zoo460-个因子)
14. [Shadow Account](#14-shadow-account)
15. [Live Trading 层](#15-live-trading-层)
16. [Channels（14 IM + Email + WebSocket）](#16-channels14-im--email--websocket)
17. [Preflight + Provider 配置](#17-preflight--provider-配置)
18. [安全层（`src/security/`）](#18-安全层srcsecurity)
19. [CLI UI 组件](#19-cli-ui-组件)
20. [完整文件清单](#20-完整文件清单)
21. [strategy-research 借鉴路线图](#21-strategy-research-借鉴路线图)

---

## 1. 项目定位与统计

**"自然语言金融研究 AI 代理 + 回测 + 多智能体协同 + 多渠道分发"**。

| 维度 | 数据 |
|---|---|
| 包名 | `vibe-trading-ai` 0.1.11 |
| 作者 | HKUDS |
| Python | ≥ 3.11 |
| License | MIT |
| 入口命令 | `vibe-trading`、`vibe-trading-mcp` |
| 标语 | 54 MCP tools · 87 skills · 30 swarm presets |
| 受众 | 港股 / A 股 / 美股 / 加密 / 期货 / 外汇研究者 |
| 栈 | LangChain + LangGraph-checkpoint + DuckDB + DuckDB-parquet 缓存 + WeasyPrint |
| 文件数 | **≈1262 个**（≈50K LOC） |
| 配置文件 | `~/.vibe-trading/.env`（0600，原子写） |
| 运行数据根 | `~/.vibe-trading/` |

> 数据来源：`cat vibe_trading_ai-0.1.11.dist-info/RECORD | grep -v __pycache__ | wc -l` → 1271，扣除 dist-info 和 bin 入口后约 1262。

---

## 2. 顶层架构（5 层）

```
┌─ 入口层 ───────────────────────────────────────────┐
│  cli/ (Rich REPL + argparse)                       │
│  api_server.py (FastAPI + SSE + WebUI SPA)         │
│  mcp_server.py (FastMCP stdio/SSE, 仅 research)    │
└────────────────────────────────────────────────────┘
            │
┌─ 渠道层 ── src/channels/（14 个 IM 适配器）────────┐
│  Telegram / Discord / Slack / Signal / WhatsApp /  │
│  Feishu / DingTalk / MS Teams / QQ / Napcat /      │
│  Mochat / Matrix / WeCom / Weixin / Email /        │
│  WebSocket（WebUI）                                │
└────────────────────────────────────────────────────┘
            │
┌─ 智能体层 ── src/agent/ + src/swarm/ + src/goal/ ──┐
│  AgentLoop（ReAct + 5 层 context 压缩）             │
│  SwarmRuntime（30 个 YAML DAG preset）              │
│  GoalStore / HypothesisRegistry / PersistentMemory │
└────────────────────────────────────────────────────┘
            │
┌─ 工具层 ── src/tools/ + src/skills/（87 个）───────┐
│  ~70 BaseTool 子类 + MCP 远程工具                  │
│  SkillsLoader（SKILL.md + frontmatter + 支持文件）   │
└────────────────────────────────────────────────────┘
            │
┌─ 数据/执行层 ── backtest/ + src/live/ + src/shadow/ ┐
│  21 个 DataLoader（按市场 fallback chain）          │
│  10 个回测引擎（A 股/期货/加密/外汇/期权/组合）       │
│  5 个组合优化器 + 验证层（Monte Carlo/Boostrap/WF）  │
│  11 个券商连接器（IBKR/Robinhood/Alpaca/Binance等）  │
└────────────────────────────────────────────────────┘
```

**核心代码分布**：
- `backtest/`（顶层包，独立）：2289 行、49 个文件
- `src/`：智能体、工具、Skills、Swarm、Goal、Hypothesis、Channels、Live
- `cli/`：Rich REPL + argparse 后备
- `api_server.py` / `mcp_server.py`：HTTP / MCP 入口

---

## 3. CLI 入口（`cli/`）

| 项 | 内容 |
|---|---|
| **入口** | `vibe-trading` = `cli:main`；`vibe-trading-mcp` = `mcp_server:main` |
| **解析框架** | argparse（不用 click/cyclopts）；`cli/_legacy.py` 是 argparse 真实源 |
| **顶端子命令** | `run`, `serve`, `provider`, `data`（QVeris）, `channels`, `list`, `show`, `chat`, `init`, `setup`, `dev`, `memory`, `connector`, `alpha`（插件）, `hypothesis`（插件）+ 顶层标志 `--continue / --list / --show / --code / --pine / --trace / --skills / --swarm-presets/inspect/run/list/show/cancel / --sessions / --session-chat / --upload` |
| **首次运行向导** | `cli/onboard.py` 五步：选 provider → 选 model → 输 key → 选 timeout → 选 Tushare token；原子写入 `~/.vibe-trading/.env` |
| **REPL** | `_interactive_loop` + `_run_one_turn`；rich.live 显示 Codex 风格 activity rail；ThinkingSpinner + StreamRenderer；ctrl_c 三态（清空→提示→退出） |
| **`mcp` 子命令** | **不存在**；MCP 服务由独立 `vibe-trading-mcp` 启动 |

### 3.1 Slash 命令清单（REPL 内 `/…`）

> Source of truth：`cli/commands/slash_router.py`。三组在 import 时追加（live + data + 别名）。

| 命令 | 功能 |
|---|---|
| `/help` | 命令清单 + 快捷键 |
| `/model` | 显示当前 provider/model + 提示如何重跑 init |
| `/memory [name\|search q\|forget]` | 持久化记忆 CRUD |
| `/history` | 列出会话（→ `cmd_sessions`） |
| `/goal [objective\|status\|start\|evidence #\|complete\|cancel]` | 管理当前研究目标（criteria checklist + audit） |
| `/search <q>` | FTS5 全文搜会话 |
| `/swarm` | 调用 swarm preset（committee / quant / risk） |
| `/skill` | 列出所有 skill |
| `/show <run_id>` | 回放历史 run |
| `/clear` | 清屏 + 丢历史 |
| `/pine <run_id>` | 生成 Pine Script |
| `/journal <path>` | 排队 "Analyze my trade journal at <path>" |
| `/shadow [path]` | Shadow dashboard / 训练新 shadow |
| `/export` | 占位（web UI 富导出） |
| `/debug` | 切换每轮 `[debug] iter=… tools=… elapsed=… ctx≈…` 行 |
| `/quit`（`q`/`exit`/`:q`）| 退出（exit code 2） |
| `/connector [...]` *（注入）* | IM / 交易连接器桥接 |
| `/halt` / `/stop` *（注入）* | 全局 HALT 旗标 |
| `/resume` *（注入）* | 清 HALT 旗标 |
| `/data [...]` *（注入）* | QVeris 配置（status / mode free\|paid / usage） |

### 3.2 顶级子命令用途

| 命令 | 作用 |
|---|---|
| `vibe-trading run --prompt "..."` | 一次性跑（JSON / no-rich / max-iter） |
| `vibe-trading serve --host --port --dev` | 启 FastAPI（端口默认 8000；`--dev` 同时起 Vite 前端 5173） |
| `vibe-trading provider login <provider>` / `doctor` | OAuth login / 自检 |
| `vibe-trading data status / mode / usage` | QVeris 网关配置 |
| `vibe-trading channels status / start / stop / login / pairing` | IM 渠道生命周期 |
| `vibe-trading list [--limit]` / `show <run_id>` | 回放 run |
| `vibe-trading chat --max-iter` | 进 REPL |
| `vibe-trading init` | 工作区初始化（**注意**：与 strategy-research 含义不同） |
| `vibe-trading setup --frontend-dir` | 拷贝前端 dist |
| `vibe-trading dev --port --frontend-port` | 全栈开发模式 |
| `vibe-trading memory list/show/search/forget` | 持久化记忆 |
| `vibe-trading connector list/use/configure/check/status/authorize/account/positions/orders/quote/history/start/stop/halt/resume/revoke` | 交易连接器 |
| `vibe-trading alpha list/show/bench/compare/export-manifest` | 因子（插件式） |
| `vibe-trading hypothesis list/show/invalidate` | 假说（插件式） |
| `vibe-trading --swarm-*` | swarm 顶层标志 |
| `vibe-trading --continue <sid> "<prompt>"` | 续接 session |

### 3.3 子命令发现机制

- **硬编码**，非 entry-points
- `cli/_legacy.py:_build_parser()`（行 4353）手工建 argparse 树
- 末尾插入插件钩子：`src/factors/cli_handlers.add_subparser`（→ `alpha`） + `src/hypotheses/cli_handlers.add_subparser`（→ `hypothesis`）
- `cli/main.py` 同时实现 `_build_typer_app()`（行 1355），但生产入口仍走 argparse

---

## 4. MCP 服务器（54 个工具）

> FastMCP，server name `"Vibe-Trading"`，stdio 默认 / `--transport sse --port 8900`。
> **全部 research-only**（无下单 / 撤单 / 改密工具）。
> Shell 工具（read/write/edit/bash）stdio 默认开启，SSE 由 `VIBE_TRADING_ENABLE_SHELL_TOOLS` 控制。

### 4.1 Skill
| Tool | 用途 |
|---|---|
| `list_skills` | 列所有 skill（name + description JSON） |
| `load_skill` | 按名加载完整 SKILL.md |

### 4.2 Goal
| Tool | 用途 |
|---|---|
| `start_research_goal` | 创建/替换 session 的研究目标 + 3-5 acceptance criteria + budgets |
| `get_research_goal` | 当前目标快照 |
| `add_goal_evidence` | 向目标某个 criterion 追加 evidence（file/note/citation） |
| `update_research_goal_status` | 更新状态（active/complete/cancelled）+ audit row |

### 4.3 Backtest / 分析
| Tool | 用途 |
|---|---|
| `backtest` | 跑 vector 化回测（`config.json` + `code/signal_engine.py`） |
| `factor_analysis` | 跑因子 alpha 分析 |
| `analyze_options` | Black-Scholes 定价 + Greeks |
| `pattern_recognition` | 检测头肩顶 / 双底 / 楔形 等形态 |

### 4.4 I/O
| Tool | 用途 |
|---|---|
| `read_url` | 抓网页转 Markdown |
| `read_document` | PDF 文本抽取（OCR 回退） |
| `web_search` | DuckDuckGo 搜索 |
| `write_file` | 写文件（脚手架 `config.json` / `signal_engine.py`） |
| `read_file` | 读文件 |

### 4.5 交易连接器（**只读**）
| Tool | 用途 |
|---|---|
| `trading_connections` | 列出可选连接器 profile |
| `trading_select_connection` | 切换默认 profile |
| `trading_check` | 健康检查 |
| `trading_account` | 账户摘要 |
| `trading_positions` | 当前持仓 |
| `trading_orders` | 当前挂单（`--include-executions`） |
| `trading_quote` | 标的快照 |
| `trading_history` | 历史 bar（IBKR TWS / SDK adapter） |

### 4.6 Swarm
| Tool | 用途 |
|---|---|
| `list_swarm_presets` | 列出 preset |
| `run_swarm` *（async）* | 启动 swarm；`wait_seconds` / `start_only` 控制；每 ≤5s 通过 `ctx.report_progress` 发 SSE keepalive |
| `get_swarm_status` | 任务进度 / `is_stale` |
| `get_run_result` | 最终报告 + 任务 summaries |
| `list_runs` | 最近 runs |
| `reap_stale_runs` | 把宿主死的 running run 标 failed |
| `retry_run` | 重跑 failed/stale/cancelled |

### 4.7 市场数据（A 股专属）
| Tool | 用途 |
|---|---|
| `get_fund_flow` | 主力/超大/大/中/小单净额 |
| `get_dragon_tiger` | 龙虎榜（东方财富） |
| `get_northbound_flow` | 北向资金 |
| `get_margin_trading` | 融资融券 |
| `get_block_trades` | 大宗交易 |
| `get_shareholder_count` | 股东户数（季度） |
| `get_lockup_expiry` | 限售解禁（`horizon_days`） |
| `get_sector_info` | 行业 / 概念板块 |
| `get_research_reports` | 卖方研报 + 一致预期 |
| `get_stock_news` | 财经新闻（个股 / 行业 / 宏观） |
| `get_financial_statements` | 三大表 + 关键指标（年/季） |
| `get_options_chain` | 美股期权链 |
| `get_stock_profile` | 美 / 港公司画像 |
| `screen_market` | 全市场筛选 + 排序 |
| `search_symbol` | 代码 / 名称模糊查 |
| `get_macro_series` | 宏观时间序列（CPI / 利率） |
| `iwencai_search` | 自然语言 A 股研究查询（问财） |

### 4.8 美股
| Tool | 用途 |
|---|---|
| `get_sec_filings` | SEC filings |

### 4.9 Shadow
| Tool | 用途 |
|---|---|
| `analyze_trade_journal` | 解析用户交易日志 |
| `extract_shadow_strategy` | 从日志抽取影子策略 |
| `run_shadow_backtest` | 跑影子回测 |
| `render_shadow_report` | HTML/PDF 报告（8 节 + 图表） |
| `scan_shadow_signals` | 当日符合入场的标的（research-only） |

### 4.10 通用数据
| Tool | 用途 |
|---|---|
| `get_market_data` | 拉 OHLCV（按 loader 链） |

---

## 5. HTTP API（FastAPI）

> `api_server.py`：FastAPI + CORS + SPA static fallback。
> Auth：X-API-Key 或 `?api_key=` query；Loopback 检测；DNS rebinding 拦截；Cross-site 拦截。

| 路由模块 | 端点 |
|---|---|
| `alpha_routes.py` | `/alpha/list`, `/alpha/{id}`, `/alpha/bench` + SSE, `/alpha/compare` + SSE |
| `sessions_routes.py` | `/sessions` CRUD + `/messages` + `/events` SSE + `/run` + `/cancel` + `/runtime` |
| `runs_routes.py` | `/runs`, `/runs/{id}`, `/runs/{id}/code`, `/runs/{id}/pine` |
| `settings_routes.py` | `/settings/llm`, `/settings/data-sources`（dotenv 读写） |
| `channels_routes.py` | `/channels/status/start/stop/pairing/command` |
| `scheduled_routes.py` | `/scheduled-runs` |
| `swarm_routes.py` | `/swarm/presets/runs` + SSE + cancel/retry |
| `live_routes.py` | `/mandate/commit`, `/live/halt/resume/status/authorize`, `/live/runner/start/stop` |
| `uploads_routes.py` | `/upload`（50MB cap + 扩展名黑名单）+ `/shadow-reports/{id}` |
| `qveris_routes.py` | `/qveris/config/status` |
| `system_routes.py` | `/health`, `/correlation`, `/system/shutdown`, `/skills`, `/api`（路由索引） |

**生命周期**：
- `startup`：`src.preflight.run_preflight()` → 启 scheduled_research executor → 可选 `VIBE_TRADING_CHANNELS_AUTO_START` 起 channels
- `shutdown`：关 channels + executor

**前端**：`<repo>/frontend/dist/index.html` SPA，`--dev` 时 `npx vite --host 0.0.0.0`

---

## 6. AgentLoop（5 层 context 压缩）

> `src/agent/loop.py`，1607 行，`class AgentLoop`。

### 6.1 类签名
```python
class AgentLoop:
    def __init__(registry, llm, memory=None, event_callback=None,
                 max_iterations=50, persistent_memory=None) -> None
    def run(user_message, history=None, session_id="") -> dict
    def cancel() -> None
```

### 6.2 状态变量
- `registry`, `llm`, `memory`, `_event_callback`, `max_iterations`
- `_called_ok: set[str]`（已成功调用的工具白名单）
- `_cancel_event: threading.Event`
- `_previous_summary`（iterative compaction 用）
- `_persistent_memory`, `_run_iteration`
- per-run：iteration, final_content, content-filter counters, goal continuation state, react_trace, llm_usage_summary

### 6.3 关键常量
| 常量 | 值 |
|---|---|
| `KEEP_RECENT` | 3 |
| `TOOL_RESULT_LIMIT` | 10 000 |
| `LLM_USAGE_ARTIFACT` | `llm_usage.json` |
| collapse preservation | 6 messages |
| collapse text min | 2 400 chars |
| head/tail | 900 / 500 chars |
| tail token budget | 20 000 |
| 压缩阈值 | 50% / 70% / 100% × `_token_threshold()` |

### 6.4 ReAct 迭代（每轮）
1. 检测取消 / drain 后台通知
2. 估计 tokens
3. 应用压缩层（microcompact / context_collapse / auto_compact）
4. 可选注入 wrap-up nudge
5. 流式 LLM（text + reasoning）
6. 记录 usage
7. 处理 content-filter 响应
8. 若文本回复 → finalize；否则串并行执行工具

### 6.5 5 层 Context 压缩

| 层 | 触发条件 | 行为 |
|---|---|---|
| 1. **microcompact** | `tokens > 0.5×threshold` | 把 `KEEP_RECENT=3` 之前的工具结果标 `[cleared]` |
| 2. **context_collapse** | `tokens > 0.7×threshold` | 头 900 + 尾 500 chars 折叠；**无 API 调用** |
| 3. **auto_compact** | `tokens > threshold` | LLM 生成结构化 handoff summary；保留 20K token 尾部 |
| 4. **compact 工具** | 模型主动调 `compact(focus_topic=...)` | 标记请求 → `_auto_compact` + 主题优先级 |
| 5. **iterative update** | 多次 auto_compact 时 | 在既有 summary 上增量更新而非重建（**减少信息衰减**） |

### 6.6 `_fix_tool_pairs`
压缩后修复孤儿 tool_call / tool_result 对：
- 移除 tool_call_id 不存在的 tool_result
- 为缺少 tool_result 的 assistant tool_call 插入 stub（"result is in compression summary"）

### 6.7 Token 记账
- 规范化 `usage_metadata` → input/output/total
- 每个 run 原子写 `<run_dir>/llm_usage.json`
- 发 `llm_usage` 事件
- **不**持久化本地启发式估算作为 usage

### 6.8 工具批处理
- 连续**只读**调用 → ThreadPoolExecutor 并行
- **写 / 非读**调用 → 串行
- 每次调用有 cancel/timeout + heartbeat + 进度事件 + workspace counter + bounded result 注入 + trace record + `tool_result` event

### 6.9 Heartbeat
- `HeartbeatTimer` 包住工具调用：后台 daemon 线程每 `_HEARTBEAT_INTERVAL_S`（≥0.5s）触发 SSE keepalive
- 超时返回 JSON `tool_timeout` 错误，worker 标记 timed out

### 6.10 Trace + Redaction
- `TraceWriter` JSONL trace
- 事件：start, user/assistant msg, thinking, tool result, compact, answer, cancel, error, forced_text, content-filter, goal-continuation
- 大 text / result sidecar 到 `trace-blobs/` 或 `tool-results/`（SHA-256 寻址）
- JSON tool result 走 `redact_payload`；敏感结构键（account_ref 等刻意保留）

### 6.11 Reasoning 处理
- Kimi K2.5 / DeepSeek reasoner / Qwen thinking → 保留为 `reasoning_content`
- Gemini thought signatures → 复制到 provider-neutral + `google` extra_content slots
- `thinking_chunks` 也可保留 streaming reasoning

---

## 7. 工具层（~70 个 BaseTool）

### 7.1 抽象（`src/agent/tools.py`，94 行）

```python
class BaseTool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict = {}      # JSON Schema
    repeatable: bool = False
    is_readonly: bool = True

    def execute(self, **kwargs) -> str: ...   # abstract

    @classmethod
    def check_available(cls) -> bool:
        return True

    def to_openai_schema(self) -> dict:
        # 返回 function-call JSON Schema
```

```python
class ToolRegistry:
    _tools: Dict[str, BaseTool]
    def register(self, tool)       # id 冲突会覆盖
    def get(self, name) -> BaseTool
    def get_definitions(self) -> list
    def execute(self, name, params) -> str   # 总是有效 JSON
    # tool_names / __len__ / __contains__
```

### 7.2 Registry 构造（`src/tools/__init__.py`）

- `pkgutil` 自动发现所有非 private 模块
- 递归收集所有 `BaseTool` 子类
- 缓存 + `check_available()` 跳过
- 默认**抑制** shell 工具，除非 `include_shell_tools=True`
- 注入依赖（memory / session / event）到 selected tools
- 可选追加 MCP 远程工具

### 7.3 `build_registry` vs `build_swarm_registry`

| 函数 | 用途 |
|---|---|
| `build_registry(agent_config=…, include_shell_tools=…)` | 构建完整可用本地注册 + 可选全部 MCP |
| `build_swarm_registry(tool_names, agent_config=…, include_shell_tools=…)` | 先 prunes 远程 MCP → build → `_filter_registry` 到 whitelist；drop 不可用 requested 名 + warning |

### 7.4 远程 MCP 工具（`MCPRemoteTool`）

- `build_mcp_tool_wrappers(server_name, server_config, local_server_name=…)` 创建
- 本地名 `mcp_<server>_<tool>`
- 转发 schema-filtered args
- 默认 `is_readonly=False`, `repeatable=True`

### 7.5 工具清单（按类别）

| 类别 | 工具 |
|---|---|
| **回测 / 因子** | `backtest`, `factor_analysis`, `alpha_bench`, `alpha_compare`, `alpha_zoo` |
| **市场数据** | `get_market_data`, `screen_market`, `search_symbol`, `get_block_trades`, `get_dragon_tiger`, `get_fund_flow`, `get_northbound_flow`, `get_margin_trading`, `get_shareholder_count`, `get_lockup_expiry`, `get_sector_info`, `get_research_reports`, `get_stock_news` |
| **基本面** | `get_fundamentals`, `get_financial_statements`, `get_macro_series`, `fred_macro` |
| **美股** | `get_sec_filings`, `get_stock_profile`, `get_options_chain`, `options_pricing` |
| **分析** | `pattern`, `financial_rigor`, `report_audit`, `image_vision` |
| **Web/I-O** | `read_url`, `web_search`, `read_document`, `read_file`, `write_file`, `edit_file`, `bash`, `background_run`, `check_background` |
| **Skills** | `load_skill`, `save_skill`, `patch_skill`, `delete_skill`, `skill_file` |
| **Goal/Hypothesis** | `start_research_goal`, `get_research_goal`, `update_research_goal_status`, `add_goal_evidence`, `create_hypothesis`, `update_hypothesis`, `search_hypotheses`, `link_backtest` |
| **Memory/Session** | `remember`, `session_search` |
| **Swarm / Compaction** | `run_swarm`, `compact` |
| **交易连接器** | `trading_connections`, `trading_select_connection`, `trading_check`, `trading_account`, `trading_positions`, `trading_orders`, `trading_quote`, `trading_history`, `trading_place_order`, `trading_cancel_order` |
| **Shadow** | `shadow_account`（多个工具） |
| **Auto-pilot** | `run_research_autopilot`, `generate_backtest_config`, `scaffold_signal_engine`, `link_autopilot_backtest` |
| **A 股特殊** | `iwencai_search`, `research_reports`, `northbound`, `fund_flow`, `margin_trading` |
| **QVeris 网关** | `qveris_search`, `qveris_inspect`, `qveris_execute` |
| **基础** | `mcp`, `redaction`, `trade_journal_parsers` |

---

## 8. Skills 系统（87 个 SKILL.md）

### 8.1 文件格式

```
src/skills/{skill_name}/
├── SKILL.md                       # frontmatter + Markdown 正文
├── examples.md                    # 可选
├── example_signal_engine.py       # 可选（技术分析类技能提供）
└── references/                    # 可选，按主题分目录
    ├── 主题1/file1.md
    └── 主题2/file2.md
```

**Frontmatter keys**：
- `name`
- `description`
- `category` ∈ {data-source, strategy, analysis, asset-class, crypto, flow, tool, other}

### 8.2 Loader（`src/agent/skills.py`）

```python
@dataclass
class Skill:
    name: str
    description: str
    category: str = "other"
    body: str
    dir_path: Path
    metadata: dict

class SkillsLoader:
    def __init__(skills_dir, user_skills_dir=~/.vibe-trading/skills/user)
    def get_descriptions() -> list       # 系统 prompt 用的摘要
    def get_content(name) -> str         # 按需完整 body，XML 包裹 <skill name="...">
    def load_support_file(name, filename) -> str | None
```

- user skills 先加载、覆盖 bundled 同名
- 中途创建（mid-session）的 skill 也 disk-discovered

### 8.3 类别覆盖

| 类别 | 代表 |
|---|---|
| **技术分析** | candlestick, ichimoku, elliott-wave, harmonic, chanlun, smc（smart money concepts）|
| **策略** | factor-research, ml-strategy, multi-factor, pair-trading, hedging-strategy, cross-market-strategy, event-driven, technical-basic, statistical-arbitrage, sector-rotation, sentiment-analysis |
| **数据源** | tushare, akshare, yfinance, ccxt, okx-market, eastmoney, mootdx, qveris, sec-edgar, edgar-sec-filings, vnpy-export |
| **基本面** | fundamental-filter, earnings-forecast, earnings-revision, financial-statement, dividend-analysis, fund-analysis, valuation-model, credit-analysis, convertible-bond, etf-analysis, deep-company-series, management-deep-dive, corporate-events, private-company-research |
| **加密/链上** | crypto-derivatives, defi-yield, stablecoin-flow, token-unlock-treasury, liquidation-heatmap, perp-funding-basis, onchain-analysis |
| **资金流** | hk-connect-flow, northbound, block-trades, dragon-tiger, margin-trading, lockup-expiry, us-etf-flow |
| **宏观/政经** | global-macro, macro-analysis, geopolitical-risk, regulatory-knowledge |
| **期权 / 结构** | options-strategy, options-payoff, options-advanced, commodity-analysis |
| **执行 / 归因** | execution-model, performance-attribution, market-microstructure, trade-journal |
| **分钟级** | minute-analysis |
| **情报** | social-media-intelligence, web-reader, doc-reader |
| **辅助** | bottleneck-hunter, alpha-zoo, ashare-pre-st-filter, asset-allocation, backtest-diagnose, behavioral-finance, correlation-analysis, data-routing, report-generate, research-discipline, research-goal, risk-analysis, seasonal, shadow-account, skill-writer, strategy-generate, thesis-tracker, volatility, adr-hshare, pine-script, iwencai |

---

## 9. Swarm 多智能体（30 个 preset）

### 9.1 数据模型（`src/swarm/models.py`）

```python
class SwarmAgentSpec(BaseModel):
    id: str                  # e.g. "macro_analyst"
    role: str
    system_prompt: str       # 支持 {upstream_context}
    tools: list[str] = []
    skills: list[str] = []
    max_iterations: int = 25
    timeout_seconds: int = 300
    model_name: str | None = None
    max_retries: int = 2

class SwarmTask(BaseModel):
    id: str
    agent_id: str
    prompt_template: str     # 支持 {var}
    depends_on: list[str]    # 不可变
    blocked_by: list[str]    # 可变（dispatch 时缩减）
    input_from: dict[str, str]   # {context_key → upstream_task_id}
    status: TaskStatus
    summary: str
    artifacts: list[str]
    started_at / completed_at
    worker_iterations: int = 0

class SwarmRun(BaseModel):
    id: str                  # "swarm-{ts}-{8hex}"
    preset_name: str
    status: RunStatus
    user_vars: dict[str, str]
    agents: list[SwarmAgentSpec]
    tasks: list[SwarmTask]
    grounding_data: dict | None
    total_input/output_tokens
    provider / model         # 从 env 捕获
```

**状态枚举**：
- `TaskStatus`：pending / blocked / in_progress / completed / failed / cancelled
- `RunStatus`：pending / running / completed / failed / cancelled
- `WorkerStatus`：**completed / failed / timeout / token_limit / incomplete**（incomplete = 计划型 / 编造数字 / 未解析工具标记 / data agent 无工具调用）

### 9.2 Runtime（`src/swarm/runtime.py`）

- `start_run()`：
  1. `reap_stale_running_runs()`（按 per-run 阈值扫僵尸）
  2. `build_run_from_preset(preset_name, user_vars)`
  3. `validate_dag(run.tasks)`（DFS 环检测）
  4. `store.create_run(run)` + 原子写 run.json
  5. spawn daemon 线程 `swarm-{run.id}` 跑 `_execute_run`
- `_execute_run`：
  - Kahn 算法算 `topological_layers`，每层**并行**（`ThreadPoolExecutor` 默认 4 workers），层间串行
  - 每任务 `_run_worker_with_retries` 调 `run_worker()`（`src/swarm/worker.py`）
  - 每层 deadline = `max(timeout × (retries+1)) + 60s`
- 取消：per-run `threading.Event`；层间检查；`KeyboardInterrupt` 触发；`shutdown(wait=False, cancel_futures=True)`
- 重试：per-task `max_retries`；**其他终态不重试**（timeout / token_limit / incomplete 立即返回）
- 依赖闸：dispatch 前检查 `depends_on` 全部 `completed`，否则标 `blocked` 并发 `task_blocked` 事件

### 9.3 Grounding（`src/swarm/grounding.py`）

防止 worker 编造价格：

1. **符号抽取**（`extract_symbols_from_user_vars`）：
   - 后缀形：`*.US` / `*.HK` / `*.SZ` / `*.SH` / `*.BJ` / `*-USDT`
   - 裸 ticker（2-5 字母大写）→ 提升为 `{TICKER}.US`（去停用词：FED, GDP, USD, BTC, CEO…）
   - 已消耗后缀形优先；裸形 fallback
2. **预取**：用 `backtest.loaders.registry.resolve_loader(_detect_market(code))` 拉 30 天 1D K 线
3. **Worker 注入**：`format_grounding_block(grounding_data)` → system prompt 头部 "Ground Truth — Recent Market Data"（最近 5 根 + 窗口 min/max）
4. **节流**：`SWARM_GROUNDING_MAX_SYMBOLS` 默认 8

### 9.4 Worker（`src/swarm/worker.py`）

- **不**实例化 `AgentLoop`（模块 docstring 明确）
- 直接 `ChatLLM.stream_chat` + 手工迭代循环
- **过滤工具**：`build_swarm_registry(whitelist)` 裁剪（CLI 传 `include_shell_tools=True`）
- **Prompt 拼接顺序**：
  1. `## Role` = `agent_spec.role`
  2. `system_prompt`（`{upstream_context}` 替换为上游 summaries）
  3. `## Available Skills`（过滤后的 skills）
  4. `## Ground Truth — Recent Market Data`（非空时）
  5. `## Market Data Tool Policy`（若 `get_market_data` 在工具列表）
  6. **`## Data Citation Discipline (HARD RULE)`**：每个引用数字必须有 tool / ground truth / upstream context 出处
  7. `## Execution Rules`：plan / execute / summarize；20 次工具硬上限；必须 `write_file(path="report.md", ...)`；2 句最终 summary
  8. `## Current Date & Time`（UTC）
- **缺失变量兜底**：`_FallbackDict` 返回 `(determine the appropriate {var} based on the objective)`（让 LLM 推断而非报错）
- **`_classify_deliverable`**：data agent 必须有数据工具调用 + report.md 写入；否则 `incomplete`
- **Per-task 防御**：
  - `_KEEP_RECENT_TOOLS=3` microcompact
  - 每 iter 检查 timeout → `WorkerStatus.timeout`
  - `len(json.dumps(messages))//4 > 60_000` → `WorkerStatus.token_limit`
  - `int(max_iterations*0.8)` 后注入 wrap-up nudge
  - 最后一 iter `tool_defs=None` 强制文本
  - `ProviderStreamError(retryable=True)` 单次重试 + `_STREAM_RETRY_DELAY_S=1.0s`
  - `content_filter_triggered` 计数；累计 `MAX_CONSECUTIVE_CONTENT_FILTER_SKIPS` → `circuit_breaker` + failed

### 9.5 30 个 Preset 清单

| # | Preset | 描述（verbatim `description:`） |
|---|---|---|
| 1 | `commodity_research_team` | 供需并行深挖 → cycle strategist 综合为投资论点（DAG 工作流）|
| 2 | `convertible_bond_team` | 债底 + 权益期权 + 嵌入期权三维并行 → 综合为可转债投资策略 |
| 3 | `credit_research_team` | 信用质量 + 利率环境 + 行业信用三维并行 → fixed income strategist 综合 |
| 4 | `crypto_research_lab` | 链上数据 + DeFi 协议 + 市场情绪三维并行 → Alpha synthesizer 综合 |
| 5 | `crypto_trading_desk` | 执行向加密 desk：funding/basis + liquidation/microstructure + on-chain/flow + risk manager |
| 6 | `derivatives_strategy_desk` | Vol 分析 → 策略设计 → Greeks 风控：顺序期权 desk |
| 7 | `earnings_research_desk` | 财报深挖：fundamental + revision + options/event + earnings strategist |
| 8 | `equity_research_team` | Macro → sector → stock 三层深研 → research editor 综合 |
| 9 | `etf_allocation_desk` | ETF 筛选 + 宏观配置 + 风险预算并行 → portfolio optimizer 构造 + 回测 |
| 10 | `event_driven_task_force` | 事件扫描 → 影响深挖 → 策略构建：顺序事件驱动对冲基金深查 |
| 11 | `factor_research_committee` | 因子挖掘 + 因子验证并行 → 因子组合构建 → 回测评审：量价内部研究流 |
| 12 | `fund_selection_panel` | 多维量化筛选 → Brinson 业绩归因 + 风格分析 → FOF 组合权重优化 |
| 13 | `fundamental_research_team` | 财务 / 估值 / 质量三维并行 → research editor 综合为买入深度报告 |
| 14 | `geopolitical_war_room` | 地缘 + 能源冲击 + 供应链影响并行 → Chief Strategist 应急资产配置剧本 |
| 15 | `global_allocation_committee` | A 股 + 加密 + 港/美 并行 → allocator 综合 + 数据加权 + 场景 + 再平衡规则 |
| 16 | `global_equities_desk` | A 股 + 港/美 + 加密 analyst + global strategist：跨市场选股 + 套利 |
| 17 | `investment_committee` | 多空辩论 → 风险审查 → PM 最终拍板：买方投委会流 |
| 18 | `macro_rates_fx_desk` | 全球利率 + FX + 商品 / 通胀 + macro PM：央行 + 收益率曲线 + 货币 + 宏观配置 |
| 19 | `macro_strategy_forum` | 全球 + 国内 + 政策 三视角并行 → chief strategist 综合 |
| 20 | `ml_quant_lab` | 特征工程 + 模型设计并行 → backtest engineer 严格 OOS 验证 |
| 21 | `pairs_research_lab` | 相关扫描 + 协整检验并行 → pair strategist → 最终 microstructure 评审 |
| 22 | `portfolio_review_board` | 业绩归因 + 风险审查 + 执行质量并行 → CIO 综合为再平衡决策 |
| 23 | `quant_strategy_desk` | 选股 + 因子研究并行 → 策略回测 → 风险审计 |
| 24 | `risk_committee` | 回撤 + 尾风险 + 市场制度审查并行 → Head of Risk 签发 |
| 25 | `sector_rotation_team` | 经济周期 + 繁荣度 + 资金流并行 → rotation strategist 构建 + 回测 |
| 26 | `sentiment_intelligence_team` | 新闻 + 社交情绪 + 资金流并行 → sentiment signal synthesizer 复合成反转信号 |
| 27 | `social_alpha_team` | Twitter + Telegram + Reddit 并行 → Alpha synthesizer 提取社交情绪因子 |
| 28 | `statistical_arbitrage_desk` | pair 扫描 + microstructure 并行 → arbitrage strategist → 最终风险审查 |
| 29 | `technical_analysis_panel` | 经典 TA + 一目 + 谐波 + 艾略特 + SMC 并行 → signal aggregator 打分共振 |
| 30 | `value_investing_committee` | 巴菲特（护城河+价格）/ 芒格（逆向+风险）/ 段永平（好生意+可信管理层）/ 李录（10 年确定性 + 文明趋势 含人口/消费）四方辩论 → chair 综合 |

---

## 10. Goal / Hypothesis / Memory / Session

### 10.1 Goal（`src/goal/`，SQLite ledger）

**状态枚举**（`GoalStatus`）：active / paused / waiting_user / needs_refresh / insufficient_evidence / compliance_blocked / blocked / budget_limited / usage_limited / complete / cancelled / superseded

**模型**：
- `GoalRecord`：frozen，ID / session / status / objective / UI summary / source / protocol / `RiskTier` / token/turn/time budgets / usage / timestamps / completion_time / wrap_up / recap
- `GoalClaim`：identity / type / text / status / timestamps
- `GoalCriterion`：**required checklist text** + status (pending 默认) + freshness / protocol step / timestamps
- `EvidenceInput`：text + criterion/claim 链接 + tool/run/source provenance + universe/benchmark/timeframe/method/assumptions + artifact path/hash + data-as-of + confidence + caveat + **contradictions**
- `EvidenceRecord`：ID + retrieval_time + freshness + verification_status
- `AuditRow`：`(criterion_id, result, evidence_ids, notes)`；`result ∈ {satisfied, satisfied_with_caveat, not_applicable_user_accepted}`
- `RiskTier`：research_general / market_specific_short_term / personalized_advice_or_position_sizing / **LIVE_TRADING_OR_EXECUTION**（建 goal 时拒绝）
- `StaleGoalError`：stale-write 异常

**政策**：
- `normalize_required_text`：strip + 拒空
- 正则拒英文/中文 live order-execution 语言
- `GoalStore.replace_goal`：supersede 当前 session 的 active goal；自动创建 active thesis claim + pending protocol criteria
- `update_goal` 在 `expected_goal_id` 守卫下改 objective/summary
- `append_evidence`：校验链接行 + 验证本地 artifact / run ID + 标记 linked criterion covered
- `complete`：要求每个 required criterion 有 audit + verified evidence
- 取消 = `status=cancelled`；替换 = `superseded`（**无专门 cancel 方法**）

**存储**：SQLite WAL + foreign keys + busy timeout + `BEGIN IMMEDIATE` + `@_synchronized(threading.RLock)`；路径 `~/.vibe-trading/sessions.db`（与 FTS 共享）；override 通过 `VIBE_TRADING_GOAL_DB_PATH`

**Context 注入**：
- `format_goal_context(session_id)` → `(formatted_xml, goal_id)`
- `get_current_goal_context(session_id)` 在每轮 user message 顶部 pre-pend
- `format_goal_continuation_prompt`：包含 open criteria / claims / recent evidence / progress / prior answer / 指示
- 可继续状态：`active`, `needs_refresh`, `insufficient_evidence`

### 10.2 Hypothesis（`src/hypotheses/`，JSON 文件）

**状态**：exploring / testing / validated / rejected / monitoring

**模型**：
- `Hypothesis`：mutable；ID（`hyp_<12hex>` SHA-256）+ title + thesis + status + universe + signal + data sources + skills + `run_cards[]` + invalidation_notes + timestamps
- `update()`：接受任何允许 status，**无 transition / state-machine 强制**
- 搜索：token overlap across serialized fields + recency 排序
- IDs：SHA-256-derived `hyp_<12hex>` 带冲突后缀
- **无 delete 操作**

**存储**：`~/.vibe-trading/hypotheses.json` 或 `VIBE_TRADING_HYPOTHESES_PATH`；root = JSON list，按 created_at 排序；写用 temp + replace；malformed 抛 `ValueError`

**CLI**：
- `vibe-trading hypothesis list / show / invalidate`
- **没有** `add / propose / accept / reject`（但 agent tool 暴露完整 CRUD）

### 10.3 Persistent Memory（`src/memory/persistent.py`）

**存储布局**：
```
~/.vibe-trading/memory/
├── MEMORY.md                          # 索引，≤200 行
├── user_<slug>.md                     # type=user
├── feedback_<slug>.md
├── project_<slug>.md
└── reference_<slug>.md
```

**Frontmatter**：name / description / type（∈ user/feedback/project/reference）

**Prompt 缓存**：
- `snapshot` 在 session 启动时冻结 → 注入 system prompt
- 写磁盘后 snapshot **不**自动更新（保持 cache 稳定）
- `find_relevant(query, max_results=3)`（实测默认 5）召回 → pre-pend 到 `<recalled-memories>` 用户消息块，每条 ≤500 body chars
- 评分：title/description 命中 2.0×，body 命中 1×；按 score + recency 排序

**Tokenizer**：ASCII [a-z0-9]{3,} + 单个 CJK/泰/阿拉伯/希伯来/西里尔字符；下划线作边界

**净化**：剥 C0/C1 控制符 + DEL（保留 tab/newline）；body 截断 `MAX_ENTRY_CHARS=8000` 显式标记

### 10.4 Session（`src/session/`）

**双写**：JSONL + FTS5 SQLite（共享 `~/.vibe-trading/sessions.db`）

**模型**：
- `Session`：active/completed/archived status + title/config + timestamps + last attempt
- `Attempt`：pending/running/waiting_user/completed/failed/cancelled status + prompt + parent + run_dir + summary/error/metrics + **ReAct trace**
- `Message`：role/content/timestamps/attempt 链接/metadata

**`SessionService`**：4 worker ThreadPoolExecutor 后台执行 AgentLoop；per-session SSE 缓冲（500）+ 30s 心跳；`call_soon_threadsafe` 投递；last-event replay

**`SessionSearchIndex`**：incremental + bulk reindex；quoted OR 语法；返回 grouped session matches（title / start / count / rank / highlighted snippets）

**事件**（`events.py`）：thread-safe per-session SSE buffers（默认 500），subscriber queues，last-event replay，30s heartbeat

**WebUI helpers**：`goal_state.py`（normalizes metadata → `{active, active_goals, completed_goals}`）+ `webui_turns.py`（in-process chat_id → turn-start timestamp map）；**两者都不持久化**

**API 路径**：`agent/sessions/`（`SESSIONS_DIR = Path(__file__).parent.parent.parent / "sessions"`）

---

## 11. Scheduled Research

**模型**：
- `ScheduledResearchJob`：id + prompt + schedule + `next_run_at` (epoch ms) + `created_at` + optional `last_run_at` + config + `JobStatus`
- `JobStatus`：pending / running / completed / failed / cancelled

**Cron 解析**：
- 仅接受 5 字段 `min hour dom month dow` 或正毫秒间隔
- 字段仅支持裸数字 / `*` / `*/n`
- 边界：0–59 / 0–23 / 1–31 / 1–12 / 0–6
- **不支持**：6 字段（秒）/ 列表 / 范围 / 名称 / 时区
- DOM / Month / DOW 是 **AND** 语义

**`ScheduledResearchExecutor`**（`src/scheduled_research/executor.py`）：
- asyncio 守护线程，默认 60s tick（可注入 clock）
- 启动时 `recover_stale_running()` 把 running 改回 pending
- 顺序 dispatch due job
- `dispatch(job)` 是用户 `Callable[[ScheduledResearchJob], None]`
- 异常 → attempt failed；成功 callback → completed；周期性 job 给新 `next_run_at`
- `re_reads` records 前后 + `created_at` 对比，避免并发 delete/replace 复活

**存储**：
- JSON envelope `{schema_version:1, jobs:[…]}` at `~/.vibe-trading/scheduled_research/scheduled_research_jobs.json`
- 原子写：temp file → fsync → replace → parent dir fsync
- 缺失 = 空；corruption 重命名为 `.corrupt-<UTC timestamp>` 并抛 `CorruptStoreError`

---

## 12. Backtest 子系统

### 12.1 入口（`backtest/runner.py`）

- CLI：`python -m backtest.runner <run_dir>` → `main(Path(sys.argv[1]))`
- 安全：
  - `safe_run_dir` 校验（`VIBE_TRADING_ALLOWED_RUN_ROOTS` 白名单）
  - AST scrub `code/signal_engine.py`（禁顶层语句、`from signal_engine import …`、装饰器、unsafe default/annotation）
  - SignalEngine `__init__()` 无必填参数
  - 必须有 `generate(data_map) -> Dict[str, pd.Series]`
- **`BacktestConfigSchema`**（Pydantic，`extra="allow"`）：
  - `codes: List[str]`（非空、无 blank）
  - `start_date`, `end_date`
  - `source: str = "tushare"`
  - `interval: str = "1D"` ∈ `{1m,5m,15m,30m,1H,4H,1D}`
  - `engine: str = "daily"` ∈ `{daily, options}`
  - `fundamental_fields: Optional[Dict[str, List[str]]]`
  - `event_feeds: Optional[List[Dict[str, Any]]]`
- **流程**：
  1. parse/validate config → load SignalEngine
  2. fetch（`source="auto"` → `_fetch_auto()` per-market；单 source → loader，带 fallback chain walk）
  3. `_sanitize_data_map()` → `validate_ohlc()` drop-invariance check
  4. `_maybe_inject_fundamentals_for_factor_panel()`（选 `fund:*` 列）
  5. 选 engine（cross-market → `CompositeEngine`；其余按 `_create_market_engine()` 路由）
  6. 跑 `run_options_backtest(...)` 或 `market_engine.run_backtest(...)`
- **产物**（在 `run_dir/artifacts/`）：
  - `ohlcv_<code>.csv` per symbol
  - `equity.csv`：`ret / equity / drawdown / benchmark_equity / active_ret`
  - `positions.csv`：target weights matrix
  - `trades.csv`：`timestamp / code / side / price / qty / reason / pnl / holding_days / return_pct`
  - `metrics.csv`：scalar metrics flattened
  - `validation.json`：仅当 `config["validation"]`
  - `run_card.json` + `run_card.md`（Trust Layer，SHA-256 of config + signal_engine.py）

### 12.2 BaseEngine（`backtest/engines/base.py`）

**抽象方法**（子类必须实现）：
```python
def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool
def round_size(self, raw_size: float, price: float) -> float
def calc_commission(self, size, price, direction, is_open) -> float
def apply_slippage(self, price: float, direction: int) -> float
```

**可选钩**：
- `on_bar(symbol, bar, timestamp)`：默认 no-op；crypto（funding + liquidation）/ forex（swap）/ composite（调度）

**`run_backtest(config, loader, signal_engine, run_dir, bars_per_year=252)` 流程**：
1. `loader.fetch(...)` → enrich fundamentals → enrich events
2. `signal_engine.generate(data_map)` → validate 每值是 `pd.Series`
3. `_load_optimizer(config)` + `_align(...)` → 对齐日期、close matrix、shift+normalize target matrix
4. `_execute_bars(...)` → bar-by-bar 循环
5. 通过 `benchmark.resolve_benchmark(...)` 算 external benchmark（如 `config["benchmark"]` set 且 ≠ "auto"）
6. `calc_metrics(..., bench_ret=...)` → 加 `by_symbol`, `by_exit_reason`, `validation`
7. `run_validation(...)` → 写 `artifacts/validation.json`
8. `_write_artifacts(...)`（CSVs）
9. `write_run_card(...)`（Trust Layer）

**`_execute_bars`**：
- 每 bar `ts`：
  - **(a)** `on_bar(c, bar, ts)` → 资金费 / 强平 / swap
  - **(b)** 重算 equity → rebalance 每个 symbol 到 `target_pos.at[ts, c]`
  - **(c)** 记录 `EquitySnapshot`
- **(d)** 强制 EOD 平仓（last bar safe price + `"end_of_backtest"` reason）

**Rebalance 逻辑**：
- `target_dir = sign(target_weight)`
- 若持仓：若 `target_dir == 0` 或方向不同 → close（gate `can_execute(symbol, 0, bar)`）
- 若未持仓且 `target_dir != 0` → open（gate `can_execute(symbol, target_dir, bar)`）
- execution price = `apply_slippage(bar.open, target_dir)`
- sizing = `raw_size = _calc_raw_size(...)` → `size = round_size(...)`
- **margin check**：若 `margin + commission > self.capital`，recompute size from `(capital - comm) * leverage`；abort 若仍 infeasible
- `self.capital -= (margin + comm)`；记录 `Position`

**`_close_position`**：pop position → pnl/pnl_pct via `_calc_pnl/_calc_margin` → return `margin + pnl - exit_comm` to capital → 追加 `TradeRecord(exit_reason ∈ {signal, liquidation, end_of_backtest})`

### 12.3 引擎清单（10 个）

| 引擎 | 市场 | 关键规则 |
|---|---|---|
| **ChinaAEngine** | A 股 | T+1, 禁融券（默认）, 限价 ±10/20/30% (主板/创业/科创/北交所), 100 股手（碎股仅卖）, 佣金 0.025%（¥5 min）+ 转让费 0.001% 双边 + 印花税 0.05% 卖出 |
| **ChinaFuturesEngine** | 中期 | T+0 双向, 合约乘数表（IF=300/IC=200/rb=10/au=1000/T=10000/...）, 保证金率表, 涨跌停 ±10%（债 ±2%）, per-product 佣金 |
| **GlobalEquityEngine** | 美/港 | US：T+0, 零佣金, 碎股（round 0.01）, 滑点 0.0005; HK：T+0, 100 股手, 经纪 0.015% + 印花 0.1% 双边 + SFC+FRC 0.00565% + CCASS 0.002%, 滑点 0.001 |
| **GlobalFuturesEngine** | 全球期货 | CME/CBOT/NYMEX/COMEX/ICE/Eurex；合约乘数 ES=50/NQ=20/CL=1000/GC=100/6E=125000；USD 佣金表（ES=2.25/MES=0.62）；±7% 股指限价 |
| **IndiaEquityEngine** | 印度 | NSE/BSE T+1（当 bar 卖被 block）, 默认禁空, circuit band 0.20, 1 股手, 全成本栈（brokerage + STT 0.1% 双边 + exchange 0.00297% + SEBI 0.0001% + stamp 0.015% 买 + GST 18% + DP） |
| **CryptoEngine** | 加密 | 24/7 双向, 6 位小数碎位, maker 0.02% / taker 0.05%, 资金费每 8h（0/8/16）, 强平当 `margin + unrealized ≤ notional × maint_rate`（OKX tiered） |
| **ForexEngine** | 外汇 | 24×5, 100× 杠杆默认, 标准手 100k（round 1000 单位）, 点差表 + 0.3 pip 滑点, swap 日 close（周三 ×3 rollover） |
| **CompositeEngine** | 跨市场 | 共享资金池；子引擎作无状态 rule book；自行处理 T+1（sub-engine 无 shared positions view） |
| **OptionsEngine** | 期权 | BS 价格 + Greeks, 美式提前行权启发式, IV smile, 多腿; 信号接口返 list[trade instructions]; 输出 greeks.csv |
| **FuturesBaseEngine** | 期货基类 | 抽象 `get_contract_multiplier(symbol)`，封装合约乘数 |

### 12.4 DataLoaderProtocol（`backtest/loaders/base.py`）

```python
@runtime_checkable
class DataLoaderProtocol(Protocol):
    name: str
    markets: set[str]
    requires_auth: bool
    def is_available(self) -> bool
    def fetch(self, codes, start_date, end_date, *,
              interval="1D", fields=None) -> dict[str, pd.DataFrame]
```

**工具**：
- `validate_date_range(start, end)`
- `validate_ohlc(frame, *, strategy="drop")`：flag `high<low | high<open | high<close | low>open | low>close | open<=0 | ...`
- `check_budget(deadline, label)` / `retry_with_budget(fn, *, transient, deadline, label, max_retries=3)`
- **缓存**：`cached_loader_fetch(source, symbol, timeframe, start, end, fields, fetch)`
  - env `VIBE_TRADING_DATA_CACHE` / `VIBE_TRADING_DATA_CACHE_ROOT`（默认 `~/.vibe-trading/cache/loaders/`）
  - SHA-256 of canonical JSON（v2）key
  - DuckDB parquet + sidecar JSON（index names/dtypes, columns）
  - 原子 rename（pid+uuid tmp）
  - `loader_cache_range_is_final(end_date)`：仅 fully-elapsed days 可缓存
  - silent failure → fall back to live provider

### 12.5 Loader Registry + Fallback Chain（`backtest/loaders/registry.py`）

**`LOADER_REGISTRY`**：populated by `@register` 装饰器
**`VALID_SOURCES`**（单源真理）：`{tushare, okx, yfinance, akshare, baostock, tencent, mootdx, ccxt, futu, eastmoney, sina, stooq, yahoo, finnhub, alphavantage, tiingo, fmp, qveris, india_broker, local, auto}`

**`FALLBACK_CHAINS`**（按 IP-ban 风险 → 数据质量排序）：
```python
{
  "a_share":     ["tencent","mootdx","eastmoney","baostock","akshare","tushare","local"],
  "us_equity":   ["yahoo","stooq","sina","eastmoney","yfinance","tiingo","fmp","finnhub","alphavantage","akshare","local"],
  "hk_equity":   ["eastmoney","yahoo","futu","yfinance","akshare","local"],
  "india_equity":["yahoo","yfinance","india_broker","local"],
  "crypto":      ["okx","ccxt","yfinance","local"],
  "futures":     ["tushare","akshare","local"],
  "fund":        ["tushare","akshare","local"],
  "macro":       ["akshare","tushare","local"],
  "forex":       ["akshare","yfinance","local"],
}
```

**`_NO_NETWORK_FALLBACK_SOURCES: frozenset = {"local", "qveris"}`**（显式选这俩失败时**不**静默降级到大网）

**`resolve_loader(market)`**：`_ensure_registered()` → 走 chain → 第一个 `is_available()` 返 True 的实例

**`get_loader_cls_with_fallback(source)`**：返回 class；不可用且不在 `_NO_NETWORK_FALLBACK_SOURCES` 时 walk 每个 `loader_cls.markets` → `resolve_loader(market)`

**`_symbol_utils`**：`_ETF_PREFIXES = {"15","16","50","51","52","56","58"}`；`_is_etf_listed(code)`

### 12.6 Loader 清单（21 个）

| Loader | Markets | Auth |
|---|---|---|
| `akshare_loader` | a_share/us_equity/hk_equity/futures/fund/macro/forex | – |
| `alphavantage_loader` | us_equity | ✓ |
| `baostock_loader` | a_share | – |
| `ccxt_loader` | crypto | – |
| `eastmoney_loader` | a_share/hk_equity/us_equity | – |
| `eastmoney_client` | 底层 HTTP 客户端（resolve_secid/fetch_kline/HostThrottle）| – |
| `finnhub_loader` | us_equity | ✓ |
| `fmp_loader` | us_equity | ✓ |
| `futu.py` | hk_equity/a_share | ✓ |
| `fundamentals_loader` | 不注册为 market loader；aggregator（`tushare_fundamentals`）| ✓ |
| `india_broker_loader` | india_equity | ✓ |
| `local_loader` | **all 8 markets** | –（**显式选择失败大声报错**） |
| `mootdx_loader` | a_share | – |
| `okx.py` | crypto | – |
| `qveris_loader` | us/hk/a/crypto/forex/fund/macro | ✓（**显式选择失败大声报错**） |
| `rsshub_events` | EventProviderError / FeedSpec / RSSHubEventProvider；enrich price frames with event_score | – |
| `sec_edgar_client` | SEC EDGAR REST（CIK / submissions / company_facts） | – |
| `sina_loader` | us_equity | – |
| `stooq_loader` | us_equity | – |
| `tencent_loader` | a_share | – |
| `tiingo_loader` | us_equity | ✓ |
| `tushare.py` | a_share/futures/fund | ✓ |
| `tushare_fundamentals` | TushareFundamentalProvider（balancesheet/cashflow/fina_indicator/income）| ✓ |
| `yahoo_client` | `_CrumbStore` crumb/cookie manager | – |
| `yahoo_loader` | us_equity/hk_equity/india_equity | – |
| `yfinance_loader` | us_equity/hk_equity/india_equity/crypto | – |
| `_http.py` | HostThrottle / throttled_get / _session_for | – |
| `_fundamental_schema.py` | `fund:<field>` → RawFieldSpec / DerivedFieldSpec | – |

### 12.7 Optimizers（5 个 + Base）

**`BaseOptimizer`**：
```python
class BaseOptimizer(ABC):
    def __init__(lookback=60, **kwargs)
    def optimize(ret, pos, dates) -> pos      # 循环 dates → 滚动窗口 → _build_context → _calc_weights → 应用（保留信号 sign）
    def _build_context(window, active)         # 默认：covariance
    @abstractmethod
    def _calc_weights(ctx) -> np.ndarray
    @staticmethod
    def _normalize(w)                          # 非负，sums to 1
    @staticmethod
    def _equal_weight(n)
```

| Optimizer | 算法 |
|---|---|
| `EqualVolatilityOptimizer` | inverse-vol weights |
| `MaxDiversificationOptimizer` | SLSQP maximize `(w'σ) / sqrt(w'Σw)`；fallback equal weight on failure |
| `MeanVarianceOptimizer` | SLSQP max-Sharpe over long-only simplex |
| `RiskParityOptimizer` | Spinu 2013：inverse-vol seed + 5 轮迭代 `w_i ← w_i × (port_vol/n) / (w_i × MRC_i)` |
| `TurnoverAwareOptimizer` | SLSQP minimize `-w'μ + λ w'Σw + γ ‖w − w_prev‖₁`；track `realized_turnover` |

**选择**：config["optimizer"] 字符串（importlib `backtest.optimizers.<name>`）；extras via `config["optimizer_params"]`

### 12.8 Metrics（`backtest/metrics.py`）

**`calc_bars_per_year(interval, source)`**：
- `_TRADING_DAYS`：tushare/yfinance/akshare/mootdx/futu = 252；okx/ccxt = 365
- `_BARS_PER_DAY`：per (interval, source) — e.g. 1m tushare=240, okx=1440, yfinance=390, akshare=240, ccxt=1440

**`win_rate_and_stats(trades)`**：win_rate / profit_loss_ratio / max_consecutive_loss / avg_holding_bars / profit_factor
**`by_symbol_stats(trades)` / `by_exit_reason_stats(trades)`**

**`calc_metrics(equity_curve, trades, initial_cash, bars_per_year, bench_ret=None)`** 返回：
- `final_value, total_return, annual_return, max_drawdown, sharpe, calmar, sortino`
- `win_rate, profit_loss_ratio, profit_factor, max_consecutive_loss, avg_holding_days, trade_count`
- `benchmark_return, excess_return, information_ratio`（bench_ret 提供时）
- 跨市场（>1 种 market_types）→ `bars_per_year=None` → 日历日化 `(last-first).days / 365.25`

### 12.9 Validation（`backtest/validation.py`）

3 道独立闸（任意子集 via `config["validation"]`）：

| 工具 | 输入 | 输出 |
|---|---|---|
| `monte_carlo_test(trades, initial_capital, n_simulations=1000, seed=42)` | 交易 PnL | `actual_sharpe, p_value_sharpe, actual_max_dd, p_value_max_dd, simulated_sharpe_mean/std/p5/p95, n_simulations, n_trades` |
| `bootstrap_sharpe_ci(equity_curve, n_bootstrap=1000, confidence=0.95, bpy=252, seed=42)` | equity curve | `observed_sharpe, ci_lower, ci_upper, median_sharpe, prob_positive` |
| `walk_forward_analysis(equity_curve, trades, n_windows=5, bpy=252)` | 两者 | per-window return/sharpe/max_dd/trades/win_rate；`profitable_windows, consistency_rate, return_mean/std, sharpe_mean/std` |

**集成**：`run_validation(config, ...)` 读 `config["validation"].{monte_carlo,bootstrap,walk_forward}`（each True or dict override）

**CLI**：`python -m backtest.validation <run_dir>` 读 `config.json` + `artifacts/equity.csv` + `artifacts/trades.csv` 跑全部，写 `validation.json` + 打印 JSON

### 12.10 Run Card（`backtest/run_card.py`，Trust Layer）

```python
SCHEMA_VERSION = "0.1"
BACKTEST_SUMMARY_KEYS = ("codes", "start_date", "end_date", "interval", "engine", "initial_cash", "source")

def write_run_card(run_dir, config, metrics, *,
                   data_sources=None, strategy_path=None,
                   warnings=None, artifact_refs=None) -> dict
```

**字段**：
- `schema_version, generated_at (UTC ISO-8601 Z), run_dir`
- `backtest`：filtered summary of config
- `reproducibility`：config_hash (SHA-256) + strategy_hash (SHA-256)
- `data_sources`：effective source names
- `metrics`：scalar only（`_is_scalar` 过滤 dict；`validation` 单独存）
- `warnings`：list（`content_filter_warnings`）
- `artifacts`：list of `{path, size_bytes, sha256}`（含 config.json / signal_engine.py / artifacts/ 下每个文件，1 MiB 流式 hash）
- `artifact_refs`：IRR-AGL refs
- `validation`：verbatim（`metrics["validation"]` 存在时）

**输出**：`run_card.json`（sorted keys, `allow_nan=False`, `ensure_ascii=False`, `default=str`） + `run_card.md`

**JSON 安全**：`_json_safe` 递归 NaN/inf → None

### 12.11 Correlation（`backtest/correlation.py`）

- `infer_market(code)`：启发式市场键（USDT/BTC/ETH/... → crypto；`.HK` 检查 leading-zero；6开头/000/001/002 → a_share；0/399 → a_share；0/1/2/3/4 开头 → hk_equity；else us_equity）
- `_rolling_correlation_matrix(price_series, window, method ∈ {pearson,spearman})`：inner-join returns DataFrame，trailing `window` rows，NaN pairs fill 0.0，pairwise `np.corrcoef` 或 `scipy.stats.spearmanr`
- `compute_correlation_matrix(codes, days=90, method="pearson")`：top-level `(now − days − 60, now)`；遍历 codes → `infer_market` → `resolve_loader(market)`（resolve fail 时 yfinance fallback）→ fetch `interval="1D"`；<2 assets → raise；返回 `{labels, matrix, window, method}`

### 12.12 Benchmark（`backtest/benchmark.py`）

```python
MARKET_BENCHMARKS = {
    "us_equity": "SPY",
    "hk_equity": "HK.03100",
    "a_share":   "000300.SH",
    "crypto":    "BTC-USDT",
    "futures":   "ES.CME",
    "forex":     None,
}
```

`resolve_benchmark(strategy_codes, source, start_date, end_date, interval="1D", explicit=None)`：
- 选 ticker（`explicit` override > `_resolve_ticker(...)`）
- 用 `YfinanceLoader().fetch(...)`（无 auth）
- `ret_series = close.pct_change().fillna(0.0)`；`total_ret = (1+ret_series).prod() - 1`
- fetch fail / empty → return None

---

## 13. Alpha Zoo（460 个因子）

### 13.1 注册机制

- **无装饰器**：AST 解析 `__alpha_meta__` 字面量 + 文件名扫
- 进程级单例：`get_default_registry()`
- 文件 ≤200 KB；id 正则 `^[a-z][a-z0-9]+_[a-z0-9_]+$`
- `compute()`：lazy import module → run `compute(panel)` → validate shape，no `±inf`，≤95% NaN

### 13.2 `__alpha_meta__` Schema（`AlphaMeta`，frozen，extra="forbid"）

- 必填：`id, theme[], formula_latex, columns_required[], universe[], frequency[], decay_horizon (0-512), min_warmup_bars`
- 可选：`nickname, extras_required[], requires_sector, notes`
- Columns 必须在 `_PRICE_COLS` (open/high/low/close/volume/vwap/amount) 或 `fund:*`-prefixed

### 13.3 Zoo 清单

| Zoo | 数量 | 来源 / License |
|---|---|---|
| `alpha101/` | 101 | Kakushadze (2015) "101 Formulaic Alphas", arXiv:1601.00991 |
| `gtja191/` | 191 | 国泰君安证券 2014 "191 个短周期交易型 alpha 因子" |
| `qlib158/` | 154 (.py) | Microsoft Qlib Alpha158 clean-room 移植，Apache-2.0，pin commit `d5379c52` |
| `academic/` | 10 | Fama-French (1993/2015), Carhart (1997), Jegadeesh (1990), George-Hwang (2004), Amihud (2002), Harvey-Siddique (2000) |
| `fundamental/` | 4 | ROE / earnings yield / gross profitability / asset growth（PIT over `fund:*`） |

**Academic 文件清单**：`smb.py, hml.py, carhart_mom.py, cma.py, high52w.py, illiq.py, mkt_rf.py, retskew.py, rmw.py, strev.py`

### 13.4 因子分析核心（`factor_analysis_core.py`）

```python
def compute_ic_series(factor, returns) -> DataFrame
    # 每日 Spearman rank IC = rank().corrwith()
    # drop dates with <5 paired non-NaN cells

def compute_group_equity(factor, returns, n_groups) -> DataFrame
    # 分层回测：日频截面分位数 → 等权组收益 → 累计 NAV（Group_1..Group_N）
```

### 13.5 Bench Runner（非严格）

- `alive = ic_mean>0.02 ∧ ic_positive_ratio≥0.55 ∧ |t|>2`
- `reversed = ic_mean<-0.02 ∧ |t|>2`
- else `dead`

### 13.6 Bench Runner Strict

- **强制 random control**：row-shuffle within date（NaN/inf pinned），N seeds（默认 5），inner-join on common dates → `random_ic_mean`；paired alpha = `signal_ic - random_ic`；`alpha_t_full = t_stat(paired_alpha)`
- **OOS split**（`oos_split: YYYY-MM-DD`，train ≤ split < test，boundary exclusive）：加 `alpha_t_train, alpha_t_test`
- **Strict gate**：
  - `confirmed_alive`：`alpha_t_full ≥ thr` ∧ (no OOS ∨ `alpha_t_test ≥ thr`)
  - `reversed_strict`：`alpha_t_full ≤ -thr` 或 OOS sign-flip
  - `train_only`：full pass 但 OOS 在 noise band
  - `noise`：其余
- `StrictThresholds(alpha_t_threshold=2.0, min_ic_count=30)`；Harvey-Liu-Zhu 多测试校正抬到 3.5
- **防泄漏**：`base.py` 中 causal `delta(df, d≥1)` lookahead ban；`ref` of `-n` 不存在；row-shuffle 毁 cross-section 保分布；OOS boundary 独占

### 13.7 CLI `vibe-trading alpha …`

| 命令 | 行为 |
|---|---|
| `alpha list [--zoo --theme --universe --limit --json --include-load-errors]` | 注册表 dump |
| `alpha show <id> [--brief]` | 元数据 + syntax-highlighted source |
| `alpha bench [--zoo --universe --period --top --yes]` | `bench_runner.run_bench`；多 zoo 聚合（无 `--zoo` 时交互 y/N guard）；HTML 报告 |
| `alpha compare <id…> [--all --zoo --sort]` | `compare_runner.compare_alphas`；JSON envelope + ranking table |
| `alpha export-manifest --out PATH [--force]` | `Registry.export_manifest()` JSON；refuse paths outside repo root 除非 `--force` |

---

## 14. Shadow Account

**目的**：从用户 CSV/Excel 交易日志 + 盈利 FIFO roundtrips 派生 **research-only** 镜像策略。

### 14.1 模型

- `ShadowProfile`：journal hash + markets + date range + portrait + holding stats + rules
- `ShadowRule`：entry/exit conditions + holding range + support/coverage + samples + weight
- `ShadowBacktestResult`：per-market/combined metrics + equity curves + attribution + shadow PnL + realized PnL + delta
- `AttributionBreakdown`：missed-signal + noise-trade + early-exit + late-exit + overtrading + counterfactual-trade（signed）

### 14.2 Extractor（`extractor.py`）

- 委托 `trade_journal_parsers` 解析 CSV/Excel
- FIFO roundtrip 配对（≥5 盈利回合）
- 特征工程：holding / PnL / time / market + 可选 causal RSI14（as-of 买入日）+ prior-5-day return
- 聚类 → 派生规则；单规则 fallback

### 14.3 Backtester（`backtester.py`）

- `SUPPORTED_MARKETS = ("china_a", "hk", "us", "crypto")`
- `select_multi_market_codes()`：按 capped prefix 选已知流动性 basket
- `run_shadow_backtest(shadow_id, journal_path)`：生成 code → 调 backtest tool → 解析 metrics/equity → 算 attribution → cache `shadow_result.json`
- v1 per-market metrics 复用 combined metrics（runner 只发一个 combined metrics 文件）
- 缺日志 / artifacts → 0 / 空 attribution（**不**编造）

### 14.4 Codegen（`codegen.py`）

- Jinja2 加载 `src/shadow_account/templates/signal_engine.py.j2`
- `render_config(...)`, `render_signal_engine(...)`, `write_run_dir(...)`, `validate_generated(...)`
- 输出真实 `code/signal_engine.py` + `config.json`

### 14.5 Reporter（`reporter.py`）

- HTML always；PDF via WeasyPrint（optional）；可选 matplotlib charts；HTML-only fallback
- 模板：`src/shadow_account/templates/shadow_report.html` + `shadow_report.css`
- **8 节报告**：
  1. Shadow profile
  2. Shadow rules
  3. Combined backtest
  4. Backtest by market
  5. Delta attribution
  6. Counterfactual top-5
  7. Today's signal scan
  8. Confidence / caveats
- 字体：`fonts.py` cache / system-resolve / 下载 Noto CJK 到 `~/.vibe-trading/fonts/`；注册 matplotlib / CSS fonts；fallback DejaVu Sans

### 14.6 Scanner（`scanner.py`）

- `scan_shadow_signals`：评估规则 vs injected / fetched OHLCV through target date
- 返回 research-only signal candidates（**无** orders）

### 14.7 存储布局

```
~/.vibe-trading/
├── shadow_accounts/{shadow_id}.json       # profile
├── shadow_runs/{shadow_id}/                # backtest 工作目录
└── shadow_reports/{shadow_id}.html|.pdf    # 报告 + assets
```

---

## 15. Live Trading 层

> **真实 broker 下单**（Robinhood via MCP + 9 个直连 SDK brokers），gate by user-committed mandate + kill switch + full audit。

### 15.1 3 层分类（`src/live/classification.py`）

```python
class ToolClass(Enum):
    READ = "READ"
    WRITE = "WRITE"
    UNKNOWN = "UNKNOWN"
```

Ladder：
- Tier 1：MCP `annotations.readOnlyHint`（缺省 ≠ READ）
- Tier 2：curated per-broker map（**权威**，压过 annotations）
- Tier 3：default-deny → UNKNOWN（**=WRITE 处理，永不未门**）

### 15.2 Order Guard（`order_guard.py`）

`LiveOrderGuardTool`（subclass `MCPRemoteTool`）**仅**包裹 WRITE / UNKNOWN 远程工具（READ 保持 plain）。每次 `execute()`：
1. 加载 mandate + schema-version match
2. check `expires_at`
3. `halt_flag_set`（fail-closed）
4. 提取 `OrderIntent` via broker extractor
5. normalize notional = max(explicit, qty × quote)
6. 通过未包装的 READ broker MCP 工具读 positions/balance
7. `check_mandate` → **ALLOW** / **DENY**（structural）/ **PAUSE_FOR_REAUTH**（quantitative）
8. daily count 仅在 ALLOW + forwarded result 非错时递增
9. 每决定写一个 `LiveActionEvent`（frozen key `live_action` for SSE）
- `repeatable = False`

### 15.3 Halt 旗标（`halt.py`）

**文件系统 sentinel**（不是 in-process state）：
- `<runtime_root>/live/HALT`（全局）
- 可选 `<runtime_root>/live/<broker>/HALT`（per-broker）
- 全局赢 → 全部停
- 存在即生效（**即便 agent wedged**）；fail-closed
- `register_halt_action` / `on_halt_action` 钩子：runner 跑 preemptive cancel-and-flatten sweep

### 15.4 Mandate（`src/live/mandate/`）

```python
@dataclass(frozen=True)
class HardCaps:
    funding: ...
    max_order_notional: ...
    total_exposure: ...
    leverage: ...
    instruments: ...
    daily_cap: ...

@dataclass(frozen=True)
class UniverseConstraint:
    asset_classes: ...
    market_cap_floor: ...
    adv_floor: ...
    exclude: ...

@dataclass(frozen=True)
class ConsentMeta:
    provenance: ...
    expires_at: ...

@dataclass(frozen=True)
class Mandate:
    contract: ...               # immutable
    flatten_on_halt: bool = ...
```

- **唯一 writer**：`commit.py` 由 `POST /mandate/commit` 调用（**不**经 agent/tool registry）
- 需 `consent_ack=True`
- 原子写 mandate + consent record
- one-shot-invalidate proposal（default 30-day lifetime）
- 路径 `<runtime_root>/live/<broker>/mandate.json`（0600）
- **no write surface reachable from any tool**

### 15.5 Advisory（`src/live/advisory/`）

- **read-only pre-trade advisory**（不拦截）
- `Verdict ∈ {APPROVE, APPROVE_WITH_CONCERNS, REJECT, REVIEW_UNAVAILABLE}`
- `AdvisoryOrchestrator`：worst-case aggregation；fail-open on provider exception
- 默认 OFF（`VIBE_TRADING_ENABLE_ADVISORY`）
- 仅嵌入 gate 的 audit
- `mock.py` for tests（verdict injection, delay, forced failure）

**与 Mandate 区别**：
- Mandate = 强制合同（DENY / PAUSE）
- Advisory = 纯风险意见（观察、fail-open、audit-only）

### 15.6 Extractors（`src/live/extractors/`）

- `BROKER_EXTRACTORS: dict[broker → extract_order_intent(remote_name, kwargs) -> OrderIntent | None]`
- **仅** `robinhood` 注册（`src.trading.connectors.robinhood.extractor`）
- 返回 None → DENY
- Brokers 无 extractor → fail-closed

### 15.7 Runtime（`src/live/runtime/`）

- `scheduler.py`：wall-clock async scheduler（cron + 时钟漂移防护）
- `jobstore.py`：durable atomic `jobs.json`（corruption quarantine）
- `liveness.py`：heartbeat 文件；stale（>90s）→ dead
- `triggers.py`：market session + interval + event triggers（regular-hours / 24/7 crypto）
- `reconcile.py`：pre-tick broker-truth diff vs. last-known state；分类 `matched / unknown_fill / orphan_order / mid_order_ambiguous`；**模糊 → requires_halt=True，永不自动重发**
- `flatten.py`：preemptive kill-switch action（cancel resting → close positions）
- `runner.py`：persistent loop（halt → proactive expiry → reconcile → autonomous turn with mandate pinned → audit）；**no-retry on mutating calls**；resume 时重算

### 15.8 Audit（`audit.py`）

- append-only `audit.jsonl`（0600 dir）
- **`LiveActionEvent` kinds**：order_placed / order_cancelled / order_rejected / mandate_committed / breach / halt_tripped / halt_cleared
- 三 sink：ledger（always）+ per-run `TraceWriter`（optional）+ SSE `event_callback("live.action", record)`（optional）
- broker req/resp 走 `redact_payload` 后再写
- `mandate_snapshot_ref + consent_record_ref` 形成 user-click accountability chain

### 15.9 Daily Count（`daily_count.py`）

- `trade_counter.json`（atomic），per-broker UTC calendar-day counter
- advisory：broker 强制真 ceiling；读失败 = `0`（fail-open 仅在 count 上）

### 15.10 Connectors（`src/trading/connectors/`，**11 个 broker**）

`alpaca / binance / dhan / futu / ibkr / longbridge / okx / robinhood / shoonya / tiger / trading212`

每个 connector 子包：
- `__init__.py`
- `profiles.py`（built-in `TradingProfile`）
- `classification.py`（Tier-2 curated map；如 `ROBINHOOD_TOOL_CLASS`, `IBKR_TOOL_CLASS`, `OKX_TOOL_CLASS`）
- 直连 SDK：含 `sdk.py`（alpaca/binance/dhan/futu/longbridge/okx/shoonya/tiger/trading212）
- **IBKR**：`ibkr/local.py`（local_tws 模式无 SDK，OAuth 后才能发现 MCP catalog）
- **Robinhood**：`robinhood/mcp.py` + `robinhood/extractor.py`（**唯一**有 extractor 的 broker）

### 15.11 Trading Service（`src/trading/service.py`）

**框架核心**（connectors 与 Agent 间）：

```python
TradingProfile:
    environment: paper | live
    transport: local_tws | remote_mcp | broker_sdk
    capabilities: account.read / positions.read / orders.read / quotes.read / history.read
```

路由：`profile.transport ∈ {local_tws, broker_sdk, remote_mcp}`

- `check_connection / get_account / get_positions / get_open_orders / get_quote / get_history` 公开
- `place_order`：gate live `broker_sdk` profiles via `execute_live_order`（`sdk_order_gate.py`）；Robinhood 走 MCP gate；IBKR 保持 read-only
- `_order_classification`：推导 `(InstrumentType, AssetClass)` 给 gate；`.HK/.SH/.SZ` token inference

---

## 16. Channels（14 IM + Email + WebSocket）

### 16.1 BaseChannel ABC（`src/channels/base.py`，237 行）

**类属性**：
- `name="base"`, `display_name="Base"`, `send_progress=True`, `send_tool_hints=False`, `show_reasoning=True`

**抽象方法**：`async start()`, `async stop()`, `async send(msg: OutboundMessage)`
**可选**：`login(force=False)` 默认 no-op（QR 类如 weixin 覆写）

**Streaming hooks**（no-op defaults）：`send_delta()`, `send_reasoning_delta()`, `send_reasoning_end()`, `send_file_edit_events()`
- `send_reasoning()` = delta + end 组合
- `_stream_delta` = chunk；`_stream_end` = segment close；stateful impl 必须按 `_stream_id` key buffer

**`is_allowed(sender_id)` 逻辑**：
1. `"*"` 在 `allow_from`（也 `allowFrom` / camel）→ True
2. else `sender_id` in allowlist → True
3. else `is_approved(name, sender_id)`（pairing store）→ True
4. else deny

**首 DM 配对流**：not allowed AND is_dm → `generate_code()` → 发 `OutboundMessage(metadata={PAIRING_CODE_META_KEY})`；not allowed AND group → 只 log warning

**`_handle_message()` 派发**：permission check → build `InboundMessage(channel, sender_id, chat_id, content, media, metadata, session_key_override)` → attach `_wants_stream=True`（若 supports_streaming） → `bus.publish_inbound()`

**`supports_streaming`** = `config.streaming AND subclass overrides send_delta`

### 16.2 MessageBus（`src/channels/bus/`）

```python
@dataclass
class InboundMessage:
    channel, sender_id, chat_id, content, timestamp (UTC)
    media, metadata, session_key_override
    @property session_key: override or "{channel}:{chat_id}"

@dataclass
class OutboundMessage:
    channel, chat_id, content, reply_to, media, metadata, buttons

# 元数据 keys:
OUTBOUND_META_AGENT_UI = "_agent_ui"
INBOUND_META_RUNTIME_CONTROL / RUNTIME_CONTROL_ACK / RUNTIME_CONTROL_MCP_RELOAD
```

`MessageBus`：两个 `asyncio.Queue`（inbound / outbound），`publish_*` / `consume_*` / `*_size`

### 16.3 Pairing Store（`src/channels/pairing/store.py`）

- 路径：`~/.vibe-trading/pairing.json`（atomic write via temp + rename + `threading.Lock`）
- 形状：`{"approved": {channel: set[sender_id]}, "pending": {code: {channel, sender_id, created_at, expires_at}}}`
- `generate_code(channel, sender_id, ttl=600s)` → 8 字符 `ABCDEFGH` split `ABCD-EFGH`
- `approve_code(code)` → pending → approved；返 `(channel, sender_id)` 或 None
- `deny_code / revoke / get_approved / list_pending (GC expired) / is_approved / format_pairing_reply / format_expiry`
- `handle_pairing_command(channel, text)`：CLI + runtime 都可用 `/pairing list|approve|deny|revoke`

### 16.4 Channel Registry + Plugin Discovery

- `_INTERNAL`（排除）：base / bus / config / manager / pairing / registry / runtime / utils
- `discover_channel_names()` → `pkgutil.iter_modules` on `src.channels`（zero imports）
- `load_channel_class(name)` → import + 第一个非 `BaseChannel` 子类
- `inspect_channel(name)` → `ChannelAvailability(name, available, display_name, error, install_hint)`
- `inspect_channels(config)` + configured/enabled/loaded/running
- `discover_plugins(enabled_names)`：walks `importlib.metadata.entry_points(group="vibe_trading.channels")`
- built-ins take priority（plugin shadowing log warning）

**YAML config schema**：`ChannelsConfig` Pydantic 全局字段 + per-channel 子模型

### 16.5 Channel Manager（`src/channels/manager.py`）

- `_init_channels()`：`inspect_channels(config)` → enabled_names → skip unavailable → load + instantiate with `cls(section, bus, **kwargs)` → global → per-channel 布尔 override → record loaded/running
- `start_all()`：dispatcher + per-channel start tasks
- `stop_all()`：cancel dispatcher + stop channels
- Outbound dispatcher (`_dispatch_outbound`)：处理 `_reasoning_*`、`_progress`（tool-hint filter）、`_retry_wait` skip、stream-delta coalescing per `_stream_id`、SHA-1 dedup on `(channel, chat_id, origin_message_id)`、retry on `send_max_retries`（1s/2s/4s backoff）
- Public：`get_channel(name)`, `get_status()`, `enabled_channels`

### 16.6 Runtime（`src/channels/runtime.py`）

```python
ChannelRuntime(bus, session_service, manager, *,
               session_map_path=~/.vibe-trading/channels/sessions.json,
               reply_timeout_s=600, poll_interval_s=0.25)
```

- `start(start_manager=True)`：load session map + 可选 `manager.start_all()` as `_manager_task` + spawn `_consume_loop`
- `_consume_loop` reads `bus.inbound` + spawn `_handle_inbound` per msg
- `_handle_inbound`：intercepts `/pairing …` + `/new | /reset | /newsession`（`reset_session`）；else resolve/create Session for `msg.session_key` → `session_service.send_message(...)` → poll `session_service.get_messages()` until assistant reply（linked to `attempt_id`）
- Session map persisted atomically JSON

> 注意：runtime consumes `bus.inbound`；manager owns `bus.outbound` dispatch。两者一起 wire channels ↔ `SessionService`。

### 16.7 16 个 Channel 适配器清单

| Channel | SDK / 协议 | 关键特性 |
|---|---|---|
| **telegram** | python-telegram-bot | Application + MessageHandler/CallbackQueryHandler；inline keyboards；流式 edit-in-place；markdown fence splitter；reply parameters；HTTPXRequest 代理 |
| **discord** | discord.py + app_commands | intents 可配置；group_policy `mention\|open`；流式 message-edit；proxy；per-chat `_StreamBuf` |
| **slack** | slack_sdk Socket Mode (WSS) | webhook fallback；`slackify_markdown`；DM policy；thread context cache；reaction emoji；group policy |
| **signal** | signal-cli REST API | per-channel DM/group；markdown → BodyRange styling；table rendering；重切分 |
| **whatsapp** | neonize async client | SQLite `~/.vibe-trading/runtime/whatsapp-auth/neonize.db`；group_policy `open\|mention`；LID mappings |
| **feishu** | lark_oapi (lazy) WebSocket | Lark/Feishu OpenAPI；`MentionEvent/P2ImMessageReceiveV1`；rich message；streaming buffer |
| **dingtalk** | dingtalk_stream SDK callback | 长连接 stream client；text/image/file via `ChatbotMessage`；`downloadCode` media；DingTalk OpenAPI 出站；`DINGTALK_AVAILABLE` flag |
| **msteams** | Bot Framework | 无公网 IP（本地 HTTP server `host:port`）；JWT 验证；conversation ref 30 天 TTL + 文件锁；Web Chat 剪枝 |
| **napcat** | OneBot v11 WebSocket | `websockets.asyncio` + HTTP action API；`group_policy` literal/float probability；per-group override；welcome 新成员；image size cap |
| **qq** | botpy SDK | c2c + group；rich-media upload（msg_type=7）；file/image classification；CJK 文件名净化 |
| **mochat** | python-socketio (msgpack optional) | mention/group rules；panel target resolution；delayed-dispatch buffer + cursor save；mixed text/media |
| **matrix** | nio AsyncClient | E2EE via Olm/Megolm；markdown → custom HTML（`nh3` allowlist）；workspace policy；cross-process safety guards；streaming buffer |
| **wecom** | WeCom AI Bot WebSocket long-connection | 无公网 IP；bot_id + secret；image/voice/file/video classification（200MB cap）|
| **weixin** | 反向工程 iLink WS 协议 v2.1.1 | 基于 `@tencent-weixin/openclaw-weixin` v1.0.3；item types 1-5（text/image/voice/file/video）；context_token proactive refresh（~60s）；连续失败 backoff |
| **email** | IMAP (IDLE/UID) + SMTP | `consent_granted` gate；`verify_dkim/verify_spf` 反 spoofing；attachment allowlist + size cap；`post_action` delete/move；`mark_seen`；`Re:` subject prefix；thread context |
| **websocket** | websockets.asyncio | 本地 WS server（ws + unix）+ HTTP routes for token/media；订阅 fan-out by chat_id；static + issued one-time tokens；37 MB max msg；配 `GatewayServices`；桥接 `cli_apps_api/mcp_presets_api/forking/transcription_ws` |

### 16.8 `src/channelsui/`（WebUI 兼容层）

- **目的**：让 `websocket` channel 表现得像 legacy WebUI 但跑在 channel-agnostic 进程里
- 模块：
  - `cli_apps_api.py`：`normalize_cli_app_mentions(content, metadata?)` → list of names
  - `forking.py`：`handle_webui_fork_chat(...)` async stub（emits error）
  - `gateway_services.py`：real services — `WebSocketTokenIssuer` / `WorkspaceScope` / `SimpleHttpRouter` / `MediaService` / `TranscriptService` / `GatewaySessionManagerAdapter` / `GatewayServices` / `build_gateway_services(...)`
  - `http_utils.py`：`normalize_config_path` / `parse_request_path` / `query_first` / `parse_and_validate_url` / `read_uploaded_file`
  - `mcp_presets_api.py`：`normalize_mcp_preset_mentions(...)`
  - `transcription_ws.py`：`webui_transcription_event(...)` stub（audio transcription not configured）
  - `websocket_logging.py`：`websockets_server_logger`

---

## 17. Preflight + Provider 配置

### 17.1 Preflight（`src/preflight.py`，312 行）

```python
@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str              # ∈ {ready, error, not_configured, skipped}
    message: str
    impact: str
    critical: bool
```

| 检查 | 严重性 | 行为 |
|---|---|---|
| `_check_llm_provider` | **CRITICAL**（block startup）| 读 `LANGCHAIN_PROVIDER/LANGCHAIN_MODEL_NAME` → sync env → ping `OPENAI_BASE_URL`（TCP+SSL）；`openai-codex` → `get_openai_codex_login_status()`（OAuth）|
| `_check_okx` | warning | `requests.get("https://www.okx.com/api/v5/market/candles", params=BTC-USDT)`；`code == "0"` |
| `_check_yfinance` | warning | `yfinance` import + `AAPL fast_info.last_price` |
| `_check_tushare` | warning | `TUSHARE_TOKEN`；package missing → skipped；token missing → not_configured |
| `_check_akshare` | warning | `find_spec("akshare")` |
| `_check_ccxt` | warning | `import ccxt` |
| `_check_content_filter_threshold` | info | 报告 `CONTENT_FILTER_WARNING_THRESHOLD` |

`run_preflight(console)` 渲染 Rich borderless 表（OK/FAIL/N/A/SKIP 彩色）。critical fail 打印 "agent cannot start"，**不抛异常**。

### 17.2 Provider 配置（`src/providers/llm_providers.json`）

**16 个内置 provider**：

| Provider | 类型 |
|---|---|
| `openrouter` | OpenAI-compat（推荐） |
| `openai` | OpenAI-compat |
| `openai-codex` | **OAuth** via ChatGPT → Codex Responses endpoint（不走 langchain）|
| `deepseek` | OpenAI-compat |
| `gemini` | OpenAI-compat（thought signatures）|
| `groq` | OpenAI-compat |
| `dashscope` / `qwen` | OpenAI-compat |
| `zhipu` / `glm` | OpenAI-compat |
| `moonshot` / `kimi` | OpenAI-compat（reasoning）|
| `kimi-coding` | OpenAI-compat |
| `minimax` | OpenAI-compat |
| `mimo` | OpenAI-compat（小米）|
| `zai` | OpenAI-compat |
| `ollama` | 本地（无 key）|

**Schema**：
```json
{
  "name": "deepseek",
  "label": "DeepSeek",
  "api_key_env": "DEEPSEEK_API_KEY",
  "base_url_env": "DEEPSEEK_BASE_URL",
  "default_model": "deepseek-chat",
  "default_base_url": "https://api.deepseek.com/v1",
  "api_key_required": true
}
```

OAuth：`auth_type: "oauth"`，`login_command: "vibe-trading provider login openai-codex"`

**路由**：`src/providers/llm.py` 暴露 `build_llm(model_name=None, callbacks=None)` → 返 `langchain_openai.ChatOpenAI` 子类（`ChatOpenAIWithReasoning`）；`src/providers/capabilities.py` 定义 `ProviderCapabilities`（reasoning capture / Gemini thought signatures / OpenRouter reasoning body / default headers / user-agent）；`src/providers/chat.py` 定义 `ChatLLM`（raw message interface + function-calling，被 `AgentLoop` ReAct 用）；`openai_codex.py` 走 `httpx` 对 Codex Responses endpoint。

**Content filter**（`src/providers/content_filter.py`）：处理 `finish_reason="content_filter"`（OpenAI）+ Gemini 大写 FinishReason enums；10 次连续 skip 后熔断。

---

## 18. 安全层（`src/security/`）

- `network.py`：re-export `validate_url_target / validate_resolved_url`（防 SSRF：`is_global` + multicast block）
- `scanner.py`：**prompt-injection 扫描器**，5 条规则：
  - `instruction_override`
  - `system_prompt_exfiltration`
  - `role_or_channel_claim`
  - `secret_exfiltration`
  - `tool_abuse`
- **不**重写 / drop content；只附 `security_warnings` 到 JSON envelopes
- `with_security_warnings(payload, fields=...)`：walks dotted selectors（如 `results.*.snippet`）
- `workspace_access.py`：`WorkspaceScopeError`, `WORKSPACE_SCOPE_METADATA_KEY = "_workspace_scope"`
- `workspace_policy.py`：re-exports `is_path_within` from `src.channels.utils`
- **无 sandbox / rate-limit 模块**

---

## 19. CLI UI 组件

| 文件 | 作用 |
|---|---|
| `cli/components/tool_event.py` | `render_tool_event(name, args, status, duration_ms, summary)` + `render_tool_events(events)` — 输出 `● Get Financials ("AAPL") 1.4s · 8 quarters`（amber blink 运行 / green ok / red error）|
| `cli/components/hint_bar.py` | 右对齐 hint 行（mirror `frontend/src/lib/format.ts`）|
| `cli/components/chat_log.py` | 回合重放（timestamp + meta header + body）|
| `cli/components/working_indicator.py` | `ThinkingSpinner`（rich.live.transient）+ `pause()`（mid-run 嵌套）|
| `cli/ui/rail.py` | Codex 风格 activity rail（`RailStep` + 渲染器；映射工具名为短标题 "shell command" / "skill" / "file" / ...）|
| `cli/ui/banner.py` | 渐变 "VIBE" ASCII logo + `vibe-trading v0.1.11 · cli · <model>` meta |
| `cli/ui/transcript.py` | `render_recap` / `render_elapsed_status` / `render_answer`（Markdown 表格升级；strip standalone HR）|
| `cli/stream.py` | `StreamRenderer(mode="single"\|"swarm")`；`turn(verb=None)` context；`on_tool_start/on_tool_end`；`_print_tool_line()` 调 `_emit_static()` + `with self._spinner.pause():`；`print_answer` / `print_footer` |
| `cli/theme.py` | 自动深浅色 + `COLORFGBG/TERM_PROGRAM/VIBE_TRADING_THEME` + `NO_COLOR`；`Theme` style tokens |
| `cli/utils/thinking_verbs.py` | `THINKING_VERBS = ("Pondering","Analyzing","Reasoning","Investigating","Synthesizing","Cross-checking")` + `pick_thinking_verb(seed=None)` |
| `cli/utils/format.py` | `format_duration(ms\|s)` / `format_tokens(n)` / `abbreviate_num(n, currency=…)` |
| `cli/completer.py` | `SlashCompleter` (prompt_toolkit) → slash typeahead |
| `cli/input.py` | `make_session()` / `get_user_input()` / `ctrl_c_within_window()` / `SafeFileHistory`（surrogate-strip）；Enter 平衡 buffer 提交；Alt+Enter 换行；Ctrl+C 三态；Ctrl+D 退出 |
| `cli/intro.py` | re-exports `print_banner` |

---

## 20. 完整文件清单

| 模块 | 文件数 | 备注 |
|---|---|---|
| 根目录 | 2 | `api_server.py`, `mcp_server.py` |
| `backtest/` | 49 | loaders: 26, engines: 12, optimizers: 6, +5 顶层 |
| `cli/` | 27 | 含 components/ui/utils/commands |
| `src/agent/` | 8 | loop / context / skills / tools / memory / frontmatter / progress / trace |
| `src/api/` | 14 | 11 route modules + helpers / state / _compat / models |
| `src/channels/` | 18 | 16 适配器 + base / manager / registry / runtime / config / bus / pairing / utils |
| `src/channelsui/` | 8 | WebUI 兼容层 |
| `src/config/` | 6 | accessor / env_schema / loader / paths / schema / __init__ |
| `src/core/` | 3 | runner / state / __init__ |
| `src/factors/` | 12 | base / registry / cli_handlers / compare_runner / factor_analysis_core / bench_runner / bench_runner_strict / _backend + 4 zoo |
| `src/factors/zoo/` | 462 | alpha101 (101) + gtja191 (191) + qlib158 (154 .py) + academic (10) + fundamental (4) |
| `src/goal/` | 6 | models / policy / store / context / cli_handlers(?) / __init__ |
| `src/hypotheses/` | 3 | registry / cli_handlers / __init__ |
| `src/live/` | 17 | classification / order_guard / halt / enforcement / audit / daily_count / paths / sdk_order_gate + mandate/ + advisory/ + extractors/ + runtime/ |
| `src/memory/` | 2 | persistent / __init__ |
| `src/providers/` | 7 | llm / capabilities / chat / openai_codex / content_filter / __init__ + llm_providers.json |
| `src/scheduled_research/` | 4 | executor / models / store / __init__ |
| `src/security/` | 5 | network / scanner / workspace_access / workspace_policy / __init__ |
| `src/session/` | 10 | store / service / search / events / models / goal_state / webui_turns + ... |
| `src/shadow_account/` | 9 | extractor / backtester / codegen / storage / scanner / reporter / fonts / models + templates/ |
| `src/skills/` | 87 | SKILL.md + 数百个 references/ |
| `src/swarm/` | 8 + 30 YAML | models / presets / runtime / worker / task_store / store / grounding / serialization + 30 presets |
| `src/tools/` | 65+ | 70+ BaseTool 子类 |
| `src/trading/` | 5 + 11 broker 子包 | service / types / profiles / __init__ + 11 connectors |
| `src/utils/` | 8 | misc utilities |
| **合计** | **≈1262** | |

---

## 21. strategy-research 借鉴路线图

> 当前 strategy-research 的真实状态（来自子代理扫描）：
>
> | 现状 | 严重程度 |
> |---|---|
> | `_spawn_agent()` 是 canned JSON stub，未集成任何 LLM | **致命** |
> | `cmd_init()` 在 `.format()` 上踩 `{}` 报错 | **致命** |
> | 默认 `FACTOR_EXPRS=[]`，baseline 不是 buy-and-hold，是空指标 | **阻塞** |
> | DuckDB schema 在 `cli.py` 与 `core/db.py` 重复且不一致 | **阻塞** |
> | `.prompts/` 11 个角色只是 markdown；`build_agent_prompt()` 存在但 CLI 没调用 | **未接通** |
> | `FALLBACK_CHAINS` 引用未实现 loader（mootdx/eastmoney/baostock）| **死链** |
> | `data_import.py` 把 OHLCV 压成 close 面板再丢掉 OHLV | **数据丢失 bug** |
> | Autoresearch 无 Calmar/风险/连续无改善停止条件 | **设计缺口** |
> | YAML `alpha_id` 字段不识别 | **文档与实现脱节** |
> | 测试 573 个函数，覆盖率陈旧 | **待重测** |

### 21.1 借鉴优先级矩阵

#### P0 — 必修（vibe-trading 直接提供最小可用代码）

| 模式 | vibe-trading 出处 | 收益 | 体积 |
|---|---|---|---|
| **`BaseTool` + `ToolRegistry`** | `src/agent/tools.py` (94 行) | 给 agent 工具抽象（run_backtest / compute_factor / read_workspace / git_commit …）—— 现在完全没有 | 极小 |
| **`DataLoaderProtocol` + `LOADER_REGISTRY` + `FALLBACK_CHAINS`** | `backtest/loaders/registry.py` (220 行) + `loaders/base.py` | 修复 "fallback 链引用未实现 loader" 问题；统一 tencent/tushare/akshare/ifind/fred/yfinance/local 接口 | 中 |
| **`SkillsLoader`（SKILL.md + frontmatter + support files + user override）** | `src/agent/skills.py` (182 行) | 把现有 `templates/` 改造成 progressive disclosure skill；让因子库 / 算子 / 数据源可发现 | 小 |
| **`Preflight` 启动健康检查** | `src/preflight.py` (312 行) | 启动时检测 LLM key / DuckDB 可写 / 数据源可达；CLI 现在直接 crash | 小 |
| **修复 `.format()` / DuckDB schema / baseline 三处致命阻塞** | — | 让 `cmd_init` 真正能跑出 workspace | — |

#### P1 — 强烈推荐（让研究循环真正转起来）

| 模式 | 出处 | 收益 |
|---|---|---|
| **AgentLoop + 5 层 context 压缩**（`microcompact` + `context_collapse`）| `src/agent/loop.py` | 替换 `_spawn_agent` stub；agent 读长 DuckDB / factor 表时不被 context 撑爆 |
| **Swarm DAG runtime**（`topological_layers` + ThreadPoolExecutor + per-task retry + 层截止）| `src/swarm/runtime.py` | 让 `.prompts/` 11 个角色真正可执行；orchestrator 调度 researcher → factor_analyst → strategist → critic → risk_controller |
| **Persistent memory**（MEMORY.md + auto-recall + 多语 tokenizer）| `src/memory/persistent.py` (265 行) | 让 agent 跨 run 保留 "上次发现：momentum 在中小盘失效" 这种洞见 |
| **Goal/Criterion/Evidence 生命周期**（3-5 个 acceptance criteria + evidence rows）| `src/goal/` | 替代单一 metrics 卡；让一次 research 写成可审计论文 |
| **Hypothesis Registry**（exploring / testing / validated / rejected / monitoring）| `src/hypotheses/` | 让 alpha 假说有状态机，不是 TSV 行 |
| **验证层**（Monte Carlo + Bootstrap + Walk-Forward）| `backtest/validation.py` | 配合 `factor_validate.py` 已有 IC/IR，加防过拟合第二道闸 |

#### P2 — 按需借鉴（增强阶段）

| 模式 | 出处 | 何时用 |
|---|---|---|
| **多市场 `BaseEngine`**（`can_execute / round_size / calc_commission / apply_slippage / on_bar`）| `backtest/engines/base.py` | 后续做期货 / 加密时 |
| **Optimizer 抽象**（`BaseOptimizer` + 5 个具体实现）| `backtest/optimizers/` | 做组合优化时 |
| **Scheduled research**（cron 调度 + executor）| `src/scheduled_research/` | 想让框架"每天自动跑一轮"时 |
| **报告 PDF（WeasyPrint）** | `src/shadow_account/reporter.py` | 想给客户 / 老板交报告时 |
| **`run_card.py` 增加 evidence section** | vibe-trading 与本地版都有 | 把数据来源 / artifact provenance 补齐 |
| **45 个因子 α-bench strict 评测**（random control + OOS）| `src/factors/bench_runner_strict.py` | 做大规模因子筛选时 |

### 21.2 不要搬的（明确不借鉴）

- **Live trading**：broker connectors / mandate gate / halt flag / 3-tier tool classification —— strategy-research 是 research-only
- **Channels**：Telegram / Discord / Slack / Feishu / Dingtalk 等 14 个 —— 没有多渠道分发需求
- **Shadow account**：从用户交易日志反推策略 —— 离当前阶段太远
- **LangChain / LangGraph 整套**：strategy-research 用纯 Python + DuckDB，不引 LangChain 重型依赖
- **China-A 专有 MCP 工具**（龙虎榜 / 北向资金 / 股东户数等 30+）：除非明确做 A 股研究门户
- **WebUI / FastAPI / WebSocket**：strategy-research 不需要 GUI

### 21.3 落地路径（4 个里程碑）

> 以下是研究阶段建议；执行前需用户拍板。

**M1（2 周）：修通 init 流水线**
- 改 `.format()` 为 `string.Template` 或 jinja2 渲染
- 统一 DuckDB schema（CLI 与 `core/db.py` 共用一份）
- baseline：默认 `FACTOR_EXPRS=[{'code':'ts_mean(close,20)/ts_mean(close,60) - 1'}]`，让 `prepare.evaluate()` 给出真实指标
- 接入 `DataLoaderProtocol` + `FALLBACK_CHAINS`，删 dead link
- 加 `preflight` 启动检查

**M2（3 周）：让 agent 真跑起来**
- 引入 `BaseTool` + `ToolRegistry`，提供至少 6 个工具：`read_file / write_file / run_backtest / compute_factor / git_diff / list_history`
- 引入 `SkillsLoader`，把 `templates/program.md` 改成 `templates/skills/strategy-research/SKILL.md`，加上 `prepare.py`、`strategy.py`、`data-source` 等若干 skill
- 接 LLM（OpenAI / DeepSeek / Kimi 任一），重写 `_spawn_agent` 为真实 ReAct 循环
- 加 `microcompact` + `context_collapse` 两层压缩

**M3（3 周）：让 swarm 真跑起来**
- 引入 `SwarmRuntime`（topological_layers + ThreadPoolExecutor + per-task budget）
- 把 `.prompts/` 11 个角色接到 swarm presets（orchestrator → researcher → factor_analyst → strategist → critic → risk_controller → attribution_analyst → anti_overfit_analyst → backtest_diagnostics → portfolio_construction → data_quality）
- 加 `PersistentMemory`（MEMORY.md + auto-recall），让跨 run 经验保留

**M4（2 周）：加验证与假设生命周期**
- 接 `GoalStore`（3-5 acceptance criteria + evidence rows）
- 接 `HypothesisRegistry`（draft / proposed / accepted / rejected / retired）
- 接 `validation.py`（Monte Carlo + permutation），与 `factor_validate.py` 的 IC/IR 串成两道闸
- 加 Calmar / 连续无改善 / stuck detection 三个停止条件

### 21.4 借鉴深度选项（待拍板）

| 方式 | 描述 | 优劣 |
|---|---|---|
| **A. 借鉴思想 + 自己重写最小版**（推荐）| 只读 vibe-trading 的设计模式（BaseTool / LoaderRegistry / Skills / Swarm / Memory），在 strategy-research 中按现有风格用纯 Python + DuckDB 重写 | 代码量 <2k LOC；零 LangChain 依赖；可独立演进 |
| **B. 借鉴思想 + 整包复制关键模块** | 把 vibe-trading 的 `src/agent/tools.py` + `skills.py` + `memory/persistent.py` 等小模块直接复制（保留 LICENSE/NOTICE），再补依赖 | 开发快；但要带 LangChain 等重依赖 |
| **C. 只做架构对照，不动代码** | 仅产出对比文档与建议方案，本次不引入任何代码改动，留作后续 PR | 零风险；但要后续单独投入 |

### 21.5 LLM 集成选型（待拍板）

| Provider | 优劣 |
|---|---|
| **OpenAI 兼容通用**（推荐）| 用 httpx 直接调 OpenAI / DeepSeek / Kimi / 通义千问等 OpenAI 兼容 API；不引入 langchain-openai；便于切换、依赖最轻 |
| **LangChain ChatModel 抽象** | 用 langchain-openai / langchain-anthropic 等，5 行即可切换模型；代价是引入 LangChain 整套 |
| **暂不接 LLM** | 分阶段：M1 只修基础设施，把 `_spawn_agent` 留作 stub；M2 再决定 LLM |

### 21.6 Skills 格式选型（待拍板）

| 选项 | 说明 |
|---|---|
| **照搬 opencode SKILL.md**（推荐）| 用 YAML frontmatter + Markdown 正文 + 目录形式；与 `~/.config/opencode/skills/` 完全一致，agent 在两个项目间无缝复用 |
| **vibe-trading 风格**（带 category + 支持文件 + user override）| category 字段（data-source / strategy / analysis / ...）+ user override 目录 + 加载示例代码；功能更全但和 opencode 不兼容 |
| **保留现有 templates/ 结构不动** | 维持 `templates/{config.yaml, prepare.py, strategy.py, program.md}` 不变；改动最小 |

### 21.7 Swarm 范围选型（待拍板）

| 选项 | 说明 |
|---|---|
| **先只跑现有 11 个 `.prompts/`**（推荐）| 把 11 个角色接到 SwarmRuntime 即可，足够单因子研究循环 |
| **把 11 个扩到 20+** | 参考 vibe-trading 的 factor_research_committee / investment_committee / sector_rotation_team 等，新增对应 YAML preset；覆盖更广但要先有数据源支撑 |
| **暂不上 swarm** | 先实现单 agent ReAct + 角色切换（按需加载 system prompt）；Swarm 留到 M3 |

---

## 附录 A：vibe-trading 已验证的核心代码段定位

```
~/vibe_env/lib/python3.11/site-packages/
├── src/agent/tools.py                            # 94 行
├── src/agent/skills.py                           # 182 行
├── src/agent/loop.py                             # 1607 行（核心 ReAct）
├── src/agent/context.py                          # 324 行
├── src/agent/progress.py                         # HeartbeatTimer
├── src/agent/trace.py                            # TraceWriter
├── src/agent/frontmatter.py                      # YAML parser
├── src/agent/memory.py                           # WorkspaceMemory（per-run）
├── src/memory/persistent.py                      # 265 行
├── src/goal/models.py                            # GoalRecord/Criterion/Evidence
├── src/goal/store.py                             # SQLite ledger
├── src/goal/context.py                           # goal continuation prompt
├── src/goal/policy.py                            # 拒绝 live execution 文本
├── src/hypotheses/registry.py                    # JSON 持久化 + 搜索
├── src/scheduled_research/executor.py            # cron poller
├── src/swarm/runtime.py                          # DAG + ThreadPoolExecutor
├── src/swarm/worker.py                           # mini-ReAct for worker
├── src/swarm/grounding.py                        # OHLCV 预取防幻觉
├── src/swarm/presets/*.yaml                      # 30 个 preset
├── src/preflight.py                              # 312 行
├── backtest/runner.py                            # 950 行
├── backtest/engines/base.py                      # 807 行
├── backtest/loaders/base.py                      # DataLoaderProtocol + 缓存
├── backtest/loaders/registry.py                  # FALLBACK_CHAINS
├── backtest/optimizers/base.py                   # ABC
├── backtest/validation.py                        # MC + Bootstrap + WF
├── backtest/run_card.py                          # Trust Layer
├── backtest/metrics.py                           # bars_per_year
├── src/factors/registry.py                       # AST 扫 + 文件名注册
├── src/factors/bench_runner_strict.py            # random control + OOS
├── src/factors/cli_handlers.py                   # `alpha` 子命令
└── src/hypotheses/cli_handlers.py                # `hypothesis` 子命令
```

## 附录 B：策略研究 (strategy-research) 借鉴可行性速查

| 借鉴项 | 文件来源 | 改造工作 | 优先级 |
|---|---|---|---|
| BaseTool + ToolRegistry | `src/agent/tools.py` | 直接抄（94 行）；加 ~3 行 duck typing 适配 | **P0** |
| DataLoaderProtocol + FALLBACK_CHAINS | `backtest/loaders/{base,registry}.py` | 抄接口 + 重写 7 个本地 loader | **P0** |
| SkillsLoader (SKILL.md) | `src/agent/skills.py` + `src/skills/` 模板 | 抄 loader + 改造 templates/ 为 skills/ | **P0** |
| Preflight | `src/preflight.py` | 删 openai-codex OAuth 改自家 check | **P0** |
| AgentLoop (5 层压缩) | `src/agent/loop.py` (1607 行) | 抄 mini 版（microcompact + context_collapse，~300 行） | **P1** |
| SwarmRuntime | `src/swarm/runtime.py` (~750 行) | 抄拓扑分层 + worker 派发 | **P1** |
| PersistentMemory | `src/memory/persistent.py` | 直接抄（265 行） | **P1** |
| GoalStore (SQLite) | `src/goal/store.py` | 改 schema 适配 strategy-research；3-5 criteria + evidence | **P1** |
| HypothesisRegistry | `src/hypotheses/registry.py` | 直接抄；加 CLI `add/propose/accept/reject` | **P1** |
| validation (MC + Bootstrap + WF) | `backtest/validation.py` | 直接抄 | **P1** |
| BaseEngine 多市场 | `backtest/engines/base.py` | 抄接口（仅 long-only 不实现 T+1/涨跌停 等） | **P2** |
| Optimizers | `backtest/optimizers/{base,equal_volatility,risk_parity,turnover_aware}.py` | 抄 ABC + 选 1-2 个 | **P2** |
| ScheduledResearch | `src/scheduled_research/executor.py` | 抄 cron poller | **P2** |
| bench_runner_strict | `src/factors/bench_runner_strict.py` | 抄 random control + OOS | **P2** |
| run_card evidence 字段 | `backtest/run_card.py` | 抄 Trust Layer 字段 | **P2** |
| channels (14 IM) | `src/channels/` | **不借鉴**（research-only 不需要） | ✗ |
| live trading (broker + mandate + halt) | `src/live/` + `src/trading/` | **不借鉴**（out of scope） | ✗ |
| shadow_account | `src/shadow_account/` | **不借鉴**（pre-条件：用户交易日志） | ✗ |
| LangChain / LangGraph | pyproject deps | **不引入** | ✗ |
| MCP 工具（A 股专属） | `mcp_server.py` 54 个里约 30 个 | **不引入**（除非做 A 股门户） | ✗ |
| WebUI / FastAPI / SPA | `api_server.py` + `frontend/` | **不引入** | ✗ |

---

> **调研结束**。本文档可作为 strategy-research 后续重构的蓝本。