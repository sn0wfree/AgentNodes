# 实施计划：Goal + Web I/O + 市场数据 + Swarm MCP 工具

> 调研日期：2026-07-24
> 目标：将 MCP 工具从 13 个扩展到 21 个，覆盖率从 24% 提升到 39%
> 基准：vibe-trading 54 MCP 工具

---

## 1. 对比结果：vibe-trading 54 MCP 工具 vs strategy-research

### 1.1 完整对比表

| # | 类别 | 工具名 | 用途 | 我们有？ |
|---|---|---|---|---|
| | **Skill（2个）** | | | |
| 1 | | `list_skills` | 列出所有技能 | ✅ |
| 2 | | `load_skill` | 加载技能内容 | ✅ |
| | **Goal（4个）** | | | |
| 3 | | `start_research_goal` | 创建研究目标 | ✅ |
| 4 | | `get_research_goal` | 获取当前目标 | ✅ |
| 5 | | `add_goal_evidence` | 向目标追加证据 | ❌ 后端已有 |
| 6 | | `update_research_goal_status` | 更新目标状态 | ❌ 后端已有 |
| | **回测/分析（4个）** | | | |
| 7 | | `backtest` | 执行向量化回测 | ✅ |
| 8 | | `factor_analysis` | 因子 IC/IR 分析 | ✅ |
| 9 | | `analyze_options` | Black-Scholes + Greeks | ✅ |
| 10 | | `pattern_recognition` | 图表形态检测 | ✅ |
| | **I/O（5个）** | | | |
| 11 | | `read_url` | 抓网页转 Markdown | ❌ |
| 12 | | `read_document` | PDF 文本抽取 | ❌ |
| 13 | | `web_search` | DuckDuckGo 搜索 | ❌ |
| 14 | | `write_file` | 写文件 | ✅ |
| 15 | | `read_file` | 读文件 | ✅ |
| | **交易连接器（8个）** | | | |
| 16-23 | | `trading_*` | 列出/切换/查询 broker | ❌ 不在范围 |
| | **Swarm（7个）** | | | |
| 24 | | `list_swarm_presets` | 列出 swarm preset | ✅ |
| 25 | | `run_swarm` | 启动 swarm 执行 | ❌ 有运行时 |
| 26-30 | | `get_swarm_status` / `get_run_result` / `list_runs` / `reap_stale_runs` / `retry_run` | Swarm 管理 | ❌ |
| | **A 股市场数据（17个）** | | | |
| 31-47 | | `get_fund_flow` / `get_dragon_tiger` / ... | 主力资金/龙虎榜/北向等 | ❌ |
| | **美股（1个）** | | | |
| 48 | | `get_sec_filings` | SEC 文件 | ❌ |
| | **Shadow（5个）** | | | |
| 49-53 | | `analyze_trade_journal` / ... | 交易日志→影子策略 | ❌ 不在范围 |
| | **通用数据（1个）** | | | |
| 54 | | `get_market_data` | 按 loader 链拉 OHLCV | ❌ |

### 1.2 汇总

| 维度 | vibe-trading | strategy-research |
|---|---|---|
| MCP 工具总数 | 54 | **13** |
| Agent 内置工具 | ~70 | **11** |
| 重叠部分 | — | 12 个 |
| **覆盖率** | — | **24%（13/54）** |

### 1.3 我们独有的（vibe-trading 没有）

| 工具 | 用途 |
|---|---|
| `validate_run` | Monte Carlo + Bootstrap + Walk-Forward 验证 |
| `search_messages` | 全文搜索会话消息 |
| `list_sessions` | 列出所有会话 |
| `search_memory` / `add_memory` | 持久化记忆读写 |
| `git_diff` | Git diff（Agent 工具）|
| `list_history` | 历史运行记录（Agent 工具）|

---

## 2. 设计决策

| 问题 | 决策 |
|---|---|
| Swarm 执行超时 | 可配置，默认 300s |
| 搜索限速 | 指数冷却（初始 1s，最大 30s，因子 2）|
| 行情数据输出 | 可配置，默认 500 行/code |
| 实施顺序 | Goal → Web → Data → Swarm |
| Agent 工具 | MCP + Agent 都加 |

---

## 3. 阶段 1：Goal MCP 工具

### 3.1 新增 MCP 工具

| MCP 工具 | 映射到 | 参数 |
|---|---|---|
| `add_goal_evidence` | `GoalStore.append_evidence()` | `session_id`, `goal_id`, `text`, `criterion_id?`, `claim_id?`, `source_type?`, `source_uri?`, `confidence?`, `caveat?` |
| `update_research_goal_status` | `GoalStore.update_status()` | `session_id`, `goal_id`, `status`, `recap?` |

### 3.2 修改文件

- `src/strategy_research/core/mcp/server.py` — 在 `_register_goal_tools()` 末尾新增 2 个 handler

### 3.3 关键实现

- `append_evidence` 防陈旧目标守卫：MCP 无状态调用，用 `goal_id` 同时作为 `goal_id` 和 `expected_goal_id`
- `EvidenceInput` 有 18 个字段，只暴露 8 个最常用的
- `update_status` 的 `status` 参数用字符串，内部转 `GoalStatus` 枚举

### 3.4 测试（~8 个，在 `tests/test_mcp_real_impls.py` 扩展）

1. `test_add_goal_evidence_happy_path`
2. `test_add_goal_evidence_updates_criterion`
3. `test_add_goal_evidence_empty_text_fails`
4. `test_add_goal_evidence_no_active_goal_fails`
5. `test_update_goal_status_to_complete`
6. `test_update_goal_status_to_cancelled`
7. `test_update_goal_status_invalid_value`
8. `test_update_goal_status_no_active_goal`

---

## 4. 阶段 2：Web I/O 工具

### 4.1 新增文件

| 文件 | 用途 |
|---|---|
| `src/strategy_research/core/web/__init__.py` | 包初始化 |
| `src/strategy_research/core/web/search.py` | DuckDuckGo 搜索 + 指数冷却 |
| `src/strategy_research/core/web/fetch.py` | URL 抓取 + HTML→Markdown |
| `src/strategy_research/core/web/pdf.py` | PDF 文本抽取 |
| `src/strategy_research/core/web/_rate_limit.py` | 指数冷却限速器 |
| `src/strategy_research/core/agent/builtin_tools/web_tools.py` | Agent 工具 |
| `tests/test_web_search.py` | ~8 个测试 |
| `tests/test_web_fetch.py` | ~10 个测试 |
| `tests/test_web_pdf.py` | ~6 个测试 |

### 4.2 新增依赖

```toml
"markdownify>=0.13",
"duckduckgo-search>=7.0",
```

可选：
```toml
[project.optional-dependencies]
web = ["PyMuPDF>=1.24"]
```

### 4.3 工具清单

| 工具 | 实现 | 参数 |
|---|---|---|
| `web_search` | `duckduckgo_search.DDGS().text()` | `query`, `max_results?`（默认 10）|
| `read_url` | `httpx.get()` + `markdownify.markdownify()` | `url`, `max_chars?`（默认 10000）|
| `read_document` | `fitz.open()` → 逐页提取 | `path`, `max_pages?`（默认 50）|

### 4.4 指数冷却限速器

```python
class ExponentialBackoff:
    def __init__(self, base=1.0, max_delay=30.0, factor=2.0): ...
    def wait(self): ...
    def reset(self): ...
```

### 4.5 角色白名单更新

```python
"researcher":     ["read_file", "list_history", "factor_analysis", "web_search", "read_url"],
"data_quality":   ["read_file", "web_search", "read_url"],
"strategist":     ["read_file", "write_file", "run_backtest", "git_diff", "web_search", "read_url"],
```

---

## 5. 阶段 3：市场数据 MCP 工具

### 5.1 新增文件

| 文件 | 用途 |
|---|---|
| `src/strategy_research/core/agent/builtin_tools/data_tools.py` | Agent 工具 |
| `tests/test_mcp_data_tools.py` | ~12 个测试 |

### 5.2 新增 MCP 工具

| MCP 工具 | 映射到 | 参数 |
|---|---|---|
| `get_market_data` | `resolve_loader(market).fetch()` | `codes`, `start_date`, `end_date`, `interval?`, `source?`, `max_rows?`（默认 500）|
| `list_data_sources` | `list_loaders()` + `is_available()` | 无 |
| `search_symbol` | `akshare.stock_zh_a_spot_em()` | `query`, `market?`, `limit?` |

### 5.3 角色白名单更新

```python
"researcher":     [..., "get_market_data", "search_symbol"],
"data_quality":   [..., "get_market_data", "list_data_sources"],
"factor_analyst": [..., "get_market_data"],
"strategist":     [..., "get_market_data"],
"portfolio_construction": ["read_file", "get_market_data"],
"risk_controller": ["read_file", "factor_analysis", "get_market_data"],
```

---

## 6. 阶段 4：Swarm MCP 执行工具

### 6.1 新增文件

| 文件 | 用途 |
|---|---|
| `src/strategy_research/core/swarm/run_store.py` | 内存运行结果存储（LRU 20）|
| `tests/test_mcp_swarm_tools.py` | ~8 个测试 |

### 6.2 新增 MCP 工具

| MCP 工具 | 映射到 | 参数 |
|---|---|---|
| `run_swarm` | `SwarmRuntime.execute()` | `preset_name`, `workspace`, `task`, `max_workers?`, `timeout?`（默认 300）|
| `get_swarm_status` | `RunStore.get()` | `run_id` |

### 6.3 RunStore

```python
class RunStore:
    MAX_RUNS = 20
    def save(self, run_id, result): ...
    def get(self, run_id): ...
    def list_recent(self, limit=10): ...
```

---

## 7. 依赖汇总

```toml
dependencies = [
    # ... 现有依赖 ...
    "markdownify>=0.13",
    "duckduckgo-search>=7.0",
]

[project.optional-dependencies]
web = ["PyMuPDF>=1.24"]
```

---

## 8. 测试统计

| 阶段 | 新增测试 | 新增代码行 | 新增文件 |
|---|---|---|---|
| 阶段 1：Goal MCP | 8 | ~80 | 0（扩展 test_mcp_real_impls.py）|
| 阶段 2：Web I/O | 25 | ~250 | 7（4 源码 + 3 测试）|
| 阶段 3：市场数据 | 12 | ~200 | 3（1 源码 + 1 测试 + 1 工具文件）|
| 阶段 4：Swarm MCP | 8 | ~120 | 2（1 源码 + 1 测试）|
| **总计** | **53** | **~650** | **12** |

### 实际结果

- 6255 测试全部通过（之前 6212，新增 43 测试；部分原有测试被跳过因网络不可用）
- 0 failed, 21 skipped, 29 xfailed

---

## 9. 最终状态

| 维度 | 之前 | 之后 |
|---|---|---|
| MCP 工具 | 13 | **21** |
| Agent 工具 | 11 | **17** |
| 覆盖率 | 24% | **39%** |
| 新增依赖 | — | 2 个 |
| 新增文件 | — | 12 个 |

### 实际完成的新文件

```
src/strategy_research/core/web/__init__.py
src/strategy_research/core/web/_rate_limit.py
src/strategy_research/core/web/search.py
src/strategy_research/core/web/fetch.py
src/strategy_research/core/web/pdf.py
src/strategy_research/core/agent/builtin_tools/web_tools.py
src/strategy_research/core/agent/builtin_tools/data_tools.py
src/strategy_research/core/swarm/run_store.py
tests/test_web_search.py
tests/test_web_pdf.py
tests/test_mcp_data_tools.py
tests/test_mcp_swarm_tools.py
docs/archive/undated/PLAN-phase1-4.md
```

---

## 5. v0.5.0 — Init Wizard Rewrite (2026-07-24)

### 5.1 目标

把 `quantnodes-research init` 从 workspace scaffold（3 个 blocking prompts + 11 template copy + 27 skill copy + DuckDB init + git init + baseline backtest）改为 vibe-trading 风格的 5 步 TTY 向导。

### 5.2 两条 init 路径

| 路径 | 入口 | UI | 写 |
|---|---|---|---|
| A（显式 CLI） | `quantnodes-research init` | Rich prompt_toolkit | `~/.quantnodes/strategy_research/.env` |
| B（首启自动） | `quantnodes-research`（TTY + 无 `.env`） | prompt_toolkit | `~/.quantnodes/strategy_research/.env` |

### 5.3 文件影响

| 文件 | 操作 |
|---|---|
| `cli/__init__.py` | 删 scaffold helpers (69-186)，重写 cmd_init → cmd_run_onboarding |
| `cli/_auto_onboard.py` | 新建：_maybe_run_onboarding + _migrate_legacy_env |
| `cli/__main__.py` | 顶插 _maybe_run_onboarding 调用 |
| `tests/test_init_wizard.py` | 新建：25 个测试用例 |
| `tests/test_autoresearch.py` | fixture 改为手动构造（config.yaml + templates + DuckDB seed） |
| `tests/test_cli_init.py` | 删 TestRenderTemplate + TestCmdInit，保留 TestCmdEvaluate |
| `tests/test_pr3_fixes.py` | 删 TestCmdInitNoBaseline + TestCmdInitConfigYAML |

### 5.4 向后兼容

- `~/.strategy-research/.env` 在首次 wizard 时 silent 迁移到 `~/.quantnodes/strategy_research/.env`（旧文件不删）
- `--help` 输出 "Run the credentials wizard that writes ~/.quantnodes/strategy_research/.env"
- workspace scaffold（config.yaml / .prompts/ / .skills/ / strategies/）**不再由 init 创建**——留待 `autoresearch` 按需 lazy init
