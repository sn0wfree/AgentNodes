# 分层归属评审（2026-11-05）

> **目的**：减法批次 1–9 完成后，对全仓库做一次性分层归属评审，
> 确定每个模块**应该在哪一层**，并据此排定后续迁移计划。
>
> **触发**：逐点修补模式下，每次改动只解决当下暴露的问题，
> 遗漏会累积（本轮就发现了 L4 里 1,385 行从未被清理的量化代码）。
>
> **方法**：graphify 全量重扫 + AST 门禁交叉验证 + 领域标识符评分。

---

## 0. 结论摘要

| 判定 | 数量 | 处置 |
|---|---:|---|
| **L4 含量化领域实现** | 8 文件 / 1,385 行 | **归档**（生产零消费者）✅ 已执行 |
| **L4 的 DataStore Protocol 含量化语义** | 1 文件 / 291 行 / 24 方法 | **瘦身**（保留抽象，砍量化方法）|
| **L4 errors.py 的量化错误类** | 2 类 | **改名或移除** |
| **L1 有状态模块** | 12 文件 / 4,954 行 | **单独排期**（不在本轮）|
| **L3 含研究语义** | 2 文件 / 155 行 | **清理**（第二批）|
| **大模块（归层正确，仅需拆分）** | 7 文件 | **不动** |
| **经复核无问题** | 340 文件 | — |

**评审覆盖**：379 个模块，**33 个可疑 → 9 个真问题**（其中 1 个由人工复核补出，
见 §2.1 补注）。

---

## 1. 评审方法与工具选择

### 1.1 为什么不用 graphify 作为主判据

项目自己的文档已记录（`scripts/README.md` §工具选型规则）：

| 度量 | 模块顶层 import | 函数体内 import |
|---|---:|---:|
| graphify AST 抽取 | 1,166 / 1,171 = **99.6%** | 627 / 865 = **72.5%** |

graphify 对 **lazy import 有 ~27% 系统性盲区**——而 lazy import 恰恰是层违规最常见的藏身处。
历史上还出现过「门禁 46 vs graphify 46」的**数值巧合**，逐条比对后交集仅 37/46 = 80.4%。

**因此定策**：graphify 做**交叉验证**（拓扑、god nodes、调用关系），
层归属的主判据是 AST 门禁（`scripts/arch/layer_matrix.py`）。

### 1.2 graphify 重扫（必要步骤）

评审前必须重扫：仓库里现存的 `graphify-out/graph.json` 生成于 **2026-11-04 12:17**，
早于批次 4–9 的全部改动，且仍包含 `apps/study` / `apps/goal` / `apps/alpha_zoo` /
`GoalStore` / `HypothesisRegistry` 等**已删除**的节点。

```bash
D:\tools\python312\python.exe -m graphify update . --force --no-cluster
```

结果：**50,089 节点 / 89,450 边**。

> 注：`update` 是增量合并，**不会删除**已删代码的节点（`--force` 只跳过 manifest 门禁）。
> 若需完全干净的图须 `extract --force` 全量重扫。评审以 AST 门禁为准，故不影响结论。

### 1.3 评分维度（v1 → v2 的修正）

**v1 用「业务词密度」打分，结果 72 个可疑、假阳性极高。**

根因：**`strategy` 一词在两个语境里同名**——
`kernel/agent/strategy/` 指的是 **loop 策略模式**（`ReActStrategy` / `MinimalStrategy` /
`ValidatorStrategy`），`foundation/utils/strategy_engine.py` 指的是**量化策略引擎**。
词频无法区分。

**v2 改用三层判据**（按「硬事实 → 语义 → 辅助」排序）：

| 判据 | 类型 | 权重 | 说明 |
|---|---|---:|---|
| **跨层出边方向** | 硬事实 | ×12/条 | 上行边 = 确定错误 |
| **领域标识符** | 语义 | L4 ×10+6/个 · L3 ×2+2/个 | AST 提取的**函数/类名**，须命中「动词+领域名词」或「领域名词+实体后缀」 |
| **状态信号** | 硬事实 | L1 +15 | 持连接 / 锁 / 全局单例 |
| 业务词密度 | 辅助 | ×0 | **降为参考**（歧义太大） |

**领域标识符规则**：

- 规则 1（动词 + 领域名词）：`run_backtest` · `detect_market` · `save_ohlcv` ·
  `calculate_turnover`
- 规则 2（领域名词 + 实体后缀）：`BacktestResult` · `BacktestConfig` ·
  `StrategyEngine` · `BacktestCallbacks`

**明确排除的歧义**：单独的 `strategy`（loop 策略模式）、`market` 在非量化语境
（`git.py` 的 "market" 实为误匹配）。

**结果：72 → 33 个可疑**，人工复核后 **9 个真问题**。

### 1.4 已知盲区（v2 规则的局限）

**下划线前缀 + 单领域词的函数名会漏判。**

`foundation/utils/metrics.py` 的 17 个模块级函数全是 `_sharpe` / `_sortino` /
`_calmar` / `_max_drawdown` / `_ann_return` / `_info_ratio` / `_var_cvar` …——
既不满足「动词 + 领域名词」也不满足「领域名词 + 实体后缀」，规则未命中，
**但它是纯量化代码**。最终由人工复核补出（§2.1 补注）。

改进方向（下轮评分脚本）：规则 1 增加「`_` 前缀 + 领域名词」的单项匹配，
或在 L4 层额外检查「模块内出现的领域词种类数 ≥ 3 且无对应 L4 语义」。
本次靠人工复核兜住，但**不应假设自动评分没有漏**。

> **已于 2026-11-05 落地**：该补丁成为评分器的**规则 3**
> （`scripts/arch/score_layer_attribution.py`），固化后 `metrics.py`
> 得分 80 分（8 个被归档量化文件里第 2 高），**不再需要人工兜底才命中**。
> 但「不应假设自动评分没有漏」这条仍然成立——见 §5 的实测与 §1.4 的遗留风险。

**因此本次评审的流程要求**：自动评分只用于**缩小范围**，
最终结论必须人工复核每个被标红的模块，并**主动扫一遍 L4 全清单**
（L4 是「零业务」红线，最值得人工过目，且只有 67 个文件）。

---

## 2. 评审结果

### 2.1 第一类：L4 含量化领域实现（8 文件 / 1,385 行）

违反「L4 = 零业务知识」红线。这批是**减法批次 1–9 的漏网之鱼**：
回测域（批次 4）、数据域（批次 5）都删了，但 `foundation/utils/` 里的配套实现留了下来。

| 文件 | 行 | 领域标识符 | 生产消费者 |
|---|---:|---|---:|
| `foundation/utils/market_detection.py` | 97 | `detect_market` · `detect_submarket` · `detect_market_batch` | **0** |
| `foundation/utils/backtest_engine.py` | 190 | `run_backtest` · `BacktestCallbacks` | **0** |
| `foundation/utils/backtest_utils.py` | 182 | `calculate_turnover` · `calculate_turnover_cost` | 3（同目录兄弟）|
| `foundation/utils/backtest_metrics.py` | 314 | —（内容全是 sharpe/sortino/calmar）| 2（测试）|
| `foundation/utils/strategy_engine.py` | 194 | `StrategyEngine` · `BaseStrategy` | **0** |
| `foundation/utils/backtest_config.py` | 81 | `BacktestConfig` · `CostConfig` | 7（同目录兄弟 + 测试）|
| `foundation/utils/backtest_models.py` | 86 | `BacktestResult` · `TradeRecord` | 6（同目录兄弟 + 测试）|
| **`foundation/utils/metrics.py`** | **241** | **见下方补注** | **0**（仅被同批归档文件引用）|

> **补注（评审后追加）**：`metrics.py`（241 行）**未被 v2 自动评分标红**，
> 但人工复核确认它是**纯量化代码** —— 17 个模块级函数全是绩效指标：
> `_sharpe` · `_sortino` · `_calmar` · `_max_drawdown` · `_ann_return` ·
> `_ann_vol` · `_info_ratio` · `_var_cvar` · `_win_rate` ·
> `_profit_loss_ratio` · `_resample_monthly` · `_max_monthly_loss` 等，
> 公共入口是 `extended_metrics`（「17 项业绩指标」）。
>
> **评分漏判的原因**：这些函数名都是**下划线前缀 + 单个领域词**
> （`_sharpe`），既不满足「动词 + 领域名词」也不满足「领域名词 + 实体后缀」，
> 因此规则未命中。**这是 v2 规则的已知盲区**，记录在案（§1.3）。
>
> 它的唯一生产消费者是被一并归档的 `backtest_engine.py` / `strategy_engine.py`，
> 因此**归入同一批归档**。归档后 `foundation/utils/` 剩余
> `bg_proc.py`(316) · `io_utils.py`(93) · `token_utils.py`(49) · `ids.py`(21)，共 479 行，
> 全部零业务语义。

**消费者核查**（`scripts/…/check_consumers` 式全仓库 import 扫描，
范围 `src/` + `tests/` + `scripts/`）：

> **这 8 个文件在 `src/` 里零生产消费者。** 引用它们的只有彼此（`backtest_engine`
> 引用 `backtest_config`/`backtest_utils`/`backtest_models`）和**测试文件**。
>
> 补充核查（2026-11-05 执行 A1 时做）：`foundation/storage/data_store.py` 的
> `save_ohlcv` / `save_backtest_result` / `save_nav_history` 等量化方法
> **自行实现**，不经由本批 8 个文件——因此 `DataStore` 的瘦身（A2）不受本批影响。

**处置：归档到 `testcase/golden/foundation/utils/`**（与批次 4 删回测域的处置一致），
**已执行**（见 §7）。

**连带归档的测试（14 文件 / 2,849 行）**——它们引用 `foundation.utils` 的
（现已归档的）模块：

```
test_backtest_config.py         163L    test_backtest_engine.py        263L
test_backtest_metrics.py        370L    test_backtest_metrics_edge.py  105L
test_backtest_models.py         263L    test_backtest_utils.py        242L
test_market_detection.py        188L    test_strategy_engine.py       347L
test_metrics.py                 428L    test_bg_proc.py               200L
test_bg_shell_and_toolsets.py    32L    test_io_utils.py              116L
test_token_utils.py              66L    test_typed_ids.py              66L
```

> ⚠️ **归档前需逐个确认**：其中 `test_bg_proc.py` / `test_bg_shell_and_toolsets.py` /
> `test_io_utils.py` / `test_token_utils.py` / `test_typed_ids.py` 测的是
> **留存**模块（`bg_proc` / `io_utils` / `token_utils` / `ids`），
> 它们引用 `foundation.utils` 只是因为包路径匹配，不一定测被归档的部分。
> 这 5 个文件应**保留**。真正专属被归档代码的测试是
> `test_backtest_*.py`(4) · `test_market_detection.py` · `test_strategy_engine.py` ·
> `test_metrics.py`，共 **9 文件 / 2,369 行**。

**归档前的逐个确认结果**（2026-11-05 执行 A1 时完成）：

| 测试文件 | 实际 import | 判定 |
|---|---|---|
| `test_bg_proc.py` | `foundation.utils.bg_proc` | **留存** |
| `test_bg_shell_and_toolsets.py` | `kernel.agent.toolsets` · `foundation.utils.bg_proc` | **留存** |
| `test_io_utils.py` | `foundation.utils.io_utils` | **留存** |
| `test_token_utils.py` | `foundation.utils.token_utils` | **留存** |
| `test_typed_ids.py` | `foundation.utils.ids` · `interfaces.api.session.service` · `kernel.agent.loop` | **留存** |

> 5 个文件合计 480 行；9 + 5 = 14 文件 / 2,849 行，与 §2.1 清单一致。
> 5 个留存文件的 import 路径**本来就正确**（指向留存模块），
> 因此**无需改动任何 import**——「只改 import」是当时的预案，实测不需要改。

**归档后 `foundation/utils/` 剩余**（479 行，全部零业务语义）：

```
bg_proc.py      316   后台进程管理
io_utils.py      93   IO 工具
token_utils.py   49   token 估算
ids.py           21   ID 生成
```

### 2.2 第二类：L4 的 DataStore Protocol 含量化语义（1 文件 / 291 行）

`foundation/storage/data_store.py` 定义 `DataStore` Protocol，**24 个方法**里
绝大多数是量化语义：

```
init · get_connection · save_ohlcv · load_price_data · load_ohlcv_data
save_factor_data · load_factor_data · register_factor · list_factors
remove_factor · save_backtest_result · list_backtest_results
get_backtest_result · save_weight_history · load_weight_history
save_nav_history · load_nav_history · cache_validation
list_validation_cache · get_last_import_date · update_import_meta
update_data_fingerprint · get_data_fingerprint
```

**但它不能归档**——它有 **6 个真实消费者**：

| 消费者 | 用途 |
|---|---|
| `kernel/agent/loop.py:463` | `get_default_data_store()` → 填 `ToolContext.data_store` 槽位 |
| `apps/data_store_registry.py` | `from foundation.storage.data_store import DataStore` |
| `foundation/storage/__init__.py` | 导出 `DataStore` / `get_default_data_store` / `register_default_data_store` |
| `interfaces/api/app.py` | `from apps.data_store_registry import get_store` |
| `interfaces/cli/__main__.py` | 同上 |
| `tests/test_tools_capability.py` | 测试 |

**问题不是「它在 L4」，而是「它的抽象太宽」**：一个叫 `DataStore` 的存储抽象，
一半方法是量化专用（`save_ohlcv` / `save_backtest_result` / `save_nav_history`）。

**处置：瘦身**——保留通用存取（`init` / `get_connection` / 指纹 / 导入元数据），
量化方法移到 `apps/` 侧的具体实现或删除。当前框架只用 `get_connection`
（loop 的槽位），其余 23 个方法**当前无生产调用方**。

### 2.3 第三类：L4 errors.py 的量化错误类

```
StrategyResearchError(Exception)   ← 根类，名字也是「策略研究」，建议改 AgentError
├── ConfigError
├── ProviderError
├── SessionError
├── BacktestError       ← 量化专属，随回测域一起移除
├── GoalError           ← 随 goal 域一起移除
├── SwarmError
└── NotFoundError
```

`BacktestError` / `GoalError` 是**批次 2/4 删掉对应业务后留下的孤儿**。
`StrategyResearchError` 这个根类名本身也带旧项目痕迹。

**处置**：`BacktestError` / `GoalError` 移除或归档；根类改名（保留别名兼容）。

### 2.4 第四类：L1 有状态模块（12 文件 / 4,954 行）—— 单独排期

L1 的判据是「无状态 · 无编排 · 只做协议转换」。以下模块持连接 / 锁 / 单例，
**归层判错**（不是依赖方向错——门禁查不出同层问题）。

| 文件 | 行 | 状态信号 |
|---|---:|---|
| `interfaces/api/session/service.py` | 1,449 | `Queue(` |
| `interfaces/api/session/web_session_db.py` | 770 | `sqlite3.connect` |
| `interfaces/api/session/backfill_event_log.py` | 371 | `sqlite3.connect` |
| `interfaces/api/session/projection_store.py` | 354 | `sqlite3.connect` · `self._conn` · `threading.Lock` |
| `interfaces/api/session/store.py` | 352 | `sqlite3.connect` · `self._conn` |
| `interfaces/api/session/projector.py` | 312 | `sqlite3.connect` |
| `interfaces/cli/theme.py` | 311 | `threading.Lock` |
| `interfaces/api/user_db.py` | 276 | `sqlite3.connect` · `self._conn` · `threading.Lock` |
| `interfaces/api/session/loop_events.py` | 277 | `threading.Lock` |
| `interfaces/api/routers/admin_users.py` | 246 | `sqlite3.connect` |
| `interfaces/cli/tui/widgets/status_header.py` | 137 | `self._conn` |
| `interfaces/cli/halt.py` | 63 | `threading.Lock` |

其中 `interfaces/api/session/` 下 8 个文件共 **4,246 行**是主体——即
**`interfaces/api/session/` 整体下沉到 `kernel/session/`** 的范围（L3）。

**为什么不在本轮做**：
1. 这是事件溯源的核心链路（projector / store / service 互相咬合），
   一次搬 4,246 行 + 改 65 个测试文件，风险远高于 L4 那批「零消费者」的文件；
2. 前置条件已具备——**步骤 1 已断开全部 L3→L1 反向依赖**，
   搬家后 L3→L3 同层合法，方向正确；
3. 需要单独排期、独立验证。

**风险已识别的两个「同层互引」**（搬完后变成同层耦合，不违反门禁但需注意）：
- `kernel/agent/event_store.py`（L3）↔ `interfaces/api/session/projector.py` → 搬后同层
- `kernel/agent/memory_manager.py`（L3）↔ `interfaces/api/session/web_session_db.py` → 搬后同层
两者已通过 `ProjectionSink` / `schema_port` 两个端口解耦，**不产生新的 import 方向**。

### 2.5 第五类：L3 含研究语义（2 文件 / 155 行）

| 文件 | 行 | 问题 |
|---|---:|---|
| `kernel/workflow/validator.py` | 130 | `AgentValidator` 内含 `_validate_factor_analyst` / `_validate_portfolio` —— 研究域角色校验 |
| `kernel/workflow/grounding.py` | 25 | `GroundingProvider.fetch_market_data` —— 行情数据获取 |

**处置**：研究语义的具体规则移到 `apps/`，`kernel/workflow/` 只保留通用的
「节点校验」与「数据获取端口」抽象。

### 2.6 第六类：大模块（归层正确，仅需拆分）

以下模块**归层正确**，评分高只是因为体量大。不在本次评审处置范围：

```
interfaces/cli/onboard.py             1,005L
interfaces/api/routers/chat.py           967L
interfaces/cli/tui/app.py                738L
foundation/llm/model_catalog.py          735L
foundation/llm/openai_client.py          707L
foundation/llm/config.py                 706L
interfaces/cli/__init__.py               661L
```

### 2.7 经复核无问题（340 文件）

其余 340 个模块归层正确。特别确认：

- `kernel/agent/strategy/*`（7 文件）—— 「strategy」指 loop 策略模式，**不是量化策略**，
  归 L3 正确（与 `agent` 同层，是 AgentLoop 的策略扩展点）。
- `kernel/agent/toolsets.py`（200L）—— 工具集配置机制，无业务语义。
- `foundation/git.py` / `foundation/errors.py` 的词频命中为**误报**。

---

## 3. 评审时的门禁基线

评审全程以下门禁保持全绿（`L2→L1 · L3→L1 · L3→L2 · L4→*` 全为 0）：

```
L1    238      8    115     52
L2      0      8     39      1
L3      0      0    389     54
L4      0      0      0     87
```

> 这是 2026-11-05 步骤 1（断反向依赖）达成的**首次全层归零**。
> 本评审的所有结论都建立在「门禁已绿」的前提上——**门禁管依赖方向，
> 不管归层正确性**，两者互补。

**执行 A1 后的门禁基线**（2026-11-05 实测）：

```
L1    238      8    115     52
L2      0      8     39      1
L3      0      0    389     54
L4      0      0      0     77
```

> 只有 `L4→L4` 从 87 降到 77（归档 8 个文件带走 10 条 L4 内部 import）。
> **六条上行违规仍全为 0**——归档没有引入任何新的层间依赖方向。
> 层体量随之变化：L4 `67 文件 / 11,415 行` → `59 文件 / 10,030 行`，
> 总计 `379 / 75,330` → `371 / 73,945`（架构图已回写并复核通过）。

---

## 4. 迁移计划（分步，每步完整验证）

| 批次 | 内容 | 规模 | 风险 | 验证 | 状态 |
|---|---|---|---|---|---|
| **A1** | 归档 L4 的 8 个量化文件 + 专属 9 个测试 | 1,385 + 2,369 行 | **低**（生产零消费者）| 门禁 + 全量回归 | ✅ **已执行** |
| **A2** | `DataStore` Protocol 瘦身 + `errors.py` 清理 | 291 + 170 行 | 低 | 门禁 + 全量回归 | 待执行 |
| **B1** | L3 的 `kernel/workflow/validator.py` + `kernel/workflow/grounding.py` 研究语义清理 | 155 行 | 低 | 门禁 + 定向回归 | 待执行 |
| **C**（单独排期）| `api/session/` 下沉 `kernel/session/` | 4,246 行 / 65 测试 | **高** | 门禁 + 全量回归 + 事件溯源专项 | 待排期 |

**顺序理由**：A1 是「删无人用的代码」，风险最低、收益最直接，先做；
A2/B1 紧随其后形成完整的 L4/L3 清理；C 因为触及事件溯源核心链路，
等前面三批都验证绿、且仓库处于已知良好状态时再单独做。

---

## 5. 本评审的可复用工具

评分脚本的两处关键设计（供后续复查复用）：

1. **领域标识符须用 AST 提取**，不能用词频——`strategy` 的歧义导致 v1 误报率过半。
2. **跨层出边与状态信号是硬事实**，优先于任何语义启发式。

**脚本已固化到 `scripts/arch/score_layer_attribution.py`**（评分器，恒退出 0，
不进 pytest 门禁——它的输出必须人工复核后才算数）：

```bash
python scripts/arch/score_layer_attribution.py                  # 扫 src/agentnodes
python scripts/arch/score_layer_attribution.py --root testcase/golden   # 扫任意包根
python scripts/arch/score_layer_attribution.py --json           # 机器可读
```

固化时相对本评审的临时脚本做了三处修正：

| 修正 | 原临时脚本 | 现在 |
|---|---|---|
| **规则 2 的 CamelCase 拆分** | `BacktestConfig` 不拆词 → **漏判** | `_words()` 同时拆 snake_case 与 CamelCase |
| **规则 1 的匹配位置** | 只看第 2 段 | 动词与领域词**同在一个名字里**即可（`calc_bars_per_year` 能命中）|
| **规则 3（§1.4 漏判补丁）** | 临时脚本没有 | 已实现：「`_` 前缀 + 领域词」单项匹配 |

固化后的实测（`--root testcase/golden`，即本评审归档的那批量化文件）：

```
96  backtest_utils.py     80  metrics.py         64  backtest_engine.py
64  backtest_models.py    48  backtest_config.py 48  backtest_metrics.py
48  market_detection.py   32  strategy_engine.py
```

**8 个被归档的量化文件全部被命中**（含规则 3 救回的 `metrics.py`），
另有 `ic_utils.py`（更早批次归档）也被正确命中。

在当前 `src/agentnodes` 上跑，评分器把评审的**全部真问题**排在前列：

```
192  foundation/storage/data_store.py   ← A2 目标
 32  foundation/errors.py                ← A2 目标（BacktestError / GoalError）
 15  interfaces/api/session/*.py 等 7 个  ← L1 有状态模块
 12  kernel/workflow/validator.py        ← B1 目标
  8  kernel/workflow/grounding.py        ← B1 目标
```

其余低分项（4–8 分）是**已知误报**（`StrategyFactory` / `get_compaction_metrics`
等），已在脚本 docstring 里列明，人工复核时直接排除。

> **仍然不能假设自动评分没有漏**——§1.4 的教训成立。
> 领域词表外的专有命名不会被命中，因此 **L4 层仍要人工扫全清单**。

---

## 6. 未做的事（明确记录）

| 项 | 原因 |
|---|---|
| 全函数级评审（500+ 函数）| 粒度过细；模块级评审已覆盖归层决策所需信息 |
| graphify 作为主判据 | 对 lazy import 有 ~27% 盲区，且历史上出现过数值巧合 |
| `api/session/` 本轮搬家 | 触及事件溯源核心链路，需单独排期（§4 C）|
| 大模块拆分 | 归层正确，不属本次评审范围（§2.6）|

---

## 7. 执行记录

### 7.1 A1 — 归档 L4 的量化残留（2026-11-05）

**动作**：把 §2.1 的 8 个量化文件与其专属 9 个测试从产品代码树移出，
归档进 `testcase/`（减法批次的既定处置：代码不丢弃，转金标准快照）。

```
src/agentnodes/foundation/utils/  →  testcase/golden/foundation/utils/
    market_detection.py  backtest_engine.py  backtest_utils.py
    backtest_metrics.py  strategy_engine.py  backtest_config.py
    backtest_models.py   metrics.py                       （8 文件 / 1,385 行）

tests/                          →  testcase/tests/
    test_backtest_config.py    test_backtest_engine.py
    test_backtest_metrics.py   test_backtest_metrics_edge.py
    test_backtest_models.py    test_backtest_utils.py
    test_market_detection.py   test_strategy_engine.py
    test_metrics.py                                    （9 文件 / 2,369 行）
```

**归档目标遵循 `testcase/README.md` 的既有约定**：
`golden/` 镜像原路径存源码快照，`tests/` 存被移除的测试（原样保留、不被 pytest 收集）。
`pyproject.toml` 的 `testpaths = ["tests"]` 使 `testcase/` 天然排除在收集外；
`check_dangling_imports.py`（扫 `src` + `tests`）与 `check_path_literals.py`
（扫 `src` + `tests` + `scripts`）**都不扫 `testcase/`**，
因此归档文件内部对已移出模块的引用不会触发门禁。

**归档后 `foundation/utils/` 剩余**（479 行业务内容 + 4 行 `__init__.py`）：

```
bg_proc.py      316   后台进程管理（run_bg / shell_command / 任务注册）
io_utils.py      93   原子写 + dotenv 解析
token_utils.py   49   chars → token 估算
ids.py           21   SessionId / AttemptId
```

四个模块全部零业务语义，L4「零业务知识」红线在 `foundation/utils/` 内**已达成**。

**验证结果**：

| 检查 | 结果 |
|---|---|
| `compileall`（`src/agentnodes` + `tests`）| ✅ 通过 |
| `check_dangling_imports.py --layers` | ✅ 0 悬空；上行违规全 0 |
| `check_path_literals.py` | ✅ 0 问题 |
| `pytest tests/test_layer_matrix.py test_scripts_arch.py test_c1_inventory_gate.py` | ✅ 37 passed |
| `sync_architecture_diagram.py --write` + 复核 | ✅ 图与代码一致 |
| 全量回归 `pytest tests -n 4 --dist loadgroup` | ✅ 见 §7.2 |

**层体量变化**：L4 `67 → 59` 文件 / `11,415 → 10,030` 行；
总计 `379 → 371` 文件 / `75,330 → 73,945` 行；`L4→L4` 边 `87 → 77`。

### 7.2 顺带确认（未改动，只记录）

- `.shard-timings.json` 里这 9 个测试的条目**未清理**——该文件是 CI 分片的
  **测量缓存**（由 `scripts/collect_shard_timings*.py` 重建），
  且已有 222 条来自更早批次的同类陈旧条目。`scripts/pytest_shard.py`
  只对**实际收集到的文件**查表，陈旧条目不会被查询，故无害。沿用既有做法不动它。
- `tests/baselines/pr-lane-nodeids.json` 的 nodeid 基线里**不含**这 9 个文件，
  归档不影响 PR lane 的基线比对。
- `docs/design/` 与 `docs/archive/` 里若干历史设计文档提到
  `utils.market_detection` / `utils.strategy_engine` 等路径——那些是
  **当时的架构记录**（部分还写着早已不存在的 `agentnodes.core.*`），
  按文档分层约定归 `design`/`archive`，**不改写历史**。
