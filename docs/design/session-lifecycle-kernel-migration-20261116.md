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

> ⚠️ **2026-11-16 修正**：本节初版曾写「`kernel/agent/*` 已反向依赖 `interfaces/api/session`」——
> **该判断错误**。那几处命中（`chat_loop.py:7`、`memory_manager.py:112`、`event_store.py:45`）
> 全是**注释里的文字**，不是 import。`event_store.py:62` 的注释本身写着
> 「lazy import 接口层的 Projector —— 那是 L3→L1 上行违规」，说明**代码有意避免了它**。
> AST 复核结论：**kernel 层对 `interfaces.api.session` 的真实 import = 0 处**，
> 门禁 `L3→L1 = 0` 印证。
> ⇒ **迁移难度显著低于初版估计** —— 没有已成型的循环要打断。

| # | 约束 | 实测 | 处置 |
|---|---|---|---|
| **K1** | **`service.py` / `store.py` 反向依赖 routers** | `service.py:99,176` → `routers.web_session`（`_get_db` / `auto_title_session`）；`store.py:120` → `routers.web_session`（`_get_db` / `_row_to_message`）。**`service.py` 的 `routers.chat` 两处已随 LLM 收敛批消除**（`56bce1b0`），剩余 **4 处**，全部指向 `routers.web_session` | 迁入 kernel 后必须改 —— L3 不能 import L1。`_get_db` / `_row_to_message` 随 `web_session_db.py` 一起下沉到 `kernel/session/db.py`（§3.2、§3.3 B3）|
| **K2** | **L3 → L1 是门禁禁止的方向** | `layer_matrix.ORDER = [L1,L2,L3,L4]`，`L3→L1` 判违规 | ⇒ **`kernel/session` 不能反向 import L1**。「双入口同源」只能**反向做**：实现下沉到 kernel，L1 留 re-export 垫片（L1→L3 合法）。见 §3.1 修订 |
| **K3** | **待迁文件对 kernel 内部的依赖共 17 处** | `service.py`→`kernel.agent.{compact,event_store,chat_loop,text_guard}`（8）· `history_convert`→`compact`（2）· `projector`/`projection_handlers`→`events.event_v2`（3）· `trace_projection`→`event_store`+`event_v2`（2）· `web_session_db`→`memory_manager`（1）· `loop_events`→`prompt_builder`（1）| **全部 L3→L3，迁入后仍合法** ✅ 不构成障碍 |
| **K4** | **SQLite 连接管理的 WinError-32 flake 家族** | `web_session_db.py:7`「must close explicitly」· `store.py:57-60`「Historically this returned a raw sqlite3.connect handle」· `projection_store.py:5`「close() is explicit」 | 连接生命周期代码**必须随文件一起搬**，不得重写（§3.4）|

> **2026-11-16 状态更新**（LLM 配置收敛批 `56bce1b0` 之后）：
>
> - ✅ **R1（`_build_llm_config` 归属）已解** —— 收敛批把 `service.py` 的 2 处
>   `from ..routers.chat import _build_llm_config` 改为直接 `LLMConfig.load()`，
>   `service.py` 对 `routers` 的反向依赖由 **4 处减到 2 处**（只余 `routers.web_session`）。
> - ✅ **R2（`_invalidate_projection` 依赖 FastAPI Request）不存在** ——
>   实测全仓只有 `routers/web_session.py` 自身定义并调用它（web 的 DELETE 端点内部用），
>   **没有任何待迁文件 import 它**。方案 §6 缺口 4/5 的原估计偏保守。
> - ⇒ **剩余反向依赖只有 4 处**（`service.py` 2 + `store.py` 2），全部指向
>   `routers.web_session` 的 `_get_db` / `auto_title_session` / `_row_to_message`。

### 1.3.1 零依赖文件（批次 A 的实际范围）

AST 筛选：**无任何项目内 import**（只依赖 stdlib）的文件有 4 个 —— 它们可以**零改写**先下沉：

| 文件 | 行 | 依赖 | 说明 |
|---|---:|---|---|
| `models.py` | 226 | 仅 stdlib（dataclasses/datetime/enum/typing/uuid）| 会话数据模型（Session/Message/Attempt）|
| `projection_models.py` | 206 | 仅 stdlib | 投影数据模型（ProjectedSession/…）|
| `part_accumulate.py` | 129 | 仅 stdlib | assistant part 累积 |
| `web_session_state.py` | 31 | 仅 stdlib | 前端状态 shaping |

> 这 4 个是「协议/数据」层 —— **会话领域的词汇表**。把它们先放进 kernel，
> 后续批次搬「行为」（service/store/projector）时词汇表已在正确位置。

### 1.3.2 真实 import 依赖全景（AST 判定，修正后）

| 方向 | 真实 import |
|---|---|
| 谁 import `interfaces.api.session` | **仅 2 处**（都在 L1 内）：`interfaces/api/routers/admin_users.py:212`、`interfaces/api/routers/chat.py:761` → `...session.web_session_db` |
| 谁 import `kernel.session` | **10 处**（全在 L1）：`mcp/server.py:350,358` · `cli/commands/session.py:25,51,87,107` · `cli/commands/slash_session.py:56,87` · `cli/tui/app.py:229,260` |
| 待迁文件 → interfaces | **5 处**（K1）|
| 待迁文件 → kernel 内部 | **17 处**（K3，全 L3→L3）|

> ⇒ **L1 是唯一需要「改调用点」的层**，且只有 2 处 import 需要改。
> CLI/MCP/TUI 那 10 处 import 指向的是 `kernel.session.unified`（已经是 L3）——
> **它们不需要改 import 路径**，只需在批次 D 改「直查 DB」为「走服务层」。

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

### 3.1 目标形态（**双入口的方向已修正**）

> **修正（2026-11-16）**：初版写「`kernel/session/__init__.py` 从 L1 旧路径转发」——
> **不可行**：`kernel`(L3) import `interfaces`(L1) 是门禁禁止的上行方向（K2）。
> **正确做法是反过来**：实现先下沉到 kernel，**L1 留 re-export 垫片**
> （L1→L3 是合法方向）。这样：
> - 老调用点 `from agentnodes.interfaces.api.session.models import Attempt` **完全不改**（垫片转发）
> - 新调用点 `from agentnodes.kernel.session.models import Attempt` 也能用
> - **两者返回同一个类对象**（垫片是 re-export，不是复制）

```
kernel/session/                        L3 · 会话领域内核（owner）
├── models.py              ← interfaces/api/session/models.py            [批次 A]
├── projection_models.py   ← interfaces/api/session/projection_models.py [批次 A]
├── part_accumulate.py     ← interfaces/api/session/part_accumulate.py  [批次 A]
├── db.py                  ← interfaces/api/session/web_session_db.py   [批次 B]
├── store.py               ← interfaces/api/session/store.py           [批次 B]
├── service.py             ← interfaces/api/session/service.py         [批次 B]
├── projector.py           ← interfaces/api/session/projector.py       [批次 B]
├── projection_handlers.py ← interfaces/api/session/projection_handlers.py [批次 B]
├── projection_store.py    ← interfaces/api/session/projection_store.py [批次 B]
├── history_convert.py     ← interfaces/api/session/history_convert.py  [批次 B]
├── loop_events.py         ← interfaces/api/session/loop_events.py      [批次 B]
├── trace_projection.py    ← interfaces/api/session/trace_projection.py [批次 B]
├── backfill_event_log.py  ← interfaces/api/session/backfill_event_log.py [批次 B]
├── reader.py              ← kernel/session/unified.py（改写：走 store，不再直连 DB）[批次 D]
└── web_state.py           ← interfaces/api/session/web_session_state.py [留在 L1，见下]

interfaces/api/session/                L1 · 薄适配层
├── __init__.py            ← 新增：统一 re-export 面
├── models.py              ← 垫片：re-export kernel.session.models      [批次 A]
├── projection_models.py   ← 垫片：re-export kernel.session.projection_models [批次 A]
├── part_accumulate.py     ← 垫片：re-export kernel.session.part_accumulate [批次 A]
├── api_models.py          ← 新增：HTTP DTO（批次 A）
└── web_session_state.py   ← 留在 L1（前端形状，L2 语义不适用）—— **不下沉**
```

> **`web_session_state.py` 留在 L1**：31 行 `_shape_agents_for_frontend`，是**前端 DTO shaping**，
> 下沉到 kernel 会让 L3 知道前端形状（方向反了）。初版目标树把它放进 kernel 是错的，
> 已修正（§6 缺口 1）。

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
> ✅ **人工判断完成（2026-11-16，E 批前置）**：**4 处断言锁的是 3 个不同函数，
> 相互零矛盾**——
>
> | 函数 | 测试 | 语义 | 判定 |
> |---|---|---|---|
> | `reader.delete_session`（CLI/MCP/TUI 门面）| list_helper ×2 | 硬删（行+消息）| **唯一要改的**——E1 把它改为软删 |
> | `SQLiteStore.delete_session`（memory_manager 后端）| dataplane_hotfix ×2 | 清消息**保锚行** | **不动**——这是 `/clear` 的路径（D7 修复：锚行是 FK 锚，删行会让 /clear 后投影 flush 撞 FK），与「删除会话」是两回事 |
> | `InMemoryStore.delete_session`（降级后端）| comprehensive:178 | 清 messages | **不动**——降级后端没有 sessions 行概念 |
>
> ⇒ §3.5 列的两种可能性属于第一种：「测的是不同对象」。**真正的语义分歧只有一个**
> ——web `DELETE /{id}` 软删 vs CLI `session delete` 硬删（这正是 D2 债本身）。
> E1 可以动手。

### E1 的对齐目标（web 侧现状，实测）

| 操作 | web 现状 | reader（CLI/MCP/TUI）现状 | E1 后 |
|---|---|---|---|
| 删除 | `UPDATE archived=1`（保消息+event_log，投影/SSE 继续可用）| `DELETE` 行+消息 | 同 web：`archived=1` |
| list | `AND archived=0`（`include_archived` 开关）| 无 archived 过滤 | `WHERE archived=0` |
| search | `AND s.archived=0`（L582，JOIN 过滤）| messages LIKE 无过滤 | JOIN sessions 过滤 archived |
| GET 单会话 / 消息 | 不过滤（删后仍可读）| 不过滤 | 不变 |
| `/clear`（memory_manager）| 保锚行清消息（D7）| — | 不动 |

> 测试改写：`test_delete_session_removes_row_and_messages` →
> `test_delete_session_archives_row`（archived=1 / get_session 仍返回 /
> get_messages 仍可读 / list 不含）；`test_delete_missing_session_returns_false`
> 保留；新增 list 过滤与 search 过滤断言。E2（`--purge` 真硬删）另行。

---

## §4 分批实施（每批独立可回滚）

> **R-020**：删除类动作排最后；主体与删除分两个 commit。
> **R-015**：删任何接线对象前须满足四项（规范已定 / 接线完成 / 完整测试 / 审计能力）。

### 批次 A · 协议先行（零依赖文件下沉 + 双入口同源）

> **A 批范围已按实测收窄**：只下沉 **4 个零项目内依赖的文件**（§1.3.1），它们是会话领域的
> **词汇表**（数据模型 / part 累积），不含任何行为。这样 A 批能做**真正的双入口**：
> 实现下沉到 kernel，**L1 留 re-export 垫片**（L1→L3 合法，K2），老调用点零改动。

| 步 | 动作 | 验收判据 |
|---|---|---|
| A1 | `git mv` 4 个零依赖文件 → `kernel/session/`（保留历史）| `git log --follow` 仍能追到原文件 |
| A2 | L1 原路径放**垫片**（re-export，不是复制）| `from agentnodes.interfaces.api.session.models import Attempt` 与 `from agentnodes.kernel.session.models import Attempt` **是同一个对象**（`is` 相等）|
| A3 | `kernel/session/__init__.py` 扩展导出面 | `from agentnodes.kernel.session import Attempt` 可用 |
| A4 | 新增 `interfaces/api/session/__init__.py` 统一 re-export 面 | 从统一面能取到全部会话公开名 |
| A5 | 新增 `interfaces/api/session/api_models.py`（HTTP DTO）| 纯新增，无行为变化 |
| A6 | 加「双入口同源」契约测试 | 老路径与新路径的**类对象身份一致**（逐个符号断言）|

**A 批不做**：不碰 `service.py` / `store.py` / `projector.py`（有依赖，B 批再说）。

> **垫片的形态**：只写 `from ... import *` 会丢 `__all__` 控制 —— 应显式列出符号，
> 或用 `from ... import X as X` 逐个转出（保留 `__module__` 与类型检查器可见性）。

---

### 批次 B · 主体迁移 —— 按**依赖簇**拆成三个子批

> **2026-11-16 重排**：原计划「一次搬 14 个文件」不可行，实测发现三个硬约束：
>
> 1. **包内相对 import 形成一个簇**：`service → store → projector → {projection_store,
>    projection_handlers}`、`loop_events → service`、`service → {history_convert}`。
>    搬任何文件都会让留在 L1 的消费方 `from .X import` 断链 —— **除非**给 L1 留垫片
>    （A 批已验证该模式可行）。相对簇根是 `service`（1,471 行），它是最后搬的。
> 2. **`web_session_db.py` 导入 `fastapi`**（L19 + L622/L624 两处 `HTTPException`）——
>    而 **kernel / foundation 对 fastapi 的导入是 0 个文件**（实测）。
>    直接搬 = 内核首次引入 web 框架。**必须先重构**，见 B2 前置。
> 3. 6 个叶子的消费方共 **27 处**（含 18 处在 tests/），全部靠 L1 垫片保持零改动。

#### B1 · 叶子下沉（5 文件 / 2,001 行 · 同 A 模式）

**搬**：`history_convert.py`(358) · `projection_handlers.py`(791) · `projection_store.py`(354) ·
`trace_projection.py`(126) · `backfill_event_log.py`(372) —— 5 个**无出向相对依赖**的叶子。

**不搬**：`web_session_db.py`（fastapi，见 B2）· `web_session_state.py`（前端 DTO，留 L1，§3.2）·
`{service, store, projector, loop_events}`（相对依赖簇，见 B3）。

| 步 | 动作 | 验收判据 |
|---|---|---|
| B1.1 | `git mv` 5 个文件到 `kernel/session/` | `git log --follow` 仍能追到原文件 |
| B1.2 | 每个文件在 L1 放 **re-export 垫片**（显式列符号，同 A）| 27 处消费方**零改动**；`from ...session.X import Y` 与 `from ...kernel.session.X import Y` 是同一对象 |
| B1.3 | 扩展 `tests/test_session_kernel_migration.py` 的 `SUNK_MODULES` | 契约测试覆盖 5 个新模块（`is` 相等 + `__module__` 指向 kernel）|
| B1.4 | 这 5 个文件搬到 kernel 后，它们的 `from .projection_models import` 等**解析到 kernel/session 的真身**（A 批已迁）| 无需改写 —— 同包相对引用自动对齐 |

> **B1 与 A 的关键差异**：A 搬的是「被消费方」，B1 搬的是「消费者」。
> A 搬完后 L1 的相对 import 指向垫片（L1→L3 合法）；B1 搬完后**它们的相对 import
> 指向 kernel 内的真身** —— 同样合法，因为 `projection_models` 等已在 kernel。

#### B2 · `web_session_db` → `kernel/session/db.py`（**已拍板 2b**）

> **2026-11-16 拍板：选项 2b**（用户确认）。

**决策依据（实测）**：`web_session_db.py` 770 行里只有 **3 行**与 fastapi 相关：

| 行 | 内容 |
|---|---|
| L19 | `from fastapi import HTTPException` |
| L622 | `raise HTTPException(status_code=404, detail="Session not found")` |
| L624 | `raise HTTPException(status_code=403, detail="Forbidden")` |

这三行全部在 `_fetch_session_owned`（L615-625，**11 行**）内 —— 其余 **15 个函数 / 759 行零框架依赖**。

**为什么选 2b 而不是 2a**：`_fetch_session_owned` 做的是**HTTP 鉴权语义**
（404/403 状态码 + IDOR 保护），不是会话存储。把它上移到路由层比在 kernel 里
发明一套领域错误 + 在 L1 catch 更短、更对位。2a 要新建两个异常类 + 改 5 个错误处理
分支，收益只是「错误类型更抽象」，而它本来就不该在存储层。

**零调用点改动的证据**：`_fetch_session_owned` 全仓 15 处跨文件引用**全部**写成
`from .web_session import _fetch_session_owned`（chat / goal / permission / schedule /
workflow 五个路由），web_session.py 自身的 5 处调用在同一文件内。**没有任何地方**
从 `session.web_session_db` 直接 import 它 ⇒ 上移后调用点全部不变。

| 步 | 动作 | 验收判据 |
|---|---|---|
| B2.1 | `_fetch_session_owned`（11 行）移入 `routers/web_session.py`；该文件补 `import sqlite3` | `test_iteration_budget_wiring.py:76` 对 `routers.web_session._fetch_session_owned` 的 patch 仍生效 |
| B2.2 | `git mv web_session_db.py → kernel/session/db.py`（改名 `db`，理由见 §3.2）；删 `from fastapi import HTTPException` | kernel 侧 fastapi 导入数仍为 **0**（新增断言）|
| B2.3 | L1 原路径 `web_session_db.py` 改为 **re-export 垫片**（17 个符号，**不含** `_fetch_session_owned`）| 29 处老 import 零改动；`is` 相等 |
| B2.4 | 契约测试加 `RENAMED = {"db": "web_session_db"}`（kernel 名与垫片名不同）| `test_shim_reexports_same_objects["db"]` 绿 |
| B2.5 | **8 个测试文件的 patch 点改指向实现模块**（见下方「隐患」）| 全量 pytest 绿 |

> **为什么垫片不转出 `_fetch_session_owned`**：它已住在 `routers/web_session.py`，
> 垫片若要转出就得 `session/web_session_db.py → routers/web_session.py`，
> 而后者正 import 前者 —— **循环 import**。它也不再属于「DB 层」的导出面。

**⚠️ 隐患（B2 的真实成本，不是搬文件）**：8 个测试文件用
`monkeypatch.setattr(_wsdb, "_get_db_path", lambda: db_path)` 重定向 DB 路径
（`test_backfill_message_parts` / `test_chat_send_sync_run_traversal` /
`test_chat_slash_commands` / `test_compact_unpack` / `test_message_parts_dual_write` /
`test_message_parts_read_path` / `test_trace_ownership` / `test_event_log_schema`）。

垫片是 **re-export**：对垫片属性赋值**只在该模块上新建一个名字**，不会改变
kernel 模块的全局名 —— 而 `_get_db()` 是在 kernel 模块里解析 `_get_db_path` 的。
⇒ 这些 patch 会**静默失效**（测试仍绿，但写进了真实用户库）。
修法：这 8 个文件的 `_wsdb` 改指 `agentnodes.kernel.session.db`（各 1 行）。
`test_message_parts_read_path.py:26-31` 的注释已经预告了这件事。

**顺带修正一处死代码**：`tests/conftest.py:357-359` 的 thread-local 连接清理块
当前**从未生效** —— 它 `getattr(_ws_mod, "_db_thread_local", None)`，而 router
的 re-export 清单里从来没包含 `_db_thread_local`（恒为 `None`）。B2 顺势让它指向
`kernel.session.db._db_thread_local`。这不是顺手重构：K4 明令连接必须显式关闭，
清理失效正是 WinError-32 flake 的来源。

**不搬**：`schema_port` 注册逻辑保留（`kernel/session/db.py` import 时注册
`_ensure_schema`）。B2 后 L3 已可直接 import 它，端口退化为可选层 —— 但移除端口是
F 批的事，本批不动（`test_memory_manager_comprehensive.py:221` 还依赖它）。

#### B3 · 相对依赖簇（4 文件 / 2,414 行 · 必须整簇一起搬）

> **2026-11-16 B2 后复测更新**：`_get_db` / `_row_to_message` 已就位
> `kernel/session/db.py`，K1 的消除条件成熟。簇实际行数 2,414（service 1,471 +
> store 353 + projector 312 + loop_events 278）。

**簇成员**：`service.py`(1,471) · `store.py`(353) · `projector.py`(312) · `loop_events.py`(278)

```
service ──→ store ──→ projector ──→ {projection_store, projection_handlers}  (B1 → kernel)
   │                   │
   ├→ loop_events       └→ .projection_models  (A → kernel)
   ├→ history_convert  (B1 → kernel)
   └→ part_accumulate  (A → kernel)

loop_events ──(TYPE_CHECKING)──→ service   ← 运行时无环（L11-13 在 if TYPE_CHECKING）
```

| 步 | 动作 | 验收判据 |
|---|---|---|
| B3.1 | `git mv` 4 文件（**同 commit**，缺一即断链）| `git log --follow` |
| B3.2 | L1 垫片（4 个）+ `auto_title_session` 在 router 留 re-export | container / dependencies / chat + ~40 测试文件的 import **零改动** |
| B3.3 | **消除 K1（3 处）**：`service.py:99` 与 `store.py:120` 的 `_get_db` / `_row_to_message` 改 `from .db import`；`auto_title_session`（28 行）随簇下沉 `kernel/session/service.py` | L1 簇文件对 `routers.web_session` 的 import = **0**；`grep routers src/agentnodes/kernel/session/` = 0 |
| B3.4 | 契约测试覆盖 4 个新模块 | `is` 相等 + `__module__` 指向 kernel |

**`auto_title_session` 的归属（B2 后实测）**：定义在 router（L175-202）但
**router 自身零调用**——唯一生产调用方是 `service.py:176`（send_message 首条
用户消息自动起标题），3 处测试从 router import。它是**会话域逻辑**（不是 HTTP
形状），随簇下沉。router 留一行 re-export（L1→L3 合法），测试零改动。

**patch/reload 点清单（B2 教训的 B3 版）**：

| 点 | 性质 | 处置 |
|---|---|---|
| `test_iteration_budget_wiring.py:125` | `patch.object(SessionService, "_run_test_script")` —— **类级** patch | 无需改：垫片 re-export 同一个类对象，patch 类属性直达真身 |
| `test_phase2_data_integrity.py:275` | `patch.object(SessionService, "_convert_messages_to_history")` —— 同上 | 无需改 |
| `test_chat_timeout.py:39-44` | `importlib.reload(svc_mod)` + 读模块级 `_CHAT_ATTEMPT_TIMEOUT` —— **模块级 reload** | **必须改**指 `kernel.session.service`：reload 垫片只会重跑垫片的 import 语句（从 sys.modules 取已加载的 kernel 模块，不重执行顶层），env 驱动的常量不会重新解析 |

**垫片符号清单**（顶层定义 + service 尾部的 W-split re-export）：

| 模块 | 顶层定义 | 另需转出（文件内 re-export，AST 推导不含 import，须显式列）|
|---|---|---|
| service | `SessionService`, `_QUEUE_LIMIT`, `_CHAT_ATTEMPT_TIMEOUT`, `_friendly_error_text`, `_utc_now_iso`, `logger` | `MAX_HISTORY_CHARS` + history_convert 9 名 + part_accumulate 7 名（L1420-1440）|
| store | `SessionStore`, `_row_to_attempt`, `_utc_now_iso`, `logger` | — |
| projector | `Projector`, `logger` | — |
| loop_events | `_build_attempt_metrics`, `_LoopEventForwarder`, `_bootstrap_workspace`, `_PLAN_READONLY_TOOLS`, `_plan_mode_allowed_tools`, `_thinking_instructions`, `_resolve_loop_prompt`, `logger` | — |

> **K4 提醒**：store 的连接生命周期（`store.py:57-60` 注释所述 raw-handle 历史）
> 逐字搬运，不重写。

**⚠️ B3 实施中触发的名词门禁清理（预期外的真问题，非顺手修）**：
`loop_events.py` 下沉 kernel 后，`_PLAN_READONLY_TOOLS` 首次进入
`check_kernel_nouns` 扫描范围 —— 7 处业务名词命中（factor_* ×5 / quintile /
drawdown），batch_gate 转 RED。运行时核对（`build_default_registry()`，
18 工具）证明清单 24 名中 **14 个是死名**（永不注册：strategy 研究时代的
display 工具 9 个 + 通用残名 5 个）。allowlist 死名对可见工具集零效果
（过滤取注册表交集），删除**行为中性**；该 frozenset 本有同款清理惯例
（L226 旧注释：goal 工具已随批次 2 移除）。已删 14 死名，留 10 活名；
`test_plan_build_mode` 23 用例全绿。这是文件易位**暴露**的问题，
不是迁移引入的问题。

---

### 批次 C · 通道切换（四通道逐一）

> **2026-11-16 B3 后修订**：原 C2/C3/C4 的前提已消解。方案成文时
> `service/store/projector` 还在 L1，「CLI/TUI/MCP 改走服务层」意味着改通道代码；
> B3 之后实现已全在 kernel，而 CLI/TUI/MCP 调用的 `kernel.session.unified`
> **本来就是 L3**——依赖方向已合法。「不再直查 DB」的实质工作收缩为：
> **D1（reader 内部改走共享连接）+ §3.2 改名（unified → reader）**，
> 四通道调用点零改动。修订后的 C 批 = C1（web import 翻转）+ 改名；
> D1 保持独立 commit（K4 敏感）。

| 步 | 动作 | 验收判据 |
|---|---|---|
| C1 | **web**：container / dependencies / chat / admin_users 的 import 从垫片翻到 kernel 真身，**调用点不变** | `tests/test_api_*.py` 全绿；垫片只剩测试兼容职责 |
| C2' | **改名**：`kernel/session/unified.py` → `reader.py`（§3.2：unified 无信息量）；CLI/TUI/MCP/tests 的 10 处 import 同步 | 全量绿；`grep "session.unified" src/` = 0 |
| ~~C2~C4~~ | ~~CLI/TUI/MCP 改走服务层~~ | 被 D1 + C2' 取代（通道已指向 kernel）|

**D1 的 db_path 参数约束（实施前必读）**：`unified.*` 五函数接受显式
`db_path`，tests 大量传 tmp 路径。`db._get_db()` 按解析路径缓存连接，
**无法**尊显式路径 ⇒ D1 不能把函数体无脑换成 `_get_db()`。

> **2026-11-16 D1 实施记录**：采用 `_get_db_at(path)`——把 `_get_db` 的
> 连接体拆为按显式路径取缓存的 `_get_db_at(db_path)`，`_get_db()` 变为
> 「解析路径 + 委托」的薄壳（**解析必须留在模块全局名上**，tests 靠
> monkeypatch `_get_db_path` 重定向）。reader 五函数改走 `_get_db_at`，
> 两个不变式：
> 1. `exists()` 前置守卫保留在取连接**之前**——否则 `_ensure_schema`
>    会把「查询不存在的库」变成「创建空库文件」（`test_missing_db_returns_empty`
>    钉住）；
> 2. 共享连接**不再 close**——句柄由 db 层线程缓存持有，tests 由 conftest
>    `_close_session_services`（B2 复活的那段）统一回收。
> 契约测试当场抓到 `_get_db_at` 未进垫片（防漂移网正常工作），已补。

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
| 3 | ~~未核查 `tests/` 里对硬删/软删的断言~~ **已核查（§3.5）**：4 处断言锁 **3 个不同函数**（reader 门面 / SQLiteStore=/clear 路径 / InMemoryStore 降级后端），**相互零矛盾**；唯一语义分歧 = web 软删 vs CLI 硬删 | ✅ **关闭（2026-11-16）**。E1 解除阻塞，方案见 §3.5「E1 的对齐目标」 |
| 4 | **未评估 `interfaces/api/session/web_session_db.py` 的 FTS 索引迁移** | 该文件有 `_ensure_fts_table` / `_add_column` / `_run_schema_migrations`（370+ 行）—— 搬文件不搬 schema，理论上安全，但需在 B 批后核对 FTS 表仍工作 |
| 5 | ~~未量化四个通道的回归测试覆盖~~ **已核查**：web 52 文件 / CLI 27 / TUI 39 / MCP 18 —— **四通道全覆盖充分** | C 批每步都有对应测试，无需先补 |
| 6 | **`kernel/session/unified.py` 被 2 个测试文件直接 import**（`test_session_list_helper.py` 9 处 / `test_memory_dataplane_hotfix.py`）| D1 批次改 `reader.py` 实现后，这 2 个测试文件的 import 路径也要改（`unified` → `reader`）|
| 7 | **FTS 表由 `web_session_db.py` 独家维护**
| 8 | **R1（`_build_llm_config` 归属）已解** —— LLM 收敛批 `56bce1b0` 消除了 `service.py` 对 `routers.chat` 的 2 处依赖 | ✅ 关闭。`service.py` 对 routers 的反向依赖 4 → 2 处 |
| 9 | **R2（`_invalidate_projection` 依赖 FastAPI Request）不存在** | ✅ 关闭（方案原估计偏保守）。实测只有 `routers/web_session.py` 自身用它，无待迁文件引用 |
| 10 | **`web_session_db.py` 导入 `fastapi`（L19 + L622/L624 两处 `HTTPException`）**，而 kernel/foundation 对 fastapi 导入数 = **0** | ✅ **已关闭（2026-11-16）** —— 拍板 **2b**：11 行 `_fetch_session_owned` 上移 `routers/web_session.py`，其余 759 行下沉 `kernel/session/db.py`。详见 §4-B2 |
| 11 | **8 个测试文件用 `monkeypatch.setattr(_wsdb, "_get_db_path", ...)` patch 实现模块全局名** —— 改垫片后这些 patch 静默失效（re-export 不回写源模块），测试仍绿但写进真实库 | B2.5 一并改指向 `kernel.session.db`（8 文件各 1 行）。见 §4-B2「隐患」 |
| 12 | **`tests/conftest.py:357-359` 的 thread-local 清理恒不生效** —— `getattr(_ws_mod, "_db_thread_local", None)` 恒 `None`，因为 router 的 re-export 清单从未包含它 | B2 顺势指向 `kernel.session.db._db_thread_local`。K4 明令连接显式关闭，失效的清理是 WinError-32 flake 来源 |
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
