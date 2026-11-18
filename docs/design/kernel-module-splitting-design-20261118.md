# 顺序⑤ 大模块职责拆分设计（kernel）

> **日期**：2026-11-18 · **状态**：设计定稿（测绘已完成），**未实施**
> **依据**：`project-architecture-review-and-refactor-plan-20261118.md` §12.1 顺序 5 / §11.2-K5 / §11.4-K4
> **性质**：只读测绘 + 切口判定。实施须在本设计确认后分批进行。

## 0. 结论摘要

四个候选大模块中，**只有一个存在干净的职责切口**：

| 模块 | 行数 | 切口判定 | 建议 |
|---|---:|---|---|
| `orchestration/ticket_store.py` | 690 | ✅ **通用 Ticket/Assignment 状态机** 与 **RSI/goal 策略**（签名口径、工具集发现、库路径）边界清晰，策略侧仅 3 个入口 | **本批实施**：签名策略改为显式注入 |
| `session/service.py` | 1546 | ❌ 队列/attempt 执行/历史转换/压缩/摘要**互相咬合**；且队列部分已被 Gateway 取代（裁决 3 删除批处理） | **不在本批拆**；先走裁决 3 删除队列，再评估剩余职责 |
| `agent/loop/core.py` | 1580 | ❌ 37 方法共享大量可变状态（`_current_iter`/`_turn_counters`/策略/budget/压缩/事件桥），**无独立输入输出的阶段边界** | **不动**（K5 原文："禁止仅按 500 行阈值机械拆分"） |
| `evolution/validation_gate.py` | 1090 | ❌ 成本闸/held-out 门/分歧 kill-switch 三者共享 `ValidationResult` 与状态推进，**拆开会割裂判定语义** | **不动**（K5 原文："整体留在 evolution"） |

**推荐执行范围**：本批只做 TicketStore 的策略注入化（切口清晰、有独立测试边界、零行为变化）；其余三个模块的"大文件"是**复杂度信号而非拆分信号**——真正的解法是行为契约与职责所有权（顺序 2/3 已做），不是切文件。

## 1. 测绘事实（2026-11-18 实测）

### 1.1 `orchestration/ticket_store.py`（690 行，24 方法）

```
class TicketStore (171-687)
  ├─ 通用持久化：upsert_ticket / get_ticket / list_tickets /
  │              create_assignment / update_assignment / get_assignment /
  │              list_assignments / recover / recover_and_mark
  ├─ 状态机：admit（幂等 upsert + create Assignment）/ settle
  └─ 策略侧（本次切口）：
     ├─ __init__(role=None, tool_set=None) → resolve_signature_scope()
     ├─ signature_role / signature_tool_set（只读属性）
     └─ _signature_for(objective) → kernel.evolution.holdout.compute_task_signature
```

策略侧的真实耦合点只有 **3 处**：
- `__init__` 的 `role`/`tool_set` 缺省解析（`resolve_signature_scope`）；
- `_signature_for` 的 lazy import `kernel.evolution.holdout`（L3 内同层，合法但属**应用策略混入通用存储**）。

而 `_signature_for` 的 docstring 已给出关键论据：**"签名是 holdout 的匹配键，决定了哪些历史轨迹能进验证集。若发起方可以自填，验证集就能被挑选（H5 防奖励黑客的最后一环）。store 是唯一的准入/收尾入口，所以它是唯一合法的计算点。"**——这条不变量**必须保留**：签名计算点不能外移到调用方。

### 1.2 `session/service.py`（1546 行，21 方法）

```
├─ 会话生命周期：create_session / get_available_actions
├─ legacy 聊天队列（已被 Gateway 取代，待裁决 3 删除）：
│    send_message / cancel / cancel_session / resume_queue /
│    is_session_processing / mark_session_processing /
│    list_active_attempts / wait_for_attempt /
│    _process_session_queue / _run_attempt / _signal_attempt_done
├─ AgentLoop 执行：_run_with_agent / _run_with_guard / _run_test_script
├─ 压缩与摘要：compact_history / _extract_summary / _build_fallback_summary
└─ 历史转换：_convert_messages_to_history
```

**为什么不按"三组"拆**：`_run_attempt`（执行）同时负责加载历史（转换）、落库（投影桥接）与 AgentLoop 构造——与压缩/历史转换共享 `SessionStore` 与事件总线；切出"执行层"会造出只有一个调用方的转发模块（K5 禁止）。

**真正的边界**：队列那 11 个方法整体已无生产入口（`/send_async` 已切 Gateway，20261118 计划 §12.2 已记录"生产代码搜索未发现 send_message 调用点"）→ **裁决 3 删除批**（R-015/016/014）比"拆分"更直截了当：删掉的是**死路径**，不是拆出**活边界**。

### 1.3 `agent/loop/core.py`（1580 行，37 方法）

共享可变状态清单（拆分的真实障碍）：
- 运行期：`_current_iter` / `_turn_counters` / `_budget` / `_drain_inbox` 注入队列 / `_active_attempt` 类字段；
- 装配期：`_engine` / `_strategy` / `_registry` / `_data_store` / `_sandbox` / `_permission_*`（setattr 挂载）；
- 事件面：`_emit` + `_event_bridge`（`_make_event_bridge`）+ `_trace` 三条并行输出通道。

阶段边界确实存在（`_prepare_run` → `_run_loop_core` → `_get_response` → `_response_to_assistant_msg`），但**阶段间共享上述可变状态**，切出后仍需回调宿主 → 变成"转发层"。K5 明确禁止。

### 1.4 `evolution/validation_gate.py`（1090 行，16 方法）

三个判定闸（成本 `_check_cost` / held-out `_check_heldout` / 分歧 `_divergence_result`）共享：
- 同一 `ValidationResult` 结构与同一状态推进（`_reject` / `_rollback`）；
- 同一"本轮验证锚"概念（`_rollback` 注释：回滚到**本轮验证的锚**，不是 `cursor−1`）。

拆开会割裂判定语义（"哪个闸否决、否决后回滚到哪"），且 `_heldout_unavailable` 的注释已写明语义耦合（"一律 ok=False，不得 activate"）。K5 结论：整体留在 evolution。

## 2. 本批设计：TicketStore 策略注入化（K4）

### 2.1 目标

把"**本应用如何计算任务签名 / 选择工具集 / 用哪个数据库**"从通用 Ticket/Assignment 存储中剥离为**显式注入**，同时**保留签名计算的合法计算点**（§1.1 的防奖励黑客不变量）。

### 2.2 接口

```python
# kernel/orchestration/task_signature.py（新模块，纯策略，无存储）
@dataclass(frozen=True)
class TaskSignaturePolicy:
    """任务签名口径策略（RSI/goal 应用侧提供）。"""
    role: str
    tool_set: tuple[str, ...]
    compute: Callable[[str, str, tuple[str, ...]], str]
    """(objective, role, tool_set) -> signature；由应用侧注入计算实现。"""

    @classmethod
    def from_scope(cls, compute=None) -> "TaskSignaturePolicy":
        """缺省：resolve_signature_scope + evolution.holdout.compute_task_signature。"""
```

`TicketStore` 侧改动：
- `__init__(..., signature_policy: TaskSignaturePolicy | None = None)`——None = 保持现有缺省行为（`resolve_signature_scope` + lazy `compute_task_signature`），**行为零变化**；
- `_signature_for` 改为调用 `self._signature_policy.compute(objective, role, tool_set)`；
- 保留 `signature_role` / `signature_tool_set` 只读属性（外部有消费者）。

**不变量保持**：签名仍**只在 store 内部计算**（发起方不可自填）；策略只提供"怎么算"，不提供"算给谁"。

### 2.3 为什么这样切（而非把 `_signature_for` 整个外移）

外移签名计算点会破坏 §1.1 的防奖励黑客不变量（发起方可自填 → 验证集可被挑选）。本设计把**计算实现**注入（应用侧决定算法），但**计算位置**留在 store（唯一合法点）——两者分离正是 K4 的"策略改为显式注入"原意。

### 2.4 影响面（2026-11-18 实测）

| 项 | 实测 |
|---|---|
| 新增模块 | `kernel/orchestration/task_signature.py`（~60 行，纯策略，无存储） |
| 改动模块 | `ticket_store.py`（构造签名 + `_signature_for` 调用点，~15 行） |
| **生产消费方** | **仅 1 处**：`interfaces/gateway_wiring.py:35`（`TicketStore()` 无参构造）→ 默认参数 → **零改动** |
| 测试消费方 | `tests/test_ticket_assignment.py`（~20 处）+ `tests/test_production_wiring.py:123`——均为无参或 db_path 构造 → 默认参数 → **零改动** |
| `resolve_signature_scope` | 定义在 `ticket_store.py:100`（同模块，策略侧一并抽出） |
| `signature_role` / `signature_tool_set` 外部消费者 | 仅 `interfaces/rsi_cli.py:377-378`（**读属性**，不是签名计算 → 不受影响） |
| 签名计算实现 | `kernel/evolution/holdout.py:275 compute_task_signature`（L3 内同层；`evolution/evolution_agent.py:108`、`evolution/tools.py:99` 也各自调用同一实现） |
| 签名自填守护 | **已有**：`compute_task_signature` 集中在 holdout；store 是唯一**准入/收尾**计算点（docstring 已述不变量）——本设计保留该不变量（计算位置留在 store，只注入"怎么算"） |

## 3. 实施批次（待确认）

| 批 | 内容 | 验证 |
|---|---|---|
| **S1** | 新增 `task_signature.py`（含 `TaskSignaturePolicy` + `resolve_signature_scope` 迁入）+ `TicketStore` 策略注入（默认行为等价） | `tests/test_ticket_assignment.py` + `tests/test_production_wiring.py` 全量 + 新增注入用例（自定义 policy 生效 / 缺省等价 / 计算点仍在 store） |
| **S2** | 生产消费方显式化：`interfaces/gateway_wiring.py:35` 显式传 policy（把"用哪套签名口径"从隐式默认变成可见决策） | 行为等价回归 |

## 4. 明确不做

- 不拆 `agent/loop/core.py`（K5：禁止按行数机械拆分；阶段边界共享可变状态）；
- 不拆 `evolution/validation_gate.py`（K5：判定语义耦合，拆开割裂状态推进）；
- 不在裁决 3 之前拆 `session/service.py` 的队列部分（它属于**待删除的死路径**，不是待拆的活边界）；
- 不把签名计算点外移到调用方（破坏防奖励黑客不变量）。

## 5. 后续（顺序⑤ 剩余部分）

`session/service.py` 的**剩余**职责（队列删除后剩余：会话生命周期 / 历史转换 / 压缩摘要 / AgentLoop 执行）待裁决 3 删除完成后重新测绘——届时"历史转换 + 压缩摘要"可能形成一个有多个调用方的真边界（`SessionStore` 与 `SessionService` 都在用），届时再评估拆分。

## 6. 本轮未做事项

- 未修改任何实现代码（本轮为只读测绘 + 设计）；
- 未删除/移动任何模块；
- 未提交代码。
