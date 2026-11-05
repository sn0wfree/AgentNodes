# Phase 1: 诊断与抽象 — 详细分析报告

> 角色：软件架构师。本阶段只梳理现有逻辑，不改变代码结构。

## 1. 核心职责清单（12 项）

| # | 职责 | 主要承载位置 | 代码量 |
|---|---|---|---|
| 1 | **LLM 调用（含流式）** | `core/agent/loop.py:_astream_chat` | ~250 行 |
| 2 | **工具调用** | `core/agent/loop.py:_aexecute_tool_batch` + `core/agent/builtin_tools/` | ~200 行 |
| 3 | **记忆/历史管理** | `ctx.history` (TUI) / `_session_histories` (chat.py:71) / SQLite | ~10 行散落 |
| 4 | **提示词构建** | `_get_system_prompt()` (chat.py:96) + `cli/tui/_CHAT_PROMPT_PATH` + `role_factory._load_role_system_prompt` | ~50 行 |
| 5 | **事件流（on_event 回调）** | `core/agent/loop.py:_emit` + `chat.py:218-283` + `session.py:264` | ~150 行 |
| 6 | **输出解析** | `core/llm/parser.py:parse_stream_chunk` + AgentLoop 内累积 | ~100 行 |
| 7 | **压缩（Compaction）** | `core/agent/loop.py:_amaybe_compact` + `register_compaction_persister` | ~170 行 |
| 8 | **重试/异常处理** | `core/workflow/controller.py:ControllerConfig` + AgentLoop `try/except LLMError` | ~80 行 |
| 9 | **SSE 推流** | `sse_buffer.push` + `chat.py:on_event` 闭包 | ~50 行 |
| 10 | **TUI 路由** | `app.route_agent_event` + `cli/tui/app.py` | ~80 行 |
| 11 | **持久化** | `persist_message()` (web_session.py) + `ctx.history.append()` | ~30 行 |
| 12 | **Goal 模式适配** | `GoalWorkflowRunner` + `SwarmRuntime` + `_AgentConfigExecutor` | ~870 行 |

## 2. DRY 原则违背点（5 类）

### 2.1 违背点 A: AgentLoop 构造参数（最严重）

`chat.py:297-308` 与 `session.py:260-271` 各 12 行重复构造 AgentLoop。

### 2.2 违背点 B: 事件 → parts 累加逻辑

`chat.py:223-283` 的 `on_event` 闭包 60 行处理 text/tool/thinking 事件累加。

### 2.3 违背点 C: system_prompt 加载

3 套并行路径，失败 fallback 不一致：
- `chat.py:_get_system_prompt` → `templates/.prompts/chat.md` → fallback `_CHAT_PROMPT_PATH`
- `cli/tui/_CHAT_PROMPT_PATH`
- `role_factory._load_role_system_prompt` (Goal 模式)

### 2.4 违背点 D: history 注入

3 个不同来源：
- `chat.py:_session_histories` (模块级 dict，内存泄漏)
- `session.py:ctx.history` (TUI 上下文)
- `web_session.py:store.py` (SQLite)

### 2.5 违背点 E: compact_config 来源

- `chat.py:307` → `cfg.compact_config`
- `session.py:270` → `(cfg or self.llm_client.config).compact_config`

## 3. 数据流向图

```
INPUT (POST /chat/send | TUI dispatch)
   ↓
PRE-PROCESS
   • _build_llm_config()      ← env + dotenv
   • _get_system_prompt()     ← templates/.prompts/chat.md
   • build_default_registry() ← builtin_tools
   • _get_or_create_history() ← module-level dict (LEAK!)
   ↓
CORE EXECUTION (AgentLoop.arun)
   for iteration in range(1, max_iterations+1):
     ├─ _amaybe_compact(messages)         ← Token 压缩
     ├─ _emit("iter_start", ...)          ← ⚠ 回调
     ├─ _astream_chat(messages, iter)     ← LLM 流式
     │    ├─ client.astream() → chunks
     │    ├─ _emit("text.started", ...)
     │    ├─ _emit("text_delta", {text_id, text}) × N
     │    ├─ _emit("thinking_delta", ...) × N
     │    ├─ _emit("llm_usage", ...)
     │    └─ _emit("text.ended", ...)
     ├─ if response.has_tool_calls():
     │    ├─ _aexecute_tool_batch(tc)  ← ⚠ 同步工具
     │    ├─ _emit("tool_call", {id, name, args})
     │    ├─ _emit("tool_result", {id, result})
     │    └─ _emit("tool_progress", ...)
     ├─ else:
     │    └─ _handle_stop() / break
     └─ _check_no_progress() / _check_goal_continuation()
   ↓
OUTPUT (on_event 回调双路分发)
   • chat.py on_event:
     ├─ sse_buffer.push(event_type, json, session_id)  → Web SSE
     └─ accumulated_parts.append(...)                  → 持久化
   • session.py on_event:
     └─ app.route_agent_event(type, data)               → TUI
   ↓
PERSISTENCE
   • history.append(user_message)
   • history.append(assistant_message)
   • persist_message(role="assistant", content, parts, metadata)
```

## 4. 最脆弱环节（7 个）

| # | 脆弱点 | 位置 | 后果 | 当前缓解 |
|---|---|---|---|---|
| 🔴1 | 流式 chunk 解析失败 | `loop.py:_astream_chat` | 单 chunk 失败 → 整个 stream 失败 → 整个 agent 失败 | `_is_stream_required_error` 降级到 `achat()` |
| 🔴2 | `on_event` 回调异常 | `loop.py:_emit` | warning + swallow | 仅 `logger.warning`，**不传播** |
| 🟡3 | `_session_histories` 内存泄漏 | `chat.py:71-75` | 长期运行 session 永不释放 | TODO 标注，等新服务层替换 |
| 🟡4 | `text_delta` 无 `text_id` | `chat.py:233-238` | 丢弃 chunk（数据丢失） | `logger.warning` + skip |
| 🟡5 | `_execute_tool_call` 同步工具在 async 路径 | `loop.py:_execute_tool_call` | 阻塞 event loop | `asyncio.to_thread` 包装 |
| 🟢6 | 工具调用无去重/重复检测 | `loop.py:_check_no_progress` | 相似 tool_call 重复 → token 浪费 | `_check_no_progress` 检测 hash |
| 🟢7 | `max_iterations` 边界 | `loop.py:803` | 到 8 仍不收敛 | `_handle_max_iter` 兜底 |

## 5. Agent 状态清单（9 项）

| 状态项 | 位置 | 跨 turn 持久化 |
|---|---|---|
| **会话历史 `history`** | `ctx.history` / `_session_histories[session_id]` / SQLite | 部分 |
| **当前迭代计数 `iteration`** | `AgentLoop._iteration_counter` | 否 |
| **压缩触发计数 `_compaction_count`** | `AgentLoop._compaction_count` | 否 |
| **工具调用次数 `tool_calls_made`** | `LoopResult.tool_calls_made` | 否（结果用） |
| **Token 计数（估算）** | `_estimate_chars` (worker.py:84) | 否 |
| **Goal 续跑计数** | `session.py:_goal_continuation_paused` | 否（TUI 内存） |
| **halt 状态** | `cli.halt._is_halted()` | 否 |
| **mode (chat/goal)** | `ctx.interactive_mode` | 否 |
| **`_goal_continuation_paused`** | `session.py:406` | 否 |

### 5.1 状态混乱的 4 个核心问题

1. **同份状态 3 个来源**：会话历史分散在 chat.py dict / TUI ctx / SQLite，互相不同步
2. **状态散落在 dict/ctx/模块级变量/LoopResult**：无法统一快照、无法断点续跑
3. **跨 turn 状态仅在 TUI 内存**：重启即失
4. **没有不可变的 AgentState**：函数间传递 dict，难以追溯修改来源

## 6. 跨模式状态对比

| 状态 | Chat 模式 | Goal 模式 |
|---|---|---|
| 会话历史 | `ctx.history` / `_session_histories`（内存） | `GoalStore` (SQLite) |
| 单 turn 上下文 | AgentLoop 内部 | SwarmRuntime._state |
| Agent 执行结果 | `result.answer` (LoopResult) | `result.agent_results` (SwarmResult) |
| 持久化 | 部分（chat.py 内存泄漏） | 完整 (GoalStore + CheckpointStore) |
| 断点续跑 | 无（history 本身是"续跑"） | 有 (CheckpointStore + pre_completed) |
| Token 计数 | 估算（_estimate_chars） | 真实（usage metadata） |

## 7. 总结

12 个核心职责、5 类 DRY 违背、7 个脆弱点、9 项状态散乱 — 已识别完整问题清单。

进入 Phase 2: 设计 11 个 Protocol 接口作为契约锚点。
