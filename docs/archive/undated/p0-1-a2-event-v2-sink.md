# P0-1 A2 — EventV2 下沉说明（2026-08-15）

> **Status:** Applied (branch `p0-1-event-sourcing`, follow-up to A1)
> **决策:** ① EventV2 下沉到 `core/events/event_v2.py` ② 不留 shim，删除 `api/session/event_v2.py`

## 决策摘要

`core/agent/event_store.py` 原有的 `EventV2` 是简化版（tuple-based `from_row`、
`id` 有默认值、无校验），与 `api/session/event_v2.py` 的完整版并存。两者运行时
行为不同：`from_row` 的输入类型、构造时是否需要 `id`、是否有 `EventType` 校验。

A2 目标：消除双 EventV2，统一为完整版。

**为什么下沉而非直接 `from ...api.session.event_v2`：**

| 考量 | 结果 |
|------|------|
| 运行时循环依赖 | 无（`event_v2.py` 纯 stdlib） |
| 分层方向 | `event_store.py` 在 core 层，导入 api 层 → **core→api 反向依赖**，违反分层 |
| 未来风险 | `event_v2` 若日后依赖 core 即成环 |
| 领域语义 | EventV2 是领域事件类型，本属 core；放在 api 是历史遗留 |

**为什么不留 shim：**

shim 会让旧路径继续工作、掩盖新位置，加重命名负担。A2 是一次性干净迁移。

## 新位置

```
core/events/__init__.py            ← re-export
core/events/event_v2.py            ← EventType + EventV2 + is_known_event_type（原 363 行内容整体迁移）
```

`api/session/event_v2.py` **删除**。

## 导入变更（src 4 处 + tests 约 24 处）

所有 `from strategy_research.api.session.event_v2 import ...` →
`from strategy_research.core.events.event_v2 import ...`

详细路径变更参见 git diff（commit `d5e4380` 之后的下一次提交）。

## event_store.py 兼容性修复

| # | 差异 | 修复 |
|---|------|------|
| C1 | 简化版 `id` 有默认值；完整版 `id` 必填 | 构造处改用 `EventV2.create(...)`（自动赋 id + 时间戳 + 校验） |
| C2 | 简化版 `from_row` 接受 tuple；完整版接受 dict/Row | `_replay` 内手动将 tuple 行转为 dict，不改 `_ensure_conn`（`memory_manager.py` 依赖 tuple 索引） |
| C3 | 完整版 `create()` 要求 `seq > 0` | `_next_seq` 从 1 起，天然满足 |

## 验证

- `tests/test_event_v2.py` 直接验证 EventV2 + EventType 行为
- b2/b3/b4/b5/b6 系列测试 + session/api 测试全绿
- `ruff check` 无新告警
- `git grep "api.session.event_v2"` 返回 0 命中

## 后续

`core/events/` 现在是事件领域层，P0-1 B/C/D 阶段的 fork/resume、blob_refs、snapshot 等
扩展都应放在此层。