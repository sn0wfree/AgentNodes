# 旧聊天队列 API 兼容出口审查（裁决 3 前置）

> **日期**：2026-11-18 · **状态**：审查完成，**删除未获批准**
> **依据**：`project-architecture-review-and-refactor-plan-20261118.md` §8.3 裁决 1（`send_message` 为兼容 API）+ §12.1 顺序 2/3 + §12.2 剩余边界
> **审计工具**：`scripts/arch/audit_legacy_chat_api.py`（只读、可复跑、不调用被审计的旧实现）
> **裁决结论**：**先批准兼容审查，不批准删除**——本文件给出审查证据、两个待裁决差异、删除边界与兼容期策略。

## 0. 结论摘要

1. **仓库内生产调用点 = 0**（逐出口实测，见 §2）；但"仓库内无调用 ≠ 外部无消费者"——`send_message` 已被定为**兼容 API**，删除属对该裁决的变更。
2. **R-015 四项判定**：① Gateway 接管方向已定 ✅；② 主路径与 Goal lease 已接线 ✅；③ **完整出口网尚未证明** ❌；④ **独立依赖审计本文件已交付** ✅（`audit_legacy_chat_api.py`）。→ **四项未全部满足，不得删除**。
3. **两个真实差异需裁决**（§4）：超时语义在 Gateway 侧无对等物（缺口①）；Gateway 聊天历史读取异常被吞（缺口③，与裁决 2 严格读契约冲突）。
4. **缺口②已证伪**：lease 释放后**常驻消费者自动继续消费**（实测通过，非手动 `run_once`）。
5. **删除边界已划清**（§5）：保留会话/历史/Attempt 持久化/压缩；**不得**随旧队列删除 `_convert_messages_to_history`；**不是**删除全局 AgentLoop。

## 1. 审查方法（不依赖旧实现）

| 手段 | 说明 |
|---|---|
| 静态引用审计 | `scripts/arch/audit_legacy_chat_api.py`：逐出口扫描 src/tests/scripts/docs 的定义与调用点，**按接收者归属**判定（`gateway.cancel_session` ≠ `service.cancel_session`；`runtime.cancel` ≠ `svc.cancel`）；排除日志字符串与定义文件内部自调用 |
| 绝对值断言 | `tests/test_gateway_legacy_parity.py` **18 用例**：每个旧出口只读 **Gateway/apps 当前行为**的绝对值，不与旧实现对比（旧实现即将删除，不能作为基准） |
| 缺口核实 | 超时 env 执行引用 grep；常驻消费者唤醒路径读码 + 真实线程测试；历史读取异常处理读码 |

**静态扫描的固有盲区**（必须写进兼容期公告）：动态 import、插件加载（`importlib.metadata.entry_points`）、仓库外消费者、反射调用。

## 2. 逐出口审计表（2026-11-18 实测）

| 旧出口（`kernel/session/service.py`） | 定义 | **src 生产调用** | 测试调用 | 文档提及 | Gateway 对应物 | 判定 |
|---|---:|---:|---:|---:|---|---|
| `send_message` | :126 | **0** | 30 | 42 | `api_chat_request` + mailbox + `run_once` | 兼容 API（裁决 1）——**不删** |
| `cancel` | :315 | **0** | 16 | 133 | `cancel_session` | 可删（无生产调用） |
| `cancel_session` | :328 | **0** | 9 | 11 | `Gateway.cancel_session` | 可删（同名者是 Gateway 的，非本类） |
| `resume_queue` | :352 | **0** | 3 | 3 | `Gateway.resume_session` | 可删 |
| `wait_for_attempt` | :474 | **0** | 3 | 2 | `Gateway.wait_turn` | 可删 |
| `is_session_processing` | :363 | **0** | 4 | 16 | `execution_lease.holder` | 可删（B4 已标注退役候选） |
| `mark_session_processing` | :397 | **0** | 5 | 19 | `execution_lease.acquire/release` | 可删（B4 已标注退役候选） |
| `list_active_attempts` | :416 | **0** | 8 | 5 | `apps/chat.attempts_query.query_live_attempts` | 可删（已迁，30c40467） |
| `get_available_actions` | :380 | **0** | 4 | 4 | `Gateway.chat_session_state` | 可删 |

**重导出链**（外部消费者可能从这里 import，兼容期必须保留）：

| 文件 | 符号 | 状态 |
|---|---|---|
| `src/agentnodes/kernel/session/__init__.py` | `_CHAT_ATTEMPT_TIMEOUT` | 存在且重导出 |
| `src/agentnodes/interfaces/api/session/service.py` | `_CHAT_ATTEMPT_TIMEOUT` | 存在且重导出 |

**与旧队列绑定的 env**：

| env | src 命中 | 执行读取点 | 结论 |
|---|---:|---|---|
| `AGENTNODES_CHAT_ATTEMPT_TIMEOUT` | 3（settings 声明 + 旧 service 读取 + 旧 service 使用） | **仅** `kernel/session/service.py:43` | **Gateway 侧无对等物**（缺口①） |
| `AGENTNODES_EVENT_LOG_READ` | 4 | 仅 settings 声明 + 诊断层 | 读取层已按 C4 移除（顺序①），无旧实现依赖 |

## 3. 逐出口的 Gateway 绝对值断言（`tests/test_gateway_legacy_parity.py`，18 用例）

| 出口 | 断言（绝对值，不依赖旧实现） |
|---|---|
| send_message | 提交 → `run_once` 产出 answer="ok"；回执 `accepted=True`/`reason=None`/`session_id` 非空；空队列 → `run_once() == []` |
| cancel / cancel_session | 无活跃 run → `False`；有活跃 run → `True` 且 `chat_session_state.paused is True`；重复取消幂等 `True` |
| resume_queue | 未暂停 → `False`；取消后 → `True` 且 `paused is False` |
| wait_for_attempt | 无 turn 完成 → 超时 `None`；有结果 → `answer == "ok"`（代际语义，不返回上一轮） |
| list_active_attempts | 存储有记录但 Gateway 无实时状态 → **丢弃**（僵尸行）；查询用例 = 存储快照 × Gateway 实时状态 |
| get_available_actions | 空闲 → `active/pending/paused` 全 False + `lease_holder is None`；有 pending → `pending is True`；goal 持有 → `lease_holder == "goal:g1"` |
| is_session_processing | 空闲 → `holder is None`；持有 → `holder == "goal:g1"`；释放 → 回到 `None` |
| **缺口② 常驻消费者** | **真实 `start_consumer` 线程**：goal 持有期间 `client.calls == []`（不执行）；lease 释放后**无需手动 `run_once`**，消费者自动继续（轮询兜底生效） |

## 4. 两个待裁决差异（旧路径有缺陷，不机械照搬）

### 缺口①：超时语义在 Gateway 侧无对等物

- 现状：`AGENTNODES_CHAT_ATTEMPT_TIMEOUT`（默认 600s）的**唯一执行读取点**是 `kernel/session/service.py:43`，只作用于**旧队列**的 attempt 执行（`:698/:714/:719/:722/:740/:746`）。
- Gateway 路径：`_send_via_gateway` → `SessionController` → `AgentLoop`，**没有同等的 attempt 级超时**。
- **差异影响**：删除旧队列后，"单次 chat 超过 600s 自动失败"这一语义**在 Gateway 路径上没有承载点**（长任务可能无限跑）。
- **建议裁决（二选一，需维护者定）**：
  - (a) **在 Gateway 侧补等价超时**（推荐）：`SessionController` 执行 turn 时按同一 env 设墙钟上限，超时 → attempt 失败 + 事件 + Attempt 终态，与旧语义对齐；
  - (b) **明确宣布该语义退役**：把 env 移入诊断层（legacy），文档注明"Gateway 路径不再有 attempt 级超时"，并接受长任务风险。
- **不做**：不静默删除 env 声明（会触发 phantom 门禁 + 破坏外部配置）。

### 缺口③：Gateway 聊天历史读取异常被吞

- 现状：`interfaces/api/routers/chat.py:514` `history = service._convert_messages_to_history(...)`，异常时 `:518` 仅 `logger.warning("send_via_gateway: history load failed: %s", exc)`，随后以**空历史继续执行**。
- **与裁决 2 冲突**：裁决 2 规定"聊天上下文等严格读路径应显式报错……不能静默喂给模型"。静默空历史 = 模型"忘记"历史，比报错更危险（且不产生可观测错误）。
- **建议裁决**：改为**显式失败**（HTTP 5xx + 明确错误体 + 事件），与 `MemoryManager.get` 的 `HistoryReadError` 语义对齐。
- **不做**：不静默保留（那正是缺口本身）；不改则须在删除清单里注明"历史读取降级仍在"，不得宣称"严格读契约已全量落地"。

## 5. 删除边界（划清，避免误删）

**保留（不在删除范围）**：

- `SessionService` 的会话生命周期（`create_session` 等）与 Attempt 持久化；
- `SessionStore` 历史读取（`get_messages`）、`Projector` / `ProjectionStore` 读模型；
- `compact_history`（压缩）；
- **`_convert_messages_to_history`（聊天路由仍在调用，`chat.py:514`）**——随旧队列一起删会直接打断 /send；
- 全局 `AgentLoop`（kernel/agent/loop）——本裁决**不涉及**删除 AgentLoop，只是不再由旧 SessionService 调度它。

**候选删除（须先关闭缺口①③ + 兼容期公告）**：

`kernel/session/service.py`：`send_message` / `cancel` / `cancel_session` / `resume_queue` / `wait_for_attempt` / `is_session_processing` / `mark_session_processing` / `list_active_attempts` / `get_available_actions` / `_process_session_queue` / `_run_attempt` / `_signal_attempt_done` + 相关队列字段（`_session_queues` / `_paused_sessions` / `_active_loops` / `_queue_consumers` / `_processing_sessions`）。

**不做**：不制造第二套执行队列；不删除 `SessionService` 类本身（其余职责仍在）。

## 6. 兼容期与破坏性变更策略（需维护者确认）

1. **兼容承诺期**：`send_message` 已在裁决 1 定为兼容 API → 保留至兼容期结束，期内行为不变（含 `max_iterations=50` / `allow_shell_tools=False` 默认与 policy 注入能力）。
2. **删除前置条件**（全部满足才进入删除批次）：
   - 缺口①按裁决落地（补超时 或 明确退役）；
   - 缺口③按裁决落地（显式失败 或 明确保留并记录）；
   - 兼容期公告已发（仓库 + CHANGELOG + 迁移说明：外部消费者如何改用 Gateway 入口）；
   - 本审查的 18 条绝对值断言全绿（删除前后各跑一次全量 + batch_gate）。
3. **删除批次形式**：R-014 方案先行 + R-015 四项 + R-016 出口网 + R-020 独立最后批次；**独立提交**，可单独回滚。
4. **迁移方式**：外部消费者改用 Gateway 入口（`api_chat_request` + `cancel_session` / `resume_session` / `wait_turn`）；旧方法在兼容期内标注 deprecated（docstring + 公告），期满后随删除批次移除。

## 7. R-015 四项现状判定

| 项 | 状态 | 证据 |
|---|---|---|
| ① Gateway 接管方向已定 | ✅ | §8.3 裁决 2/3 已接受并实施（594fe63a 等） |
| ② 主路径 + Goal lease 已接线 | ✅ | `/send_async`/`/cancel`/`/queue/resume`/`/attempts` 已切 Gateway（f01555d5）；Goal 用 lease（594fe63a） |
| ③ 完整出口网 | ❌ | 两层重导出仍在（§2）；缺口①③ 未裁决 |
| ④ 独立依赖审计 | ✅ | `scripts/arch/audit_legacy_chat_api.py`（只读、可复跑、不调用旧实现） |

→ **③④ 未闭合前不进入删除批次。**

## 8. 本轮未做事项

- 未删除任何旧实现（裁决 3 只批准审查）；
- 未修改旧队列行为（仅新增审计与断言测试）；
- 未裁决缺口①③（待维护者决定）；
- 未提交删除相关改动。
