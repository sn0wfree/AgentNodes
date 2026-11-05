# P5-1b 设计 — 新路径补齐流式与工具链（删旧分支的前置）— 2026-10-27

> 前置：P5-1（`af4cb235`）已补齐历史 / 持久化 / message_id。
> 本文补齐删旧分支前还差的两项：**流式输出**、**工具链**。
> 完成后即可删除 `_send_via_gateway` 的旧分支与 `new_entry_enabled` 门。

---

## 1. 现状核查（实测）

| 能力 | 旧路径（AgentLoop） | 新路径（GraphEngine） |
|---|---|---|
| 逐 token 推送 | ✅ `on_event` 回调 → `_PartAccumulator` → `sse_buffer` | ❌ **引擎只发 node.started/completed** |
| 工具调用 | ✅ `build_chat_agent_loop` 自带 tool registry | ⚠ react 节点支持（1b），但 Gateway 未装配 registry |
| LLM 流式能力 | — | ✅ **客户端已支持**：`openai_client.stream()` / `astream()` 返回 `StreamChunk`（含 `delta_content` / `delta_thinking` / `delta_tool_calls` / `usage`） |

**关键结论**：流式不是「接线问题」——**LLM 客户端已就绪，缺的是 llm/react 节点没把 chunk 转成事件**。

## 2. 流式设计

### 2.1 llm 节点支持流式

`make_llm_node(client, *, system=None, budget=None, stream=False, sink=None)`

```
stream=False（默认，保持现状）
  └→ client.chat(messages)           → LLMResponse

stream=True
  └→ client.stream(messages)
       ├→ 每 chunk：sink("text_delta", {run_id, delta})      ← 逐 token
       │           sink("thinking_delta", {run_id, delta})  ← 推理 token（若适配器支持）
       └→ 结束：聚合出完整 content → 与 chat() 同形返回
```

**聚合**：流式结束后节点返回值与非流式**完全一致**（`messages` + `answer` + `usage`），
下游节点与测试不需要区分两条路径。

### 2.2 事件如何到达 SSE

现有链路已就绪，只需接通两个点：

```
GraphEngine(sink=gw._combined_sink)
  └→ Gateway sink → PlatformStore.emit（本地缓冲）
       ↑ 需要转发到 sse_buffer

新增：Gateway 的 sink 增加一个 SSE 转发器
  └→ sse_buffer.push(kind, json.dumps(data), session_id)
```

**注意**：`sse_buffer.push(event_type, data_str, session_id)` 的签名需核对
（现有调用形如 `sse_buffer.push("agent_done", json.dumps({...}), session_id)`）。

### 2.3 事件词表对齐

旧路径前端已消费的事件名必须保持：

| 事件 | 载荷 | 来源 |
|---|---|---|
| `text_delta` | `{message_id, delta}` | llm/react 节点流式 chunk |
| `thinking_start` / `thinking_delta` / `thinking_done` | 同旧 | 节点（若 provider 支持 thinking） |
| `tool_call` / `tool_result` | 同旧 | react 节点（已有 tool.started/completed，需映射） |
| `agent_done` | `{message_id, status}` | `_send_via_gateway` 末尾 |
| `error` | `{message_id, error}` | 失败路径 |

**映射层放哪**：`_send_via_gateway` 内（把引擎事件翻译成前端词表），**不改引擎**。
引擎的 `node.started` / `tool.started` 是平台观测事件，前端词表是产品语义——两者分层。

## 3. 工具链设计

### 3.1 registry 装配

`build_default_registry(workspace)` 已存在（`core/agent/builtin_tools/__init__.py:47`）。

Gateway 构造时传入：

```python
Gateway(
    client=...,
    registry=build_default_registry(workspace),   # 新增参数
    ...
)
```

`SessionController._build_engine` 的默认图从「单 llm 节点」升级为
「单 react 节点（有 registry 时）/ 单 llm 节点（无 registry 时）」：

```python
if self._registry is not None:
    engine.add_node("react", make_react_node(client, registry, ...))
else:
    engine.add_node("llm", make_llm_node(client, ...))
```

**为什么用 react 而非 llm**：react 在无工具调用时**退化为单次 LLM 调用**（1b 已验证），
有工具时自动进入循环——一个节点覆盖两种模式，不需要两个 preset。

### 3.2 工具事件的词表映射

react 节点已发 `tool.started` / `tool.completed`（平台事件，载荷 `{run_id, tool, iteration}`）。
前端要 `tool_call` / `tool_result`。在 `_send_via_gateway` 的 sink 翻译器里映射，并补前端字段
（`message_id`、`arguments`、`result` 摘要）。

**注意**：react 节点的 `tool.completed` 只带 `result_chars`（不传结果体，避免事件膨胀）。
前端若需要展示结果，需从 messages/parts 读——这是现有设计，不改。

### 3.2.1 流式 tool_calls 的聚合（已验证）

`StreamChunk.delta_tool_calls` 是**增量分片**（parser 注释：「arguments may span multiple
chunks」）。流式下必须按 `index` 聚合后才能真正执行工具：

```python
acc: dict[int, dict] = {}
for chunk in stream:
    for tc in chunk.delta_tool_calls:
        idx = tc.get("index", 0)
        slot = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if tc.get("id"):
            slot["id"] = tc["id"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            slot["name"] = fn["name"]
        if fn.get("arguments"):
            slot["arguments"] += fn["arguments"]   # 字符串拼接
# 流结束后 json.loads(slot["arguments"]) → 执行工具
```

**已用真实 OpenAI 分片形态验证**（三片拼出 `{"expr": "17*23"}`；两工具并发按 index
正确分开）。关键点：`id`/`name` 只取首次出现，`arguments` **只能拼接不能覆盖**。

### 3.3 shell 工具的安全性

`build_default_registry()` 默认**不含** shell 工具（`shell_tools.py` 注释明确
「Must be explicitly opted-in」）。API chat 的旧路径通过 `allow_shell_tools` 控制。

**新路径沿用**：`_send_via_gateway` 读 `_shell_tools_enabled()`，按需把 shell 工具
加入 registry（或构造后追加）。**不能默认开启**。

## 4. 实现清单（约 5 个文件）

| 文件 | 改动 |
|---|---|
| `core/runtimes/nodes/llm.py` | 增加 `stream` 与 `sink` 参数；流式聚合 |
| `core/runtimes/nodes/react.py` | 同上（每轮 LLM 调用可流式） |
| `core/gateway/gateway.py` | 新增 `registry` 参数；sink 增加 SSE 转发钩子 |
| `core/gateway/session_controller.py` | 有 registry 时用 react 节点 |
| `api/routers/chat.py` | `_send_via_gateway`：事件词表翻译 + `agent_done` / `error` |

## 5. 验收

1. **流式**：fake client 的 `stream()` 产出 3 个 chunk → SSE 收到 3 条 `text_delta` + 1 条 `agent_done`；
2. **非流式回归**：`stream=False` 行为与现状完全一致（现有测试全绿）；
3. **工具**：fake registry 返回一次工具调用 → 收到 `tool_call` + `tool_result` + 最终答案；
4. **无 registry**：退化为纯 llm 节点，不报错；
5. **shell 门禁**：默认不含 shell 工具；`_shell_tools_enabled()` 为真时才注入；
6. **删除旧分支**（本轮之后）：`new_entry_enabled` 从代码消失，`git grep` 零命中。

## 6. 明确不做

- 思考链 UI 展示（thinking_* 事件仅透传，前端渲染属 WebUI 任务）；
- 工具结果流式（一次性回填，现状如此）；
- agent → tool → agent 的多轮中间态 UI（现有 react 已发事件，前端自行渲染）；
- 删除旧分支本身（本轮只补齐；删除是下一步，需先跑通全链路）。

## 7. 工期

| 项 | 估 |
|---|---|
| llm 节点流式 | 0.4d |
| react 节点流式 | 0.3d |
| registry 装配 + sink 转发 | 0.4d |
| 事件词表映射 | 0.3d |
| 测试与回归 | 0.4d |
| **合计** | **1.8d** |

## 8. 风险

| 风险 | 缓解 |
|---|---|
| 流式聚合与 `chat()` 返回值不一致 | 同一响应对象形状；测试断言 `answer`/`usage` 相等 |
| SSE 转发拖慢主路径 | 转发失败只记日志；`sse_buffer` 本身是内存队列 |
| 事件风暴（每 token 一条） | 沿用现有分级保留（token 事件低值），不做额外合并 |
| react 节点流式时工具调用解析 | `StreamChunk.delta_tool_calls` 是增量，需在节点内聚合后再执行工具 |
| registry 含危险工具 | 默认不含 shell；显式门禁 |
