# Study Chat 数据流文档

## 概述

Study Chat 显示 agent 执行过程中的消息，包括实时事件和历史记录。本文档描述完整的数据流架构。

## 数据流架构

### 1. 实时流式（Study 运行中）

```
AgentLoop._emit(event_type, data)
    │
    ├──→ _forward_event() 适配器
    │       │
    │       ├──→ history.append()  ← 收集到内存列表（用于持久化）
    │       │
    │       └──→ runner._emit(study_id, f"agent_{event_type}", data)
    │               │
    │               └──→ EventStore.emit() → sse_buffer.push()
    │                       │
    │                       └──→ SSE /api/chat/events → 前端
    │
    └──→ Agent 执行完成
            │
            └──→ save_agent_outputs()
                    ├──→ agents/{agent_id}.json  ← 最终输出
                    └──→ agents/{agent_id}_history.json  ← 完整历史
```

**前端处理：**
```
SSE 事件到达
    ↓
handlers.ts: agent_thinking_start → addLiveEvent()
    ↓
useStudyStore.recentEvents 更新
    ↓
StudyChat: recentEvents 变化 → buildAgentEventMessage()
    ↓
chatStore.addMessage() → MessageList 渲染
```

### 2. 历史加载（Study 完成后）

```
Study 状态: interrupted / completed / error
    ↓
StudyChat 检查 summary.execution_status
    ↓
status !== 'running' → include_history=true
    ↓
API: GET /rounds/{N}/agent_outputs?include_history=true&history_limit=200
    ↓
后端: 读取 agents/*.json + *_history.json（截断到 200 条）
    ↓
返回: 8 agents × (output + history[0:200])
    ↓
buildMessagesFromOutputs()
    ↓
遍历 history 事件 → 转换为 MessagePart[]
    ↓
返回 Message[] 包含多 parts
    ↓
chatStore.addMessage() → AssistantMessage 渲染
```

## 关键组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `_forward_event` | `langgraph_engine.py:148-165` | AgentLoop 事件适配器：收集历史 + 转发 SSE |
| `save_agent_outputs` | `engine_common.py:114-138` | 持久化 agent 输出 + 执行历史 |
| `roundAgentOutputs` API | `routers/study.py:1119` | 返回 agent 输出（可选 history） |
| `handlers.ts` agent_* | `handlers.ts:155-193` | 处理 SSE agent 事件 |
| `buildMessagesFromOutputs` | `StudyChat.tsx:126-220` | 将 history 转换为多 part 消息 |
| `buildAgentEventMessage` | `StudyChat.tsx:265-330` | 将 SSE 事件转换为聊天消息 |
| `AssistantMessage` | `AssistantMessage.tsx` | 渲染消息（支持 thinking, tool_call, text） |

## 事件类型

| 事件类型 | 来源 | 用途 |
|---------|------|------|
| `agent_thinking_start` | AgentLoop._emit | agent 开始思考 |
| `agent_thinking_done` | AgentLoop._emit | agent 思考完成 |
| `agent_tool_call` | AgentLoop._emit | agent 调用工具 |
| `agent_tool_result` | AgentLoop._emit | agent 工具返回结果 |
| `agent_text_delta` | AgentLoop._emit | agent 输出文本增量 |
| `agent_assistant_message` | AgentLoop._emit | agent 最终消息 |
| `agent_loop_end` | AgentLoop._emit | agent 执行结束 |
| `study_agent_complete` | runner._emit | agent 完成（粗粒度） |

## 消息格式

### Message 类型
```typescript
interface Message {
  id: string
  session_id: string
  role: 'user' | 'assistant' | 'system'
  agent_id?: string
  parts: MessagePart[]
  created_at: number
  metadata?: { kind?: string; round?: number }
}
```

### MessagePart 类型
```typescript
type MessagePart = TextPart | ToolCallPart | ThinkingPart | ...

interface TextPart { type: 'text'; id: string; text: string }
interface ToolCallPart { type: 'tool_call'; id: string; name: string; arguments: any; status: string }
interface ThinkingPart { type: 'thinking'; text: string; collapsed: boolean }
```

## API 端点

### GET /study/{id}/rounds/{N}/agent_outputs

返回 agent 输出（不含 history）。

**参数：**
- `include_history` (bool, default false)：是否包含执行历史
- `history_limit` (int, default 500)：history 事件数上限

**响应：**
```json
{
  "status": "ok",
  "study_id": "...",
  "round": 1,
  "agent_outputs": {
    "researcher": {
      "agent": "researcher",
      "output": "...",
      "history": [...]  // 仅当 include_history=true
    }
  }
}
```

**大小：**
- 不含 history：~1.5KB
- 含 history (limit=200)：~1.5MB

## 与正常 Chat 的对比

| 方面 | 正常 Chat | Study Chat |
|------|-----------|------------|
| 实时流式 | SSE → chat store → AssistantMessage | 相同 ✅ |
| 历史持久化 | DB (messages + message_parts) | 文件 (*_history.json) |
| 页面刷新加载 | GET /session/{id}/messages | GET /rounds/1/agent_outputs?include_history=true |
| 中间步骤显示 | DB 持久化，刷新后可恢复 | history 文件，完成后加载 ✅ |
| 消息格式 | Message + MessagePart[] | 相同格式 ✅ |
| 渲染组件 | AssistantMessage | 相同组件 ✅ |
