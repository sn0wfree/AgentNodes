# Context 溢出修复：聊天无限迭代 + 行情数据进 prompt

## 事故报告

用户运行 Web UI 聊天（siliconflow / DeepSeek-V4-Flash，128K 窗口），发送
「先跑一次基线回测」后收到：

```
LLMError: unexpected status 400: {'code': 20015,
  'message': 'number of input tokens (1206470) has exceeded
              max_prompt_tokens (1048576) limit.', ...}
```

单次 LLM 请求 120 万 token，超过 1M 上限。会话 `76113c37-d4f7-489d-8a3a-7e08e7eb97f7`
的失败 attempt（`4f1f61bce4f6`，prompt="先跑一次基线回测"）metrics 累计
`input_tokens=5,073,571,250`（50 亿），为正常 attempt（1200-2200 万）的
200-400 倍。

## 根因链

### 1. `get_market_data` 必失败（import 路径错误）

`src/strategy_research/core/data_source/utils.py:126`：

```python
from ...utils.market_detection import detect_market
```

相对 `strategy_research.core.data_source` 上三级 → `strategy_research.utils`（不存在）。
实际模块在 `strategy_research.core.utils.market_detection`（需四级 `....`）。

运行日志反复出现：

```
get_market_data failed ... ModuleNotFoundError: No module named 'strategy_research.utils'
```

→ agent 每次获取行情都收到错误，被迫反复重试。

### 2. 聊天模式无限迭代（默认值错误）

`src/strategy_research/api/session/service.py:122`：

```python
max_iterations: int = 9999999999,
```

`send_async`（`chat.py:480`）调用 `send_message` 时不传 `max_iterations`，
使用该默认值。日志证实：

```
[EXEC] running agent_loop model=None max_iter=9999999999
```

→ agent 无限迭代（AgentLoop 每轮全量重发 messages + system prompt）。

### 3. no-progress 兜底失效

`_tool_call_hash` 含参数（`loop.py:118-122`）。LLM 每次重试换 codes/日期/loader，
参数不同 → hash 不同 → `no_progress_window` 永不触发 → 无法自我终止。

### 4. 数据放大（修复 import 后的潜在必然事故）

`get_market_data` 返回全量 OHLCV JSON（`loop.py:982` 完整 content 进 messages），
`import_data` 又要求 LLM 把 `data=<完整行情>` 作为参数回传。一次成功获取
30 码 × 500 行 ≈ 75 万 token，加上回传参数 → 单个迭代即 1.5M，必爆。

## 修复设计

### 第一批：Fix2 止血（限制聊天迭代）

聊天模式迭代上限从 `9999999999` → 配置化（默认 50）。

- `LLMConfig` 加 `max_iterations`（chat，默认 50）与 `agent_max_iterations`
  （agent，默认 9999）字段，从 llm.json 读取。
- `service.send_message` 默认改为 50（防御，不应无限大）。
- `send_async` 显式传 `cfg.max_iterations`。
- TUI/autoresearch 路径保持现状（显式传值，不受影响）。

目的：即使 `get_market_data` 仍失败，50 轮硬上限保证单次请求不会累积到超限。

### 第二批：Fix1 + Fix3' 根治

**Fix1**：`data_source/utils.py:126` `...` → `....`，解除 `get_market_data` 必失败。

**Fix3'**：行情数据不再进 prompt。

1. `get_market_data`：fetch 后每码写入 loader_cache parquet
   （`~/.quantnodes-research/loader_cache/<key>.parquet`），返回**摘要**：
   行数 / 日期区间 / 首末收盘 / 平均成交量 / 前 5 行预览 / cache_key。
   不再返回全量 OHLCV。
2. 新增 `commit_market_data` 工具：按 cache_key 读取 parquet → 写入 DuckDB
   `price_data`（复用 `core/data_import.import_dataframe` / `save_price_data`）。
3. CLI `import --source cache`：同一合并逻辑的手动入口。
4. `import_data` 保留为手动/外部数据入口，描述与错误提示更新。

数据流（修复后，第三批已合并）：

```
get_market_data(codes=[...], persist=True) → fetch → 写 DuckDB → 返回摘要
   ↓ （行情直接入库，不进 prompt）
run_backtest(strategy_name=...) → 从 DuckDB 读
```

## 明确不做

- compact 兜底加强（Fix4）：Fix2+Fix3' 后风险足够低，缓办。
- 失败 attempt 的 50 亿 metrics 畸变（疑似流式 usage 重复计）：独立问题。
- `detect_market` 对纯数字代码（如 `510300`）误判为 FRED/macro：既有边界
  问题，A 股代码正常带 `.SH/.SZ` 后缀可避免；记录待后续修。

## 实现落地

### 第一批（已合入）

- `LLMConfig.max_iterations=50`（chat）、`agent_max_iterations=9999`，
  从 llm.json 读取；`send_message` 默认 50；`send_async` 显式传
  `cfg.max_iterations`。

### 第二批

- **Fix1**：`data_source/utils.py` 用绝对导入
  `strategy_research.core.utils.market_detection`（原相对导入指向
  不存在的 `strategy_research.utils`）。
- **Fix3'**：
  - `get_market_data`：fetch 后每码 `cache_put` 写 loader parquet，返回
    `{cached: {code: cache_key}, summary: {code: {rows, first_close,
    last_close, avg_volume}}, preview: {code: [5行]}}`，不含全量 data。
  - 新增 `commit_market_data` 工具：按 cache_key 读 parquet →
    `INSERT OR REPLACE INTO price_data`。
  - CLI `import --source cache --codes A,B --cache-keys k1,k2 --strategy X`。
  - `import_data` 保留（手动/外部数据），描述与错误提示更新为推荐
    `get_market_data → commit_market_data`。

### 测试

`tests/test_market_data_cache_flow.py`：摘要不含全量、preview 限 5 行、
persist 写入 DuckDB、persist=False 不写库、strategy 分区、幂等、缺 workspace
报错、`commit_market_data` 已退役（不在注册表）。

### 第三批（已合入）：合并 get_market_data + commit_market_data

Fix3' 的"两步 + parquet cache_key 中转"经评审后合并为一步：

- `get_market_data(persist=True)` 直接写 DuckDB `price_data`（复用
  `db.save_ohlcv_to_db`），返回 `{summary, preview, persisted,
  persisted_rows, strategy_name, meta}`——全量行情仍不进 prompt。
- `commit_market_data` 工具退役（移除注册、CLI `--source cache`、错误提示引用）。
- `persist=False` 仅查看不写库；`INSERT OR REPLACE` 幂等。
- 所有 `next_step` 字段删除（无第二工具；业界 ACI 惯例为返回值保持纯净）。

### 合并判定依据

为什么把两步合并为一步（判定框架，可复用于其它工具合并评估）：

| 判据 | 通过条件（本次成立） | 若失败（不合并） |
|------|---------------------|-----------------|
| 1. 契约耦合 | `commit` 无独立价值，只是把 `get` 的产物搬进 DuckDB | 第二步有独立决策/审批价值 |
| 2. 可及性一致 | `commit_market_data` 不在任何角色白名单，`get_market_data` 在 → 角色模式下数据流断链 | 两步在所有调用场景都可用 |
| 3. 先例/复用 | 引擎 `auto+duckdb` 已是 `fetch → save_ohlcv_to_db` 一步实现，核心函数可复用 | 无先例，两步逻辑差异大 |
| 4. 安全不变量 | 合并后返回值仍只含 summary+preview，全量行情不进 prompt（context 安全约束未破坏） | 合并会让全量数据进 prompt |
| 5. 代价可逆 | `INSERT OR REPLACE` 幂等、重取可覆盖、`persist=False` 逃生 | 不可逆、需人工确认 |

**业界背景**：Anthropic《Building Effective Agents》ACI 范式——工具返回值应保持
纯净数据，引导放 tool description / workflow；唯一允许"返回值带下一步"的是状态
依赖的契约式场景（对标 API `next_cursor`）。两步结构正是该特例，但与其让 LLM
记住"必须再调 commit"，不如让第一步一步到位，消除契约场景本身。



## 相关文件

- `src/strategy_research/core/llm/config.py`
- `src/strategy_research/api/session/service.py`
- `src/strategy_research/api/routers/chat.py`
- `src/strategy_research/core/data_source/utils.py`
- `src/strategy_research/core/agent/builtin_tools/data_tools.py`
- `src/strategy_research/core/agent/builtin_tools/__init__.py`
- `src/strategy_research/cli/__init__.py`
- `src/strategy_research/cli/commands/core_commands.py`
