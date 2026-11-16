# 会话生命周期下沉到 kernel（方案，R-014）

> **性质**：实施设计（**未实施**）· **日期**：2026-11-16 · **决策来源**：2026-11-16 用户拍板「选项 1」
> **上游**：架构图 `docs/architecture-layered.html` 的 D1/D2/D3 三条架构债；`docs/design/framework-and-principles-v1.2.md` §4 能力集 C7/C8。
> **本文档只出方案，不动代码。** 每一批的「验收判据」都写明了哪个断言会从红变绿或从绿变红。

---

## §0 结论摘要

**要做什么**：把会话生命周期（创建 / 存储 / 投影 / 归档 / 删除）的 owner 从
`interfaces/api/session/`（L1 接口层）搬到 `kernel/session/`（L3 平台层），
让 `interfaces/api/session/` 退化为薄适配层（HTTP 形状 + 序列化）。

**为什么**：会话是**领域概念**，不是接口层的私有实现。当前 owner 放在 L1 导致三件事：

1. `kernel/` 想管会话只能**绕过服务层直连 SQLite** —— 于是有了 `kernel/session/unified.py` 这个门面（D1）
2. 非 web 通道（CLI / MCP / TUI）**拿不到完整会话能力**，只能列/搜/删（D1）
3. **「删除」在两条路径上语义相反**（web 软删 `archived=1` vs CLI 硬删 `DELETE FROM`）（D2）

**关键判断（与直觉相反）**：这不是「搬文件」。实测发现
`kernel/agent/*` **已经反向依赖** `interfaces/api/session`（`chat_loop.py` /
`memory_manager.py` / `event_store.py` 都 import 了 `SessionService` / `Projector`）。
所以本方案的真问题是：**打断一个已成型的循环，并把会话抽成内核级公共设施**。

**规模**：待迁 **14 文件 / 5,774 行**（不是最初估的 2,924 —— 漏算了
`web_session_db.py` 770、`history_convert.py` 358、`backfill_event_log.py` 372 等）。

**不做**：不重写任何逻辑、不改数据库 schema、不改 API 形状、不动 `kernel/agent` 的行为。

---

## §1 现状实测（全部可追溯）

### 1.1 会话生命周期的八阶段与归属

| 阶段 | owner | 位置 | 备注 |
|---|---|---|---|
| 创建 | `SessionService` | `interfaces/api/session/service.py:89` | `create_session(session_id, title, user_id)` |
| 运行调度 | `SessionController` | `kernel/gateway/session_controller.py` | 准入+容量闸+身份签发+驱动引擎 —— **管 run 不管会话** |
| 消息存储 | `SessionStore` | `interfaces/api/session/store.py` | SQLite 持久化 Session/Message/Attempt |
| 状态投影 | `Projector` + `projection_handlers` | `interfaces/api/session/projector.py` · `projection_handlers.py`（791 行）| 从 event_log 派生状态，纯更新零 IO |
| 归档 | web 路由 | `interfaces/api/routers/web_session.py:313` | `archived=1` 软删，消息与 event_log 保留 |
| 删除 | **两处语义相反** | web `:302`（软）· `kernel/session/unified.py:168`（硬）| 见 D2 |
| 过期/淘汰 | **无** | — | 四组关键词全仓零命中；只有 `kernel/agent/cache.py` 的**消息 LRU 缓存**，不是会话。见 D3 |
| 只读浏览 | `kernel/session/unified.py` | 205 行 | 直连 SQLite 绕过服务层；CLI·MCP·TUI 用，web 零引用 |

### 1.2 待迁文件清单（14 文件 / 5,774 行）

| 行数 | 文件 | 迁入后归属 |
|---:|---|---|
| 1468 | `service.py` | `kernel/session/service.py` |
| 791 | `projection_handlers.py` | `kernel/session/projection_handlers.py` |
| 770 | `web_session_db.py` | `kernel/session/db.py`（**改名**，见 §3.2） |
| 372 | `backfill_event_log.py` | `kernel/session/backfill_event_log.py` |
| 358 | `history_convert.py` | `kernel/session/history_convert.py` |
| 354 | `projection_store.py` | `kernel/session/projection_store.py` |
| 312 | `projector.py` | `kernel/session/projector.py` |
| 278 | `loop_events.py` | `kernel/session/loop_events.py` |
| 226 | `models.py` | `kernel/session/models.py` |
| 206 | `projection_models.py` | `kernel/session/projection_models.py` |
| 129 | `part_accumulate.py` | `kernel/session/part_accumulate.py` |
| 126 | `trace_projection.py` | `kernel/session/trace_projection.py` |
| 353 | `store.py` | `kernel/session/store.py` |
| 31 | `web_session_state.py` | `kernel/session/web_state.py`（**改名**，见 §3.2） |

### 1.3 硬约束（本方案必须绕过的四道坎）

| # | 约束 | 实测 | 处置 |
|---|---|---|---|
| **K1** | **`store.py:120` 反向依赖 routers** | `from ..routers.web_session import _get_db, _row_to_message` | 迁入 kernel 后这条必须改 —— L3 不能 import L1。`_get_db` / `_row_to_message` 随 `web_session_db.py` 一起下沉（§3.2）|
| **K2** | **`kernel/agent/*` 已反向依赖 `interfaces/api/session`** | `chat_loop.py:7`、`memory_manager.py:112,517`、`event_store.py:45,62,221,252` | 迁入后 `kernel/agent` 改为 import `kernel/session` —— **同层自由**，K1–R-001 允许（§3.3）|
| **K3** | **`text_guard` / `event_v2` 反向依赖 session** | Q3 实测两项命中 | 同 K2：迁入后同层引用，无循环 |
| **K4** | **SQLite 连接管理的 WinError-32 flake 家族** | `web_session_db.py:7`「must close explicitly」· `store.py:57-60`「Historically this returned a raw sqlite3.connect handle」· `projection_store.py:5`「close() is explicit」 | 连接生命周期代码**必须随文件一起搬**，不得重写（§3.4）|

### 1.4 四通道的依赖形状

| 通道 | 位置 | 现状 | 迁移后 |
|---|---|---|---|
| **web** | `interfaces/api/routers/{chat,web_session,workflow}.py` + `app.py` + `container.py` + `dependencies.py` | 直接用 `SessionService` / `SessionStore` / `Projector`（33 + 18 + 16 处引用）| 改为 import `kernel/session`，**调用点不变**（§3.3）|
| **CLI** | `interfaces/cli/commands/session.py`、`slash_session.py` | 用 `kernel/session/unified.py` 的 5 个函数 | 改走 `kernel/session` 的服务层（§4.3）|
| **TUI** | `interfaces/cli/tui/app.py:229,260` | 同上 | 同 CLI |
| **MCP** | `interfaces/mcp/server.py:350,358` | 同上（函数内 import）| 同 CLI |

---

## §2 为什么不选其它方案

| 方案 | 否决理由 |
|---|---|
| **不动，只写 ADR 说明「生命周期归 L1」** | 成本低，但把 D1 的根因固化成设计：**非 web 通道永远拿不到完整会话能力**。而 CLI/MCP/TUI 恰恰是最需要它的一批 |
| **只把 `kernel/session/unified.py` 改成调服务层（不搬 owner）** | 治标：服务层还在 L1，`kernel/session` 依赖 L1 依然违规（`check_dangling_imports` 会红）|
| **整体搬到 `kernel/session/`（本方案）** | 唯一能让 L1 不再持有领域 owner 的做法 |

---

## §3 迁移设计

### 3.1 目标形态

```
kernel/session/                        L3 · 会话领域内核（新增 owner）
├── models.py              ← interfaces/api/session/models.py
├── store.py               ← interfaces/api/session/store.py       （消除 K1）
├── db.py                  ← interfaces/api/session/web_session_db.py
├── service.py             ← interfaces/api/session/service.py
├── projector.py           ← interfaces/api/session/projector.py
├── projection_models.py   ← interfaces/api/session/projection_models.py
├── projection_handlers.py ← interfaces/api/session/projection_handlers.py
├── projection_store.py    ← interfaces/api/session/projection_store.py
├── history_convert.py     ← interfaces/api/session/history_convert.py
├── loop_events.py         ← interfaces/api/session/loop_events.py
├── part_accumulate.py     ← interfaces/api/session/part_accumulate.py
├── trace_projection.py    ← interfaces/api/session/trace_projection.py
├── backfill_event_log.py  ← interfaces/api/session/backfill_event_log.py
├── web_state.py           ← interfaces/api/session/web_session_state.py
└── reader.py              ← kernel/session/unified.py（改写：走 store，不再直连 DB）

interfaces/api/session/                L1 · 薄适配层（只剩 HTTP 形状）
└── api_models.py          ← 新增：HTTP 请求/响应 DTO
```

### 3.2 三个改名/改写的理由

| 改名 | 原名 | 理由 |
|---|---|---|
| `db.py` | `web_session_db.py` | 原名带 `web_` 前缀，但它在 `kernel/` 里 —— **「web 会话库」这个语义在 L3 是错的**（它就是会话库）。改名同时消除「只有 web 用」的错觉 |
| `web_state.py` | `web_session_state.py` | 同上（`_shape_agents_for_frontend` 是前端形状，但那是 L1 的事 → 见下）|
| `reader.py` | `unified.py` | 原名 `unified` 无信息量。改后语义明确：**只读浏览** |

> **`web_state.py` 的归属有争议** —— `_shape_agents_for_frontend` 明显是前端形状，
> 属 L1。**本方案的处理**：该文件**不迁 kernel**，留在 `interfaces/api/session/`
> 作为适配层（它是 31 行，纯 DTO shaping）。§3.1 的目标树里它标了「改名」但
> 实际应**留在 L1** —— 实施时按此办，方案在 §6 已记为待修正项。

### 3.3 消除 K1–K3 的具体改法

| 约束 | 改法 |
|---|---|
| **K1** `store.py:120` import routers | `_get_db` / `_row_to_message` 随 `db.py` 下沉到 `kernel/session/db.py` → 改成 `from .db import _get_db, _row_to_message` |
| **K2** `kernel/agent/*` import L1 session | 改成 `from agentnodes.kernel.session import ...` —— **同层自由**，R-001 允许 |
| **K3** `text_guard` / `event_v2` 反向依赖 | 同 K2（同层）|
| **R-001 层级门禁** | 迁完后 `check_dangling_imports.py --layers` 必须仍报**上行违规 0**；新增的 `kernel/session → kernel/agent` 是同层，不触发 |

### 3.4 SQLite 连接管理：不重写

`db.py` / `store.py` / `projection_store.py` 的连接生命周期代码**逐字搬运**，
包括 `check_same_thread=False`、`close()` 幂等、`_flush_lock`（`projection_store.py:47`）。
**理由**：WinError-32 是本仓已踩过的坑（`projection_store.py:5` 的注释、
`store.py:57-60` 的「Historically this returned a raw sqlite3.connect handle」）。
重写连接管理 = 重新踩坑。

### 3.5 统一删除语义（解 D2）—— ⚠️ 有测试锁定，需先决策

目标：**只有一套删除语义**。

| 路径 | 现状 | 目标 |
|---|---|---|
| web `DELETE /session/{id}` | 软删（`archived=1`）| **保持软删**（SSE 与投影器依赖 event_log 继续可用）|
| CLI `session delete` | 硬删（`DELETE FROM`）| **改为软删**，与 web 一致；真硬删另设显式 `--purge` |

> ⚠️ **阻塞事实（2026-11-16 实测）**：CLI 的硬删语义**被测试锁定**：
>
> | 断言 | 位置 |
> |---|---|
> | `test_delete_session_removes_row_and_messages` | `tests/test_session_list_helper.py:155` |
> | `assert delete_session("s1", db) is True` | `tests/test_session_list_helper.py:164` |
> | `test_delete_session_keeps_session_row` | `tests/test_memory_dataplane_hotfix.py:98`（**语义相反**：这里断言「删消息但留会话行」）|
> | `test_delete_session_clears_messages` | `tests/test_memory_manager_comprehensive.py:178` |
>
> ⇒ **E1 批次不能直接改软删** —— 会撞上至少 3 处断言，且其中两处**期望的语义互不相同**
> （`test_session_list_helper` 期望整行删、`test_memory_dataplane_hotfix` 期望留行）。
> **E1 的正确做法**：
> 1. 先把这 3 处断言**读清楚各自在测什么**（三个不同的 `delete_session`：unified 门面 / memory_manager / dataplane）；
> 2. 若它们测的是**不同对象**（不是同一个 delete_session），则 CLI 的硬删改软删只影响其中一处 → 改那处断言 + 补一条「CLI 删除后仍可 GET」的断言；
> 3. 若它们测的是**同一个函数的不同预期**，说明当前语义本身就不一致 —— 这正是 D2 的证据，**先统一语义再迁**。
>
> **这一步必须人工判断，不能机械改。** E1 批次在 §6 缺口 3 关闭前不得动手。

---

## §4 分批实施（每批独立可回滚）

> **R-020**：删除类动作排最后；主体与删除分两个 commit。
> **R-015**：删任何接线对象前须满足四项（规范已定 / 接线完成 / 完整测试 / 审计能力）。

### 批次 A · 协议先行（不动行为）

| 步 | 动作 | 验收判据 |
|---|---|---|
| A1 | 新建 `kernel/session/` 骨架 + `__init__.py` 导出面（**空的 re-export**，从旧路径转发）| `from agentnodes.kernel.session import SessionService` 与旧路径 `from agentnodes.interfaces.api.session.service import SessionService` **返回同一个类对象**（`is` 相等）|
| A2 | 新增 `interfaces/api/session/api_models.py`（HTTP DTO）| 纯新增，无行为变化 |

**A 批不改任何调用点** —— 只建立「新旧路径同源」的双入口，让后续批次可以逐个切换。

### 批次 B · 主体迁移（`git mv` + import 改写）

| 步 | 动作 | 验收判据 |
|---|---|---|
| B1 | `git mv` 14 个文件到 `kernel/session/`（保留历史）| `git log --follow` 仍能追到原文件 |
| B2 | 改写所有 import：`agentnodes.interfaces.api.session.*` → `agentnodes.kernel.session.*` | `grep -rn "interfaces.api.session" src/` **零命中**（除 adapters 内有意保留）|
| B3 | 消除 K1：`store.py` 改 import `.db` | `check_dangling_imports.py --layers` 上行违规仍 **0** |
| B4 | `kernel/agent/*` 改 import 新路径（K2/K3）| 全量 pytest 0 failed |
| B5 | 同步 `scripts/arch/layer_matrix.py`（`kernel/session` 已是 L3，**无需登记** —— `agentnodes.kernel` 兜底）| 门禁绿 |

**B 批是风险最高的一批**：一次搬 5,774 行。建议 B1–B3 一个 commit，B4–B5 一个。

### 批次 C · 通道切换（四通道逐一）

| 步 | 动作 | 验收判据 |
|---|---|---|
| C1 | **web**：路由/容器/依赖改 import 新路径，**调用点不变** | `tests/test_api_*.py` 全绿 |
| C2 | **CLI**：`commands/session.py` + `slash_session.py` 改走服务层（不再用 `reader.py` 直查 DB）| CLI 会话测试全绿；**输出形状不变**（回归断言）|
| C3 | **TUI**：`cli/tui/app.py` 改走服务层 | TUI 测试全绿 |
| C4 | **MCP**：`mcp/server.py` 改走服务层（注意它是**函数内 import**）| MCP 测试全绿 |

**每步一个 commit**，任一步红就回滚那一步。

### 批次 D · 消除双路径

| 步 | 动作 | 验收判据 |
|---|---|---|
| D1 | `kernel/session/reader.py` 改为走 `store`（不再直连 SQLite）| 行为等价测试绿；`grep "sqlite3.connect" kernel/session/reader.py` 零命中 |
| D2 | CLI/MCP/TUI 改走服务层后，`reader.py` 只剩 web 无关的查询 —— 评估是否还需要它 | 若无调用方 → **删除**（R-020 末工序，独立 commit）|

### 批次 E · 删除语义统一（解 D2）

| 步 | 动作 | 验收判据 |
|---|---|---|
| E1 | CLI `session delete` 改软删（`archived=1`）| 新增断言：CLI 删除后 `GET /session/{id}` 仍可读、`list_sessions` 不含它 |
| E2 | （可选）真硬删另设 `--purge` | 新增断言 |

### 批次 F · 收尾

| 步 | 动作 | 验收判据 |
|---|---|---|
| F1 | `interfaces/api/session/` 只剩 `api_models.py`（+ 若 `web_state.py` 留此）| 目录 ≤ 2 文件 |
| F2 | 架构图回写（层体量变了：L1 减 5774 行、L3 增）| `sync_architecture_diagram.py --write` 后 `test_architecture_diagram_matches_code` 绿 |
| F3 | 能力集 C8 的「实现者」更新（`foundation/storage` → 加 `kernel/session`）| 架构图 tooltip 与 md §4.2 一致 |
| F4 | 更新 `framework-and-principles-v1.2.md` §4 与架构图 D1/D2 状态 | 文档与代码一致 |

---

## §5 回滚方式

| 批次 | 回滚 |
|---|---|
| A | 删两个新文件即可（无行为变化）|
| B | `git revert` 该 commit（B1–B3 一个、B4–B5 一个），或 `git revert -n <commit>` 后手工改回 import |
| C | 每步独立 commit → `git revert` 单步 |
| D | 同上 |
| E | 同上；**注意 E1 改变了用户可见语义** —— 若已有用户依赖 CLI 硬删，需先确认再上|
| F | 同上 |

**没有不可逆操作**：全部是 `git mv` + import 改写 + 新增，无 schema 变更、无数据迁移。

---

## §6 待修正项（本方案自身的已知缺口）

| # | 缺口 | 处置 |
|---|---|---|
| 1 | **§3.1 目标树把 `web_state.py` 放进了 kernel，但 §3.2 又说它该留在 L1** —— 自相矛盾 | 实施时以 §3.2 为准：**留在 L1**。§3.1 目标树需相应修正 |
| 2 | **`kernel/session/` 已存在**（`unified.py` + `__init__.py`）| 迁移时 `unified.py` → `reader.py` 是改名，`__init__.py` 需合并导出面 |
| 3 | ~~未核查 `tests/` 里对硬删/软删的断言~~ **已核查（§3.5）**：3 处断言，且**期望语义互不相同** | **E1 批次阻塞**，需人工判断三个 `delete_session` 各自在测什么 |
| 4 | **未评估 `interfaces/api/session/web_session_db.py` 的 FTS 索引迁移** | 该文件有 `_ensure_fts_table` / `_add_column` / `_run_schema_migrations`（370+ 行）—— 搬文件不搬 schema，理论上安全，但需在 B 批后核对 FTS 表仍工作 |
| 5 | ~~未量化四个通道的回归测试覆盖~~ **已核查**：web 52 文件 / CLI 27 / TUI 39 / MCP 18 —— **四通道全覆盖充分** | C 批每步都有对应测试，无需先补 |
| 6 | **`kernel/session/unified.py` 被 2 个测试文件直接 import**（`test_session_list_helper.py` 9 处 / `test_memory_dataplane_hotfix.py`）| D1 批次改 `reader.py` 实现后，这 2 个测试文件的 import 路径也要改（`unified` → `reader`）|
| 7 | **FTS 表由 `web_session_db.py` 独家维护**（`_ensure_fts_table` / `_add_column`）| 迁到 kernel 后由 `kernel/session/db.py` 维护 —— 需确认没有 L1 侧的代码直接调它（`grep -rn "ensure_fts\|_ensure_schema" src/`）|

---

## §7 验收判据总表（什么会从红变绿 / 从绿变红）

| # | 判据 | 方向 |
|---|---|---|
| 1 | `grep -rn "interfaces.api.session" src/` 命中数 5774 → 0 | 红→绿 |
| 2 | `check_dangling_imports.py --layers` 上行违规 | **绿→绿**（不能变红）|
| 3 | `check_kernel_nouns.py` 业务名词命中 | **绿→绿** |
| 4 | 全量 pytest | **绿→绿**（0 failed 不变）|
| 5 | `batch_gate.cmd` | **绿→绿**（ALL GREEN）|
| 6 | 会话能力对 CLI/MCP/TUI 可达 | 红→绿（当前不可达）|
| 7 | 「删除」语义一致（web/CLI 同为软删）| 红→绿 |
| 8 | 会话过期/淘汰存在 | 红→绿（**D3，本方案不含，另议**）|
| 9 | 层体量：L1 减 5774 行、L3 增 | 数字变（架构图需回写）|

---

## §8 明确不做的事

1. **不重写任何业务逻辑** —— 逐字搬 + 改 import
2. **不改数据库 schema / 不做数据迁移** —— 表结构不变
3. **不改 HTTP API 形状** —— 端点、请求体、响应体全部不变
4. **不引入新依赖**
5. **不做会话过期/淘汰**（D3）—— 单独议题
6. **不改 `kernel/agent` 的循环行为** —— 只改 import 路径
7. **不动 `kernel/session/unified.py` 的函数签名**（除 D2 的内部实现）

---

## §9 证据索引

| 断言 | 复现命令 |
|---|---|
| 待迁 14 文件 / 5,774 行 | `python tmp/probe/migration_feasibility.py` |
| K1 `store.py:120` 反向依赖 routers | `grep -n "routers.web_session" src/agentnodes/interfaces/api/session/store.py` |
| K2 `kernel/agent` 已反向依赖 session | `grep -rn "interfaces.api.session" src/agentnodes/kernel/agent/` |
| K4 WinError-32 flake 家族 | `grep -n "WinError" src/agentnodes/interfaces/api/session/*.py` |
| 四通道依赖形状 | 同上脚本的 Q4 段 |
| 上行违规恒为 0 | `python scripts/arch/check_dangling_imports.py --layers` |
