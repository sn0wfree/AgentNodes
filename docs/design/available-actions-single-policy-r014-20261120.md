# 可用动作矩阵单策略 R-014 方案（第三轮复审候选 S3）

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **来源**：第三轮架构复审候选 S3（`C:\Users\1\AppData\Local\Temp\architecture-review-20261120-163911.html`）。
> **前置**：B2 租约机制（裁决 2）已实施；`get_available_actions` 为下一版本删除候选（冻结保留）。
> **范围**：一份纯策略定义动作矩阵；删 kernel 方法与 router handler 的重复判断，不删删除候选本身。

## 0. 目标与非目标

**目标**：
- 动作矩阵（cancel/resume/send 的 label/destructive + goal lease 抑制 send）只有一个定义点：`kernel/session` 纯函数；
- L1 router 与 kernel 方法都经该纯函数产出同一矩阵，`lease_holder` 维度纳入策略；
- 行为逐字不变（现有 kernel 5 例 + router contract 3 例全绿）。

**非目标**：
- **不删 `SessionService.get_available_actions`**（它是 `pending-approvals-20261119.md` 的下一版本删除候选，冻结保留）；只把它改成薄包装；
- 不改 `Gateway.chat_session_state` 的 4 键快照；不改租约机制；不改 `apps/chat/policy.py`；
- 不迁移/删除任何测试锚；不碰冻结删除批。

## 1. 现状证据（已回读源码核对）

### 1.1 两份实现
- kernel `SessionService.get_available_actions`（`service.py:381-396`）：读 `_processing_sessions` / `_paused_sessions`；产出 cancel/resume/send（均 `destructive="false"`）；**无 lease 维度**；生产调用 0，测试 5 处。
- router `chat_available_actions`（`chat.py:966-991`）：读 Gateway `chat_session_state` 的 `active`/`pending`/`paused`/`lease_holder`；`is_processing = active or pending`；lease holder 以 `goal:` 开头时抑制 send；包 `{"status","session_id","actions"}` 返回。
- Gateway 快照（`gateway.py:376-387`）：4 键 `active`/`pending`/`paused`/`lease_holder`（`session_lease.py` 证 `holder()` 仅供观测）。
- 漂移：kernel 读的是 chat 路径永不写入的空转集合（`session_lease.py:11-12` 明示），router 才是活路径；router 多了 lease 抑制。

### 1.2 层与删除候选约束
- 层：L1 router 可向下 import L3 kernel（`chat.py:41` 已 import `SessionService`）；kernel **不可** import `apps/chat`（L3→L2 上行，ADR-003 基线 0）。故纯策略必须放 kernel/session。
- `get_available_actions` 无 docstring 删除注，但 `pending-approvals-20261119.md:34-42` 列为下一版本删除候选（删除形式 R-014+R-015+R-016+R-020 独立批次）；`is_session_processing`/`mark_session_processing` 兄弟方法才有 docstring 退役注（`service.py:373-377`）。**本方案不删它**。

### 1.3 测试锚
- kernel：`test_session_kernel_direct.py:141`、`test_session_service_units.py:149-168`（5 例，直接操作私有集合）。
- router：`test_chat_control_contract.py:794-827`（3 例，monkeypatch `_get_gateway` 返回 `SimpleNamespace(chat_session_state=Mock(return_value=state))`）；`test_gateway_legacy_parity.py:275-292` 验 Gateway 快照形状（含 lease holder）。
- 无测试断言 router 的 lease 抑制（仅 Gateway 快照暴露 lease holder）——策略必须保留该逻辑，否则违反 B2 裁决 2。

## 2. 设计（grilling 全 A：Q1-Q3）

| # | 决策 | 落位 |
|---|---|---|
| Q1 | kernel/session 新增纯函数 `available_actions(*, active: bool, pending: bool, paused: bool, lease_holder: str|None=None) -> list[dict[str,str]]`（或放 service.py 同模块），实现矩阵与 lease 抑制 | `kernel/session/service.py`（新函数） |
| Q1′ | `SessionService.get_available_actions` 改薄包装：`active = session_id in self._processing_sessions`、`paused = session_id in self._paused_sessions`、pending=False、lease_holder=None → 调纯函数（5 例行为逐字不变） | `service.py:381-396` |
| Q1″ | router 改：`state = _get_gateway().chat_session_state(session_id)` → `actions = available_actions(active=state["active"], pending=state["pending"], paused=state["paused"], lease_holder=state.get("lease_holder"))`（删内联矩阵） | `chat.py:966-991` |
| Q2 | lease 抑制纳入纯策略 | 策略内 |
| Q3 | 新增纯函数表驱动测试（含 lease 抑制行）；kernel 5 例 + router 3 例零改动 | 新测试 |

**实现要点**：
- 纯函数规则（与 router 现行为逐字一致）：`is_processing = active or pending`；processing → cancel；paused → resume；`not is_processing and not paused and not (lease_holder or "").startswith("goal:")` → send。
- 所有 action dict：`{"name": ..., "label": ..., "destructive": "false"}`（与两份现状逐字一致）。
- kernel 包装：映射 `_processing_sessions` → active、`_paused_sessions` → paused、pending=False、lease_holder=None（kernel 无 lease 维度，行为与现状一致）。

## 3. 分批与验收（单提交）

| 验收项 | 落点 |
|---|---|
| 纯策略单一定义 | kernel/session |
| kernel 方法薄包装 | service.py（删除候选保留） |
| router 改调纯策略 | chat.py（ownership 与返回信封不动） |
| 表驱动测试含 lease 抑制 | 新测试 |
| kernel 5 例 + router 3 例零改动全绿 | 既有测试 |
| 数字同步 | R-007 |

**回滚**：单提交回退恢复；零残留。

## 4. 风险与回滚

- **行为等价**：kernel 现状 `active` 与 `paused` 可同时为真→ cancel+resume；纯函数同规则，逐字等价。router 的 `active or pending` 折叠保留。
- **router monkeypatch**：`test_chat_control_contract` 的 fake state 是 dict，纯函数按 4 键读取，兼容。
- **删除候选纪律**：`get_available_actions` 保留且行为不变——本方案只是让它复用纯策略，不改动其"删除候选"状态。
- **数字漂移**：sync + check_framework_numbers。

## 5. 明确不做

① 删 `get_available_actions`（冻结删除候选）；② 改 Gateway 快照 / 租约机制；③ 改 `apps/chat/policy.py`；④ 迁移/删测试锚；⑤ 任何删除批动作。

## 6. 实施前确认

本方案来自 grilling 全 A（Q1-Q3）。**未获批不得实施任何代码**；获批后按 R-007 单提交实施，doc first 纪律已满足（本文件）。

## 7. 执行记录（2026-11-20 · 已实施 · 用户"请继续做完所有的"授权）

- `kernel/session/service.py`：新增纯函数 `available_actions(*, active, pending, paused, lease_holder=None)`（入 `__all__`）——cancel/resume/send 矩阵 + goal lease 抑制 send 的唯一定义点；`get_available_actions` 改薄包装（删除候选状态不变）。
- `routers/chat.py`：`chat_available_actions` 删内联矩阵，改调纯函数；ownership 与返回信封逐字不变。
- `interfaces/api/session/service.py`：**同步补 `available_actions` re-export**（迁移 parity 测试 `test_every_top_level_name_is_reexported[service]` 捕获——新增 kernel 顶层符号必须进冻结垫片；不删任何垫片）。
- 新增 `tests/test_available_actions.py`（9 例表驱动：四输入组合含 lease 抑制 + label/destructive 契约）；kernel 5 例 + router 3 例零改动全绿。
- 验证：受影响面 96 passed；全量 `-n 6` **8071 passed** / 0 failed（+9 新例）；batch_gate ALL GREEN；数字 464/97,025（L1 21,946 / L3 51,333）。
