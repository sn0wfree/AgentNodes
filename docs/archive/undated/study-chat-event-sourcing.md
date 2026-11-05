# Study Chat Event Sourcing 设计文档

## 背景

Study agent 的执行轨迹当前保存为 JSON 文件（`{agent_id}_history.json`），前端手动拼装 Message。
Chat 系统使用 EventStore + Projector 架构：事件写入 `event_log`，Projector 投影为 `messages` + `message_parts`。

目标：Study agent 也走 EventStore + Projector，完全复用 chat 的渲染管线。

## 架构对比

### 当前（JSON 文件）

```
AgentLoop._emit()
  → _forward_event()
    → history.append()           (内存列表)
    → emit_fn(SSE)               (实时推送)
    → 写入 {agent_id}_history.json (JSON 文件)

前端加载:
  GET /study/{id}/rounds/{n}/agent_outputs
    → 读取 JSON 文件
    → buildMessagesFromOutputs()  (手动拼 Message)
    → MessageList + AssistantMessage
```

### 目标（EventStore + Projector）

```
AgentLoop._emit()
  → _forward_event()
    → EventStore.emit(session_id, event_type, data)
      → INSERT INTO event_log     (源数据)
      → Projector.project_incremental()
        → messages + message_parts (物化视图)
      → SSE push                  (实时推送)

前端加载:
  GET /chat/session/{sessionId}/messages
    → Projector.project_to_messages()
    → 返回 Message[] 带 parts + agent_id
    → MessageList + AssistantMessage
```

## Session 设计

每个 study 每轮一个 session：

```
session_id = "study:{study_id}:round:{round_num}"
```

例如：`study:study_f48295053041:round:1`

每个 agent 的消息通过 `message_id` 区分：

```
message_id = "study:{study_id}:r{round_num}:{agent_id}"
```

例如：`study:study_f48295053041:r1:researcher`

## 事件映射

| AgentLoop 事件 | 写入 event_log | Projector 处理 |
|---------------|---------------|---------------|
| `text_delta` | `text_delta` | 累积到 TextPart |
| `text.started` | `text.started` | 创建 TextPart |
| `text.ended` | `text.ended` | 完成 TextPart |
| `tool_call` | `tool_call` | 创建 ToolCallPart |
| `tool_result` | `tool_result` | 更新 ToolCallPart |
| `thinking_start` | `thinking_start` | 创建 ThinkingPart |
| `thinking_delta` | `thinking_delta` | 累积到 ThinkingPart |
| `thinking_end` | `thinking_end` | 完成 ThinkingPart |
| `assistant_message` | `assistant_message` | 创建/更新 Message |
| `loop_start` | `loop_start` | 跳过（无 handler） |
| `iter_start` | `iter_start` | 跳过（无 handler） |
| `llm_request` | `llm_request` | 跳过（无 handler） |
| `llm_response` | `llm_response` | 跳过（无 handler） |

注意：`text_delta` 仍然写入 event_log（Projector 需要它来累积文本）。

## agent_id 传播

1. `_forward_event` 注入 `agent_id` 到 event data
2. Projector `_ensure_assistant_message` 从 event data 读取 `agent_id`，存入 `ProjectedMessage.metadata`
3. `_row_to_message` 从 metadata 提取 `agent_id` 作为顶层字段返回
4. 前端 `AssistantMessage` 读取 `message.agent_id` 渲染 agent 样式

## 历史数据迁移

将现有 JSON 文件数据迁移到 event_log：

1. 读取 `{agent_id}_history.json`
2. 跳过 `text_delta`、`thinking_delta`、`llm_usage`（Projector 不需要）
3. 为每个事件注入 `message_id` 和 `agent_id`
4. 调用 `EventStore.emit()` 写入 event_log
5. 读取 `{agent_id}.json` 的 `output` 字段，写入 `assistant_message` 事件

## 前端改动

1. StudyChat 加载历史消息改用 `chatStore.loadMessages(sessionId)`
2. SSE 事件映射：去掉 `agent_` 前缀，走 chat 的 handler
3. 保留 `AgentCardView`、`TimelineView` 等自造组件（后续清理）

## 文件清单

| 文件 | 改动 |
|------|------|
| `projector.py` | `_ensure_assistant_message` 传播 agent_id |
| `web_session.py` | `_row_to_message` 返回 agent_id |
| `langgraph_engine.py` | `_forward_event` 改用 EventStore |
| `study.py` (router) | 迁移 API |
| `StudyChat.tsx` | 改用 chat session API |

## 实施要点（踩坑记录）

### DB 路径解析

`resolve_session_db_path()` 按 cwd 解析 DB 路径。后端从 workspace 启动时用的是
`<workspace>/.quantnodes_strategy_research_session.db`，而脚本从仓库目录运行时
会解析到仓库本地的另一个 DB。**任何写入 session DB 的代码都必须显式传入
workspace 的 DB 路径**（见 `engine_common.get_study_session_db_path`）。

### Session 归属（IDOR）

`sessions.user_id` 必须是真实后端用户（非 `system`），否则 session API 的
`_fetch_session_owned` 检查会 404。归属通过继承现有 session 中最常见的
非 system 用户获得（见 `engine_common.ensure_study_session`）。

### 写入性能

`EventStore.emit()` 每条事件独立提交（大 DB 上 ~28ms/条）。迁移脚本改为
单事务批量 INSERT（2500 条 < 1s）。live 路径仍走 `emit()`（需要 SSE 推送
和增量 projector flush，逐条语义正确）。

### Projector 实时物化

EventStore 需 `flush_to_messages=True` 才会在边界事件（`assistant_message`、
`iter_start` 等）时物化 messages 表。API 容器已是 True；langgraph engine
的 factory 单例也必须显式传 True。

### 冗余事件

AgentLoop 自身已通过 `_forward_event` 发出 `assistant_message`（带注入的
message_id），engine 层无需再显式补发，否则会重复触发边界处理。
