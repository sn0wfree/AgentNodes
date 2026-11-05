# B4 Step 4-5：旧会话栈退役 — 2026-10-30

> 上游：`docs/archive/2026-10/stage-c1-b4-dataplane-design-20261028.md` §4.3 Step 4-5
> 前置：`73351b3d`（Step 1 事件化）、`1b7e905e`（Step 3 消费方迁移）

---

## 0. 范围

删掉整个 `core/session/` 旧栈（除 `unified.py`），并退役对应门禁。

| 文件 | 处置 | 依据 |
|---|---|---|
| `core/session/db.py` (605 行) | **删** | 唯一引用者 `__init__.py` / `manager.py` 一并删 |
| `core/session/manager.py` | **删** | 零生产调用（仅 `__init__.py` 导入） |
| `core/session/metrics.py` | **删** | 只被 `db.py` 与 `__init__.py` 使用（D3 已退役 metrics 端点） |
| `core/session/rate_limiter.py` | **删** | 只被 `db.py` 使用 |
| `core/session/models.py` | **删** | 只被 `db.py` / `manager.py` 使用 |
| `core/hooks/bundled/session_memory.py` | **删** | `SessionMemoryHook` 零生产构造 |
| `core/session/unified.py` | **保留** | Step 3 的新读路径 |

---

## 1. 删除前置核实（实测）

### 1.1 同名不同物，必须区分

| 疑似引用 | 实际指向 | 结论 |
|---|---|---|
| `core/backtest.py:18` `from .db import ...` | **`core/db.py`**（行情 DuckDB） | 无关，不动 |
| `core/config_runner.py:17` `from .db import` | 同上 | 无关 |
| `core/data_import.py` / `data_readiness.py` | 同上 | 无关 |
| `core/portfolio/cli.py:14` `from .metrics import` | **`core/portfolio/metrics.py`** | 无关 |
| `core/utils/backtest_engine.py:19` `from .metrics import` | **`core/utils/metrics.py`** | 无关 |
| `api/session/models.py` | **`api/session/models.py`**（另一套 dataclass） | 无关 |

**这是本步最大的风险点**：`from .db import` 在 `core/` 下指 `core/db.py`，
在 `core/session/` 下才指旧栈。已逐处核实。

### 1.2 真实引用（删除后必须零命中）

| 被删文件 | 生产引用 | 测试引用 |
|---|---|---|
| `session/db.py` | `session/__init__.py`、`session/manager.py` | `test_session.py`、`test_session_memory.py` |
| `session/manager.py` | `session/__init__.py` | `test_session.py` |
| `session/metrics.py` | `session/db.py`、`session/__init__.py` | `test_session_metrics.py` |
| `session/rate_limiter.py` | `session/db.py`、`session/__init__.py` | `test_session_rate_limiter.py` |
| `session/models.py` | `session/db.py`、`session/manager.py` | — |
| `hooks/bundled/session_memory.py` | `hooks/bundled/__init__.py` | `test_session_memory.py` |

---

## 2. 门禁退役（必须同提交）

`tests/test_architecture_gates.py` 有两条门禁直接依赖旧栈：

| 门禁 | 现状 | 处置 |
|---|---|---|
| `test_messages_table_written_only_by_projection_or_guarded_persist` | 白名单含 `core/agent/memory_manager.py`（Step 1 后它已不再直写 messages） | 白名单移除该项——门禁收紧 |
| `test_legacy_session_db_stays_separate_from_unified_db` | 直接 import `core.session.db` | **删除**（文件没了，约束自然消失） |

**为什么第一条要收紧而非删除**：`messages` 单写者约束仍然有效（现在真的只有一个写者了）。删掉它是放松，不是清理。

---

## 3. 测试处置（契约 §2.2：允许缩水但必须显式）

| 测试文件 | 处置 | 理由 |
|---|---|---|
| `test_session.py` | 删（含 `TestSessionManager`） | 全测 `SessionDB`/`SessionManager` |
| `test_session_memory.py` | 删 | 全测 `SessionMemoryHook` |
| `test_session_metrics.py` | 删 | 全测已退役的 `MetricsLogger` |
| `test_session_rate_limiter.py` | 删 | 全测旧栈 `RateLimiter` |
| `test_session_list_helper.py` | **保留** | 测新 unified 读路径 |

**这是 B4 里最大的一次测试缩水**（4 个文件）。必须在提交信息里写明，
并在同一提交内重冻 `c1_inventory`。

---

## 4. 迁移替代说明（这些能力去哪了）

| 旧能力 | 新归属 |
|---|---|
| `SessionDB.list_sessions` | `core/session/unified.py:list_sessions` |
| `SessionDB.get_session/get_messages` | `unified.get_session/get_messages` |
| `SessionDB.search_messages`（FTS5） | `unified.search_messages`（LIKE，与线上一致） |
| `SessionDB.delete_session` | `unified.delete_session` |
| `MetricsLogger` | `EventStore.health_report()` |
| `RateLimiter` | 无替代——全仓零生产使用（实测） |
| `SessionMemoryHook`（Markdown 归档） | 无替代——零生产构造 |

`RateLimiter` 和 `SessionMemoryHook` 是「零调用即删」，符合契约 §6.5 规则 2。

---

## 5. 验证

| # | 断言 |
|---|---|
| T1 | `core/session/db.py` 删除后全仓零 import |
| T2 | `unified.*` 13 条测试全绿 |
| T3 | 门禁收紧后仍绿（单写者） |
| T4 | 全量 PR 道失败集合 ⊆ 基线 |
| T5 | `c1_inventory` 重冻后净删除可解释 |

回归门：`test_architecture_gates + test_session_list_helper + test_memory* +
test_cli_session_cmd + test_admin_metrics + test_mcp + test_tui*`。
