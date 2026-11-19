# 旧聊天队列 API 迁移指南

> **状态**：弃用期公告（2026-11-19）· 删除需另行审批
> **期限**：至少一个完整正式版本周期
> **相关**：[兼容审查](../design/legacy-chat-api-compat-review-20261118.md) · [三前端一核心设计](../design/l1-three-frontends-one-core-design-20261119.md)

## 0. 公告范围

`SessionService` 的 9 个旧队列出口进入弃用期。它们在当前仓库的 **src 生产调用
全为 0**，但静态审计无法证明仓库外无消费者（动态 import / 插件加载 / 反射
均为盲区）。本指南帮助外部消费者迁移；**删除尚未批准**，弃用期内旧 API
行为不变。

`send_message` 按裁决 1 为兼容 API：弃用期内保留签名、默认参数
（`max_iterations=50` / `allow_shell_tools=False`）与 policy 注入能力，
行为不变。

## 1. 逐出口迁移表

| 旧出口（`SessionService`） | 新入口 | 迁移要点 |
|---|---|---|
| `send_message(session_id, content, ...)` | `Gateway` + `api_chat_request(session_id, text, ...)` → `entry.submit(...)` | 新入口默认 receipt 型；需要同步结果用 `wait=true` 路径或 `wait_turn` |
| `cancel(session_id)` | `Gateway.cancel_session(session_id)` | 新返回 bool，无活跃 run → False |
| `cancel_session(session_id)` | `Gateway.cancel_session(session_id)` | 同名，参数一致；旧类是实例方法，新类是 Gateway 方法 |
| `resume_queue(session_id)` | `Gateway.resume_session(session_id)` | 未暂停 → False；取消后 → True |
| `wait_for_attempt(session_id, attempt_id, timeout)` | `Gateway.wait_turn(session_id, timeout)` | **语义差异见 §2.1**：wait_turn 是会话代际等待，不接受 attempt_id |
| `is_session_processing(session_id)` | `Gateway.execution_lease.holder(session_id)` | 新返回 owner 字符串（`goal:<id>` / `chat:<run>`）或 None；只读观测，不可当锁 |
| `mark_session_processing(session_id)` | `Gateway.execution_lease.acquire(...)` | 新原语原子获取 + ownership token；必须按 token release |
| `list_active_attempts(session_id)` | `apps/chat.attempts_query.query_live_attempts(...)` | 新路径过滤 DB 僵尸行（store 快照 × Gateway 实时状态）；**必须传 store 与 gateway** |
| `get_available_actions(session_id)` | `Gateway.chat_session_state(session_id)` | 新返回 `active/pending/paused/lease_holder` dict |
| `_CHAT_ATTEMPT_TIMEOUT`（重导出常量） | `settings.get_int("AGENTNODES_CHAT_ATTEMPT_TIMEOUT")` | env 声明保留、Gateway 侧已读同一 env（默认 600 / 0=禁用）；不要直接 import 旧常量 |

## 2. 语义差异（迁移时必读）

### 2.1 `wait_turn` 是会话代际语义，不是指定 attempt 等待

旧 `wait_for_attempt` 按 attempt_id 等待；新 `wait_turn(session_id)` 等**本会话的下一次终态**，
不返回上一轮结果。不要把它当作指定 attempt 的等待接口。

### 2.2 STEER 单次通知

`/send` 默认 STEER：活跃 turn 在安全点吸收消息而不新建 turn。吸收时 assistant_message
落库由 Gateway 单例观察器按 run_id 恰一次完成；各等待方连接各自收到通知。
依赖「每桥各落一次库」逻辑的外部消费者需调整。

### 2.3 超时能力四级

attempt 超时事件的 `timeout_tool_outcomes` 按调用记录实际状态
（`stopped` / `still_running` / `returned` / `unknown`），与声明能力四级
（`cancellable` / `abandonable` / `isolatable` / `unstoppable`）并列。
**声明不等于事实**；`isolatable` 未接生产前不得报告进程已终止。
见 `docs/design/gateway-timeout-and-strict-history-design-20261118.md` §10。

### 2.4 历史严格读

Gateway 路径历史读取失败 → HTTP 500 `{error: "history_unavailable"}`，不提交；
只有显式标记的 UI best-effort 读取可降级，不进模型上下文。

## 3. 仓库外消费者自查

静态审计的盲区（必须人工核对）：

- **动态 import**：`importlib.import_module("agentnodes.kernel.session.service")` /
  `__import__` 字符串拼接；
- **插件入口**：`importlib.metadata.entry_points(group=...)` 加载的外部包；
- **反射**：`getattr(module, "send_message")` / `hasattr` 链式调用；
- **字符串路径**：配置 / 脚本里硬编码的 `agentnodes.kernel.session.service.send_message`；
- **前端 / 脚本**：直接调用旧 HTTP 端点或 Python API 的 shell 脚本。

自查命令示例（仓库内）：

```
git grep -n "send_message\|wait_for_attempt\|list_active_attempts" -- src tests scripts webui
```

发现仓库外消费者请在删除审批前反馈——删除批需单独批准，本公告不构成删除授权。

## 4. 时间线

| 阶段 | 状态 |
|---|---|
| 弃用公告（CHANGELOG + 本指南 + 兼容审查 §6 定稿） | ✅ 2026-11-19 |
| 弃用期（至少一个完整正式版本周期） | ⏳ 进行中 |
| 删除审批（R-014 方案先行 + R-015 四项 + R-016 出口网 + R-020 独立批） | ⏸ 未批准 |
| 删除执行（旧队列 + 重导出 + 3a.4 面可协同但独立提交；seq_generator 单独批） | ⏸ 未批准 |
