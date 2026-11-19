# 3a.0 基线与迁移清单（Chat send_turn 下沉）

> **性质**：3a.0 基线冻结（方案 §4 首子批）· **日期**：2026-11-19
> **来源**：[Chat send_turn 用例下沉实施方案](./chat-send-turn-usecase-extraction-20261119.md) §4
> **口径**：全部数据来自当前 HEAD（`fd1594e8`）真实扫描/运行；行号为该快照基线，
> 实施时须重核。**本批零代码改动**。

## 1. 测试基线（真实运行）

| 命令 | 结果 |
|---|---|
| 契约网 10 文件（`test_chat_control_contract` / `test_gateway_timeout_and_strict_history` / `test_gateway_legacy_parity` / `test_chat_policy` / `test_chat_policy_legacy_wiring` / `test_chat_send_sync_run_traversal` / `test_p51_chat_persistence` / `test_r2_consumer` / `test_w3_sse_seed` / `test_webui_api`） | **179 passed / 2 xfailed**（串行 41.7s） |

两 xfail = 同步工具强中断、sync-only 线程桥（既有结构边界，非本批问题）。
迁移后每子批须以同一命令复核此数字（用例数允许因新增测试上升，不允许下降/失败）。

## 2. 真实消费者图（排除 docstring 提及）

方案 §1 的粗 grep 会把 docstring 里的符号名算成引用——例如
`apps/chat/__init__.py` 仅在模块 docstring 中提到 `_send_via_gateway`，
其 `__all__` 实际只有 `export/personas/policy`，**不是消费者**。以下为 AST
精确结果：

### 2.1 真实 `from ...routers.chat import X`

| 符号 | 外部真实 import | 位置 |
|---|---|---|
| `_build_llm_config` | 2 | `api/routers/slash_common.py:46`、`tests/test_error_messages.py:303` |
| `_get_gateway` | 1 | `api/routers/workflow.py:93` |
| `_get_session_service` | 4 | `api/routers/goal.py:281`、`slash_common.py:39`、`workflow.py:93`、`tests/test_compact_unpack.py:70` |
| `_install_chat_event_bridge` | 1 | `tests/test_r2_consumer.py:157` |
| `_send_via_gateway` | **0** | —（全部是模块属性调用/monkeypatch，见 2.2） |
| `send_async` / `send_sync` | **0** | —（仅 FastAPI 装饰器注册） |

**结论**：`_get_session_service` / `_get_gateway` / `_build_llm_config` 三个
装配 helper 有真实跨路由消费者——迁移时它们必须留在 chat 模块或迁到共享
装配点（如 `api/dependencies.py`），**不能随 send-turn 一起搬走**（R-014：
出口网不破）。

### 2.2 monkeypatch / patch 属性锚（最大出口网）

| 符号 | 锚点数 | 主要文件 |
|---|---:|---|
| `_get_gateway` | 17 | `test_chat_control_contract.py`（多用例）、`test_chat_slash_commands.py`、`test_gateway_timeout_and_strict_history.py`、`test_iteration_budget_wiring.py`、`test_session_reload_recovery.py`、`test_w3_sse_seed.py`、`test_webui_api.py` |
| `_get_session_service` | 16 | `test_chat_control_contract.py`（多用例）、`test_chat_send_sync_run_traversal.py`、`test_chat_slash_commands.py`、`test_compact_unpack.py` |
| `_send_via_gateway` | 17 | `test_chat_control_contract.py`（含 2 处 monkeypatch 替换 + 多处直接调用） |
| `_install_chat_event_bridge` | 10 | `test_chat_control_contract.py`、`test_r2_consumer.py`、`test_r7_sink_and_wait.py`、`test_w3_sse_seed.py` |
| `_build_llm_config` | 9 | `test_chat_control_contract.py`、`test_iteration_budget_wiring.py` |
| `_push_sse` | 8 | `test_chat_control_contract.py`、`test_r2_consumer.py` |
| `_chat_wait_timeout` | 3 | `test_chat_control_contract.py` |
| `_install_answer_persister` | 2 | `test_chat_control_contract.py`（直接调用验证幂等） |

**全部是 `monkeypatch.setattr(chat_router, "<name>", ...)` / `patch.object(chat_mod, ...)`
形态**——无字符串全路径 patch（`monkeypatch.setattr("...chat.x", ...)` 0 处）、
无 importlib/getattr 动态引用。

**迁移约束**：router 改用 apps 用例后，上述锚点要么迁到新模块，要么在
chat 模块保留可 patch 的转发面（**仅此批内过渡**，R-014 禁止永久 re-export）。
3a.4 清理前必须逐项对照此表关闭。

### 2.3 动态引用

无 `importlib` / `getattr(module, "<symbol>")` 动态引用（扫描 0 处）。
无字符串 monkeypatch 锚（0 处）。

## 3. chat.py 迁移边界（顶层定义清单）

| 行 | 定义 | 3a 处置 |
|---|---|---|
| :53 | `_shell_tools_enabled` | 已预警（死代码），不动 |
| :65-221 | `_gateway_chat_options` / `_get_permission_gateway` / `_safe_payload` / `_push_event` / `_get_session_service` / `_build_llm_config` | **装配 helper**——保留或迁共享装配点（见 2.1） |
| :255 | `send_async`（route /send_async，137 行） | 3a.3 接入用例；含 Attempt 顺序修复 |
| :392 | `cancel_attempt`（route /cancel） | 本批不动 |
| :428 | `queue_resume`（route /queue/resume） | 本批不动 |
| :445 | `_get_gateway` | 装配 helper（同 2.1） |
| :457-499 | `_install_answer_persister` + `_persist_answer` | **必须保持**（步骤 2 修复；单例幂等 + run_id 去重） |
| :502 | `_send_via_gateway` | **3a.3 主体**：下沉到 `apps/chat/send_turn.py` |
| :785-793 | `_push_sse` / `_chat_wait_timeout` | 传输适配——留 L1 |
| :802 | `_install_chat_event_bridge` | 3a.2 事件词表 + 3a.3 桥订阅收敛 |
| :1051 | `send_sync`（route /send） | 3a.3 接入用例 |
| :1079-1388 | `chat_events` / `list_active_attempts` / `chat_available_actions` / `chat_export` / `get_session_trace` / `list_personas` / SSE generator 等 | 本批不动 |

## 4. API wire 冻结表

### 4.1 response_model / 状态码

| 端点 | response_model | 关键错误 detail |
|---|---|---|
| POST /send_async | `SendMessageResponse` | 429 `{error:queue_full,limit,current_size}`；400 `{error:entry_rejected,reason}`；500 `{error:history_unavailable,reason,session_id}` |
| POST /send | 无 | receipt / wait 两态；502 `missing_run_terminal` / `missing_run_id` / `run_failed`；504 `no run executed` / `wait_timeout` |
| POST /cancel | `ChatCancelResponse` | — |
| POST /queue/resume | `ChatQueueResumeResponse` | — |
| GET /attempts | `ChatAttemptsResponse` | — |
| GET /personas | `ChatPersonasResponse` | — |

### 4.2 SSE 事件（桥输出，字段绝对值冻结）

`message_received` / `text_delta` / `tool_call` / `tool_result` / `error` /
`agent_done`（4 处发射点）/ `text.started` / `text.ended` / `text_delta`（slash
命令共用协议）。

契约测试断言字段：`accepted` / `reply` / `status` / `message_id` /
`assistant_message_id` / `attempt_id` / `session_id` / `error` / `detail`。

### 4.3 事件桥引擎→前端映射（:825）

`tool_call → tool_call`、`tool_result → tool_result`；`run.started` →
attempt running + message_id holder seed；`run.answer` → attempt completed
（assistant 落库由单例观察器，不在桥）；`run.failed`/`run.cancelled`/`run.fenced`
→ attempt failed/cancelled；`steer.applied` → 跟随 run 并补发合成 run.started；
`work.summarized` → 被摘要请求失败 + 桥注销。

## 5. 3a.1 探针范围建议（基于以上基线）

- 抽取对象：`message_received` payload 构造器 + 用户消息记录用例（四种现存
  形状的差异须可表达——见方案 §2.3），**不碰** `_send_via_gateway` 主体；
- 新模块 `apps/chat/` 下候选名 `messages.py` / `events.py`（最终名实施时定）；
- 接入方式：现有 API 发送路径调用新构造器，事件字段名/顺序/zero-行为变化；
- 验收：契约网 179 passed 基线不降 + 新增逐字段断言用例 + 层方向门禁 0。

## 6. 风险登记（供实施期盯防）

1. **monkeypatch 出口网**（§2.2）：17+16+17 处锚是最大迁移面；3a.3 若直接删掉
   chat 模块符号，这些测试会大面积红——按方案 §7 保持可 patch 过渡面直到
   3a.4 逐项迁移。
2. **装配 helper 跨路由消费者**（§2.1）：`_get_session_service` 等不能随主体搬走。
3. **Attempt 半提交**（方案 §1）：3a.3 必做修复，先验证历史再创建 Attempt。
4. **单例观察器**（:457-499）：迁移中不得重建第二套落库路径。
5. **wire 绝对值**（§4）：HTTP detail / SSE 字段名是前端契约，逐字段断言守护。
