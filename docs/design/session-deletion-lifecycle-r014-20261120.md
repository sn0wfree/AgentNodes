# 会话删除生命周期归一 R-014 方案（第三轮复审候选 S2）

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **来源**：第三轮架构复审候选 S2（`C:\Users\1\AppData\Local\Temp\architecture-review-20261120-163911.html`）。
> **前置**：S1 已实施（`bc514065`）——归属校验收为 `authz.enforce_session_owned`。
> **范围**：把「归档行 + 丢投影状态」这一个删除生命周期操作收进单点，两前端共用。

## 0. 目标与非目标

**目标**：
- 「删除会话」的完整语义（软删归档 + projector 缓存失效）只有一个拥有者；
- Web DELETE 与 CLI delete 走同一条路径，CLI 不再留下脏 projector 缓存；
- 消除 router 遍历另一个 router 私有 cache（`_session_service_cache`）的 seam 泄漏。

**非目标**：
- 不改 `reader.delete_session`（L3）的签名与语义；不把它下沉或上移。
- 不给删除发任何事件（现有语义：删除**不发事件**，失效只能显式调 `EventStore.invalidate`）。
- 不改 `apps/chat/session_views.py`（L2）的任何函数；不改 `kernel/session` 任何代码。
- 不删 `session_views.delete_session_by_id`（L2 薄封装）——它仍在测试面被直接验证。
- 不新增 `Depends` 依赖、不改任何端点签名。
- 不动冻结删除批的任何内容。

## 1. 现状证据（已回读源码核对）

### 1.1 两条删除路径

**Web（L1）** `interfaces/api/routers/web_session.py:285-299`：
```
conn = _get_db()
enforce_session_owned(request, session_id)
conn.execute("UPDATE sessions SET archived = 1 WHERE id = ?", (session_id,))
conn.commit()
_invalidate_projection(session_id, request)
```
`_invalidate_projection`（`:302-325`）两条解析路：有 request → `request.app.state._container.session_service.event_bus.invalidate`；否则 → `from agentnodes.interfaces.api.routers.chat import _session_service_cache` 遍历每个 service 调 `bus.invalidate`。整体 `try/except` + `logger.debug`（best-effort，失败静默）。

**CLI（L1）** `interfaces/cli/commands/session.py:105-120` → `apps/chat/session_views.delete_session_by_id`（L2 `:58-62`）→ `kernel/session/reader.delete_session`（L3 `:181-209`）：纯 `UPDATE sessions SET archived=1` + `commit()`，**无失效**。

**读侧** `kernel/session/reader.py:212-215` `_resolve` → `resolve_session_db_path()`；`kernel/session/db.py:33-43` `_get_db_path()` → **同一个** `resolve_session_db_path()`。两条路径指向同一 DB 文件，合并无分叉风险。

### 1.2 失效机制
- `Projector._cache: Dict[str, ProjectedSession]`（`kernel/session/projector.py:30-43`）按 session_id 缓存投影。
- `Projector.invalidate(session_id)`（`:160-166`）→ `self._cache.pop(session_id, None)`。
- `EventStore.invalidate(session_id)`（`kernel/session/state/event_store.py:620-624`）→ 转发给 `self._projector.invalidate`。
- `SessionService.event_bus: EventStore`（`kernel/session/service.py:62,74`）；`container.py:100-123` 把**同一个** Projector 实例注入 EventStore / SessionStore / UnifiedMemoryManager。
- **删除不发事件**：全仓无 `session.deleted` / `session.archived`；`Projector.FLUSH_ON` 无删除类型。故失效只能是显式调用。

### 1.3 层约束（关键）
- 层定义：L1 `interfaces` > L2 `apps` > L3 `kernel` > L4 `foundation`，import 只能向下。
- `composition.session_service_from`（`interfaces/api/composition.py:83`）在 **L1**。`apps/chat/session_views.py`（L2）调它 = **L2→L1 上行违规**。门禁棘轮 `L2>L1: 1` 只减不增（`tests/test_layer_matrix.py:61-71`）。且 `src/agentnodes/apps/**` 现无任何 `agentnodes.interfaces` import，无先例。
- 故 S2 的拥有者只能在 **L1**（Web 与 CLI 都是 L1，可直接共处一层），L3 kernel 不动。

### 1.4 覆盖缺口
- `_invalidate_projection` **零直测**；全仓**没有任何测试**把「删除入口」与「projector 缓存状态」连起来。缓存断言只出现在直接调 `invalidate()` 的单元测试（`tests/test_projector_incremental.py:168-200,245-250`、`tests/test_event_store.py:240-256`）。
- 现有删除测试：Web 6 处（`test_webui_api.py:86-90,242-292`、`test_webui_e2e.py:76-84,136-139`）、CLI 3 处（`test_cli_session_commands.py:97-116`）、reader 3 处（`test_session_list_helper.py:155-203`）、session_views 2 处（`test_session_views.py:72-100`）、安全 1 处（`test_security_hardening.py:210`）。

## 2. 设计（grilling 全 A）

| # | 决策 | 落位 |
|---|---|---|
| Q1 | 单一操作放 **L1 composition**（`delete_session_lifecycle(session_id, request=None)`），内部做归档 + 失效；不碰 kernel、不发事件 | `interfaces/api/composition.py` |
| Q2 | CLI 改直调 L1 操作；`session_views.delete_session_by_id` 原样保留（其测试锚不动），生产上不再是删除入口 | `interfaces/cli/commands/session.py` |
| Q3 | 失效解析整段移入 composition，`from chat import _session_service_cache` 换成直接读 composition 自己的 `_SERVICE_CACHE`（同对象，去掉 router 私有穿透）；container 优先 + cache fallback 两条路都留 | `composition.py` |
| Q4 | 新增 `tests/test_session_deletion_lifecycle.py`：删除后 projector cache 空 + list 不含该行，Web/CLI 两入口都覆盖；现有删除/E2E 测试零改动 | 新测试文件 |
| Q5 | 独立 R-014 文档 → 获批后单提交 → 全量 + gate + 数字同步 → 不 push | 本文件 |

### 2.1 新操作接口

```python
def delete_session_lifecycle(session_id: str, request: Any = None) -> bool:
    """删除会话的完整生命周期：软删归档 + 投影缓存失效。

    唯一拥有者（L1）：Web DELETE 与 CLI delete 共用。
    返回 True 表示会话行存在且已被归档。
    """
```
- 归档：`UPDATE sessions SET archived = 1 WHERE id = ?` + `commit()`（与现有两处 SQL 逐字相同）。
- 失效：`request` 给定时优先 `resources_from_request(request)` → `container.session_service.event_bus.invalidate(session_id)`；否则遍历 `_SERVICE_CACHE.values()` 调 `event_bus.invalidate`。
- best-effort：失效包 `try/except` + `logger.debug`（沿用现状，失败不阻断删除）。

### 2.2 落位细节

**composition.py**：新增 `delete_session_lifecycle` + 私有 `_invalidate_projection(session_id, request)`（从 web_session 逐字迁入，只把 `from ...chat import _session_service_cache` 改为读本模块 `_SERVICE_CACHE`）。两者加入 `__all__`（`_invalidate_projection` 为私有，不入 `__all__`）。

**web_session.py**：DELETE handler 改为
```python
enforce_session_owned(request, session_id)
delete_session_lifecycle(session_id, request)
return {"status": "ok", "deleted_id": session_id}
```
删本地 `_invalidate_projection` 与不再需要的 `_get_db` 连接获取。**返回体逐字不变**（web 语义：行不存在时 `enforce_session_owned` 先抛 404，故始终 ok）。

**cli/commands/session.py**：改为调 `delete_session_lifecycle(args.session_id)`；`session_detail` 存在性检查保留（打印与返回码逐字不变）。`ok` 语义对齐：Web 恒 True、CLI 原 `delete_session_by_id` 返回 row 是否存在——新操作返回 `cur.rowcount > 0`，与 `reader.delete_session` 同口径。

**session_views.py**：**零改动**。

## 3. 分批与验收（单提交）

| 验收项 | 落点 |
|---|---|
| 单一生命周期操作存在 | `composition.delete_session_lifecycle` |
| Web 走该操作 | `web_session.delete_session` 调之；返回体不变 |
| CLI 走该操作 | `cli/commands/session.py` 调之；打印与返回码不变 |
| 私有 cache 穿透消除 | composition 内读 `_SERVICE_CACHE`，不再 import chat 私名 |
| 投影失效被覆盖 | 新测试：删后 projector cache 空 + list 无该行（Web + CLI 两入口） |
| kernel 零改动 | `kernel/session/**` 无 diff |
| 行为零变化 | 现有删除/E2E/安全测试全绿且零改动 |
| 数字同步 | R-007：sync_architecture_diagram + check_framework_numbers |

**回滚**：单提交回退恢复三处原样，零残留。

## 4. 风险与回滚

- **CLI 语义对齐**：原 CLI 走 `reader.delete_session`（走 `resolve_session_db_path()` + `_get_db_at`），新操作走 composition 内的连接获取。二者同源 `resolve_session_db_path()`，已核实不分叉；`_get_db`/`_get_db_at` 同为 thread-local 缓存连接。差异仅在「路径不存在时 `reader.delete_session` 返回 False（不建库）」——CLI 在此之前已用 `session_detail` 检查过存在性，该分支不可达。
- **返回码**：原 CLI `delete_session_by_id` 返回 bool；新操作同样返回 bool，输出一致。
- **`_invalidate_projection` 迁移**：从 `web_session` 移到 `composition` 后，web_session 的 `logger` 若因此不再被使用需检查（该模块仍有多处日志，预期不受影响）。
- **best-effort 语义**：失效失败仍静默（沿用现状），不改变删除成功与否的判定。

## 5. 明确不做

① 改 kernel / 发删除事件 / 改 `reader.delete_session` 签名；② 改 `session_views` 任何函数；③ 删 `session_views.delete_session_by_id`；④ 改端点签名或加 `Depends`；⑤ 任何冻结删除批动作；⑥ 改 Web 返回体 / CLI 输出与返回码。

## 6. 实施前确认

本方案来自 grilling 全 A 共识（Q1-Q5）。**未获批不得实施任何代码**；获批后按 R-007 单提交实施，doc first 纪律已满足（本文件）。
