# Phase 2 设计：切入点 A — web_session ↔ projector 拆分

> 来源：`refactor-plan-20261008.md` Phase 2（主战场）· `refactor-readiness-20261008.md` 切入点 A
> 现状：`api/session/projector.py` 1493 行 · `api/routers/web_session.py` 1503 行——同主题巨文件对
> 原则：**数据类 / 存储面 / 事件处理器 / 路由面**四层分离；每步独立 commit + 护栏（含 Phase 0 轨迹 diff）
> 状态：耦合面审计定稿，待实施 2.2 → 2.3

## 1. 耦合面审计（读码结论）

**依赖方向是单向且干净的**：`web_session._invalidate_projection` → `projector.invalidate`（web_session 依赖 projector，反向为零）。拆分不需要解环——只需把每层的内聚块分开。

### 1.1 projector.py 1493 行的四层

| 层 | 行范围 | 内容 | 体量 |
|---|---|---|---|
| **A. 状态模型** | 69-263 | `ProjectedPart / ProjectedMessage / ProjectedSession` + 序列化 | ~195 |
| **B. 存储与快照** | 267-540 | `Projector.__init__ / _get_conn / close / snapshots _save/_load / project_incremental / invalidate / _collect_touched / _message_row / _part_rows` | ~275 |
| **C. 全量投影 + flush** | 540-812 | `load_events / project / apply / flush（~125 行单方法）/ _upsert_message / _insert_part / project_and_flush / project_to_messages` | ~270 |
| **D. 事件处理器** | 813-1493 | `_apply` 分发 + `_on_message_received / assistant / text_* / tool_* / thinking_*`（15 个 handler） | **~680** |

**最大块是 D**（事件→状态的纯函数式更新，无 IO）——天然独立层。

### 1.2 web_session.py 1503 行的三层

| 层 | 行范围 | 内容 | 体量 |
|---|---|---|---|
| **E. DB schema + 迁移** | 54-625 | `_get_db_path / _ensure_schema / _run_schema_migrations / _ensure_fts / _add_column / _migrate_*` | **~570** |
| **F. 前端形状化** | 1096-1384 | `_build_goal_snapshot / _shape_goal_for_frontend / _shape_workflow_* / _shape_agents_for_frontend / get_session_state` | ~290 |
| **G. 路由与持久化** | 725-1090 + 1384-1503 | `persist_message / auto_title / CRUD 路由 / list_messages / _invalidate_projection` | ~390 |

## 2. 切分方案

### 2.1 projector（2.2，先拆被依赖方）

```
api/session/projector.py        # 壳：Projector 外观（方法委托），~200 行
api/session/projection_models.py   # A：Projected* 数据类
api/session/projection_handlers.py # D：事件 → 状态更新（纯函数面）
api/session/projection_store.py    # B+C：连接/快照/flush/行转换
```

**边界纪律**：
- D（handlers）**零 IO**——只接受 `(event, state)` 返回更新后状态；不碰 sqlite/连接
- B+C 持有连接与 flush 事务；`close()` 生命周期住这里（第四轮 flaky 的教训：连接生命周期显式化）
- `Projector` 保留为**外观**（public API 不变：`project_incremental / flush / invalidate / project_to_messages / close` 全部委托）——**调用方零改动**

### 2.2 web_session（2.3）

```
api/routers/web_session.py       # G：路由壳（CRUD/messages/persist）
api/session/web_session_db.py    # E：schema + 迁移（纯 DDL，无业务）
api/session/web_session_state.py # F：goal/workflow/agents 前端形状化
```

**边界纪律**：E 不 import 路由/业务；F 纯函数（快照 dict → 前端 dict）；G 只剩路由 + 持久化编排。

## 3. 实施顺序与验收

| 步 | 动作 | 验收 |
|---|---|---|
| 2.2.1 | 抽 `projection_models.py`（A） | `test_projector*` 全绿；projector 委托导入 |
| 2.2.2 | 抽 `projection_handlers.py`（D） | 同上 + 事件投影单测直接打 handlers |
| 2.2.3 | 抽 `projection_store.py`（B+C），`Projector` 变外观 | 同上 + `close()` 行为测试 |
| 2.3.1 | 抽 `web_session_db.py`（E） | `test_session*` + 迁移测试全绿 |
| 2.3.2 | 抽 `web_session_state.py`（F） | state 端点测试全绿 |
| 每步 | 69 回归 + 相关定向 + **轨迹 diff**（Phase 0 检测器） + ruff src=0 | 全过才 commit |

**红线**：event_store 是消息真源（projector 只是物化视图，flush best-effort）；SSE 事件序不变；`_invalidate_projection` 的失效语义不变；连接 close 语义不变（显式生命周期，不隐式泄漏）。

## 4. 风险与回滚

- **每步独立 commit**——revert 单步不影响其他层
- `Projector` 外观保 public API → 调用方（event_store._flush_projector / service / tests）**零改动**
- web_session 的 schema 迁移块搬移风险最高（DDL 顺序敏感）——**原样搬运不重排**，迁移测试跑一遍
- 轨迹 diff（Phase 0）：若拆分改变事件时序→轨迹漂移立刻可见
