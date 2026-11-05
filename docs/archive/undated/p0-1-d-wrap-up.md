# P0-1 D — 收尾与验证

> **Status:** Draft (branch `p0-1-event-sourcing`)
> **承接:** Phase A-C 已落地 schema 统一、EventV2 合并、fork、blob TTL。本阶段是验证 + 文档收尾。

## 目标

1. 全量测试绿灯
2. 性能基准：5000 事件 session 的 TraceProjection 在 B1 下推后 < 100ms
3. 文档：更新 README/架构图、P0-1 完成总结
4. ruff 干净（无新增告警）

## 步骤

### D1: 全量测试

跑测试套：
- src 层相关测试（event_v2, event_store, projector, projector_snapshot, replay_pushdown, blob_cleanup, event_store_fork, event_store_b3_b4, trace_projection, …）
- session/api 测试
- event_sourcing 系列

记录最终通过/失败数。

### D2: 性能验证

5000 事件 session + 5% trace 类型（~250）：
- emit 5000 events 到一个 SQLite-backed EventStore
- 调用 `TraceProjection.project(session_id, limit=100)` —— 测时
- 期望：B1 下推后 SQL 层过滤只返回 ~250 行；反序列化只跑 250 次

记录：
- 总耗时（毫秒）
- 返回 records 数
- 是否 < 100ms 阈值

写入 `tests/test_phase_d_perf.py`（可选——性能测试可能 flaky）。

### D3: 文档更新

- 在 `docs/archive/undated/observability.md` 中追加 "Event sourcing architecture (P0-1)" 一节
- 更新 `docs/design/p0-1-event-sourcing-design.md`（母文档）：标注 A1-A4 + B1-B4 + C1/C3 状态
- 可能的 README 顶部段落（如果 README 存在）

### D4: ruff + 提交

- `ruff check` 整个 P0-1 改动文件
- 任何未引入新告警
- 单次"phase D"提交汇总

## 成功标准

- 全部 565+ 测试通过
- 性能：B1 后 TraceProjection 5000 事件 session 在 SQLite 上 < 100ms
- 文档：3 个 docs 文件已更新/新增
- ruff：仅有存量 3 错误（C901 + 2×I001），无新增

## 后续（P0-2 候选）

- SSE 双路径统一（EventStore-backed）
- branch_id 多分支 fork（不是 main）
- Resume API 端点 + service 层集成
- blob_refs 的 decrement 路径（事件真正删除时）