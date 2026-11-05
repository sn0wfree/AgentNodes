# TUI Streaming v2 — Design Document

> **日期**: 2026-07-27
> **状态**: 设计稿，待实施
> **来源**: 修 "Done.×2 + 内容不显示" bug + 借鉴 vibe-trading 顺序流风格

---

## 一、问题陈述

### 1.1 当前 bug

`_run_agent_loop()` 设置 `stream_mode=False`，plain-text chat 路径走非流式：

```
> A股动量策略
• Done.
• Done.    ← 双重 Done
```

**根因链**：
1. `arun()` 走 `client.achat(messages)` 非流式分支
2. 整个会话过程中**不发 `text_delta` 事件**
3. `response.content` 只存到 `result.answer`，**从未写入 TranscriptView**
4. `iter_end` 事件触发 `append_done()`（第 1 个 Done）
5. `arun` 返回后 `_run_agent_loop` 又调 `append_done()`（第 2 个 Done）

### 1.2 用户体感差距

vibe-trading 风格的核心特征在 `tui-comparison-analysis.md:12-18` 列出：

| 特征 | 当前 | vibe-trading |
|---|---|---|
| Token-by-token 流式 | ✗（批量出现） | ✓ Rich.Live |
| 工具调用内联 | ✗（藏在右侧 rail） | ✓ 顺序流 |
| ⏳ → ✔ 实时状态 | 部分（rail） | ✓ 即时 |
| 工具耗时行内 | ✗ | ✓ |
| 折叠不丢弃 | ✓ head/middle/tail | ✓ |
| ThinkingSpinner | ✓ | ✓ |

---

## 二、设计目标

修 bug 之上，把纯 chat 路径的体感对齐 vibe-trading 80%：

1. **D1**: LLM 内容必须最终显示到 TranscriptView（修 bug）
2. **D2**: Token 逐个流入，不再批量出现（stage B）
3. **D3**: 工具调用内联到 transcript 主区，⏳ → ✔ 实时状态（stage C）
4. **D4**: ToolsRail 仅保留高层状态（goal / iter / compact），不再承担工具时间线
5. **D5**: header tool 计数仍准确，来源迁移
6. **D6**: 单 Done. 收尾，无重复

---

## 三、阶段 A — 修 "Done.×2 + 内容不显示"

### A1. 新事件 `assistant_message`

`_handle_stop()` 在 emit `iter_end` **之前** emit `assistant_message` 携带完整响应内容：

```python
# core/agent/loop.py
def _handle_stop(self, response, result, iteration):
    result.answer = response.content
    result.finished_reason = "stop"
    self._trace({"type": "loop_end", "reason": "stop", "iteration": iteration})
    self._emit("assistant_message", {"content": response.content or ""})  # ← 新
    self._emit("iter_end", {...})
```

同理：
- `_handle_max_iter` —— emit `assistant_message{content: "Reached max_iterations=..."}`
- `_check_no_progress` —— emit `assistant_message{content: response.content or "No progress..."}`

### A2. `route_agent_event["assistant_message"]` 路由

```python
# cli/tui/app.py
elif event_type == "assistant_message":
    content = data.get("content", "")
    try:
        tv = self.query_one(TranscriptView)
        if tv._streamer is None:
            tv.begin_streaming()
        tv._streamer.update_streaming(content)
        tv.end_streaming()  # ← 关键：转 folder，启用 Ctrl+E
    except Exception:
        pass
```

**对称性保证**：
- **流式模式**：`text_delta`s 先累积进 streamer → `assistant_message` 来时调 `end_streaming()` 收尾成 folder
- **非流式模式**：streamer 当场创建 → `update_streaming(content)` → `end_streaming()` 直接产出 folder

### A3. 删除 `_run_agent_loop` 末尾冗余 `append_done()`

```python
# cli/tui/session.py  _run_agent_loop()
# 删除这段（iter_end 已处理）：
# try:
#     tv = self.app.query_one(TranscriptView)
#     tv.append_done()
# except Exception:
#     pass
```

---

## 四、阶段 B — Chat 路径启用 `stream_mode=True`

### B1. 一行参数切换

```python
# cli/tui/session.py:237
loop = AgentLoop(
    ...
    stream_mode=True,   # ← 改 False → True
    ...
)
```

### B2. 行为变化

- `arun()` 自动走 `_astream_chat()` 路径
- LLM streaming chunks 通过 `text_delta` events 流入
- `update_streaming_delta()` 累积 + 原地替换
- `thinking_start` → `thinking_done` → `text_delta`s → `thinking_end` 序列触发正确的 spinner 生命周期
- `assistant_message` 事件来时，streamer 已有完整内容，`end_streaming()` 收尾成 folder

### B3. 错误 fallback（防御性）

如果 `_astream_chat()` 抛错（非网络异常），fallback 到 `client.achat()` 兜底：

```python
# core/agent/loop.py  arun()
try:
    if self._stream_mode:
        response = await self._astream_chat(messages, iteration)
    else:
        response = await self.client.achat(messages)
except LLMError:
    if self._stream_mode:
        # Streaming failed — fall back to non-streaming
        response = await self.client.achat(messages)
    else:
        raise
```

---

## 五、阶段 C — 工具调用内联到 Transcript

### C1. 设计原则

工具调用与文本流**平行**（不混在文本内）。每条工具调用占 1-2 行：

```
⏳ read_file · {"path": "config.yaml"}     ← tool_call
✔ read_file · 0.3s                        ← tool_result（同一行替换）
```

或：

```
⏳ run_backtest · {"strategy": "mom"}
  ✔ 12.4s
  ↳ Sharpe: 1.42, MaxDD: -8.5%           ← result preview（可选）
```

### C2. TranscriptView 新增方法

```python
# cli/tui/widgets/transcript.py
def append_tool_call(self, call_id: str, tool: str, args: dict) -> None:
    """Inline tool start: ⏳ tool_name · <args preview>"""
    args_str = json.dumps(args, ensure_ascii=False)
    if len(args_str) > 80:
        args_str = args_str[:77] + "..."
    line = f"[muted]\u23f3 [bold]{tool}[/bold] \u00b7 {args_str}[/muted]"
    self.write(line)
    self._tool_lines[call_id] = len(self.lines) - 1

def update_tool_result(self, call_id: str, ok: bool, elapsed_ms: int) -> None:
    """Inline tool end: ✔/✘ tool_name · 0.3s"""
    if call_id not in self._tool_lines:
        return
    line_idx = self._tool_lines.pop(call_id)
    tool = self._tool_results.pop(call_id, "?")  # cached at call time
    elapsed_s = elapsed_ms / 1000
    elapsed_str = f"{elapsed_s:.1f}s" if elapsed_s >= 1 else f"{int(elapsed_ms)}ms"
    symbol = "\u2714" if ok else "\u2718"
    style = "success" if ok else "error"
    new_line = f"[{style}]{symbol} [bold]{tool}[/bold] \u00b7 {elapsed_str}[/{style}]"
    # Replace in-place: truncate to baseline, write new
    self._truncate_to(line_idx)
    self.write(new_line)
```

### C3. route_agent_event 路由调整

```python
# cli/tui/app.py
def route_agent_event(self, event_type, data):
    if event_type == "text_delta":
        self.update_streaming_delta(data.get("text", ""))
    elif event_type == "assistant_message":
        self._finalize_assistant_message(data.get("content", ""))
    elif event_type == "tool_call":
        # ← 不再 write_rail
        try:
            tv = self.query_one(TranscriptView)
            tv.append_tool_call(data["call_id"], data["tool"], data.get("args", {}))
        except Exception:
            pass
    elif event_type == "tool_result":
        try:
            tv = self.query_one(TranscriptView)
            tv.update_tool_result(
                data["call_id"],
                data.get("ok", True),
                data.get("elapsed_ms", 0),
            )
        except Exception:
            pass
    elif event_type == "tool_progress":
        pass  # 暂不实现（未来可显示进度）
    elif event_type == "tool_heartbeat":
        pass  # 静默（未来可显示 "running 2.3s"）
    elif event_type == "compact":
        # 保留 rail 显示压缩状态
        self.write_rail("compact", data)
    # iter_start / iter_end / llm_usage / error 不变
```

### C4. ToolsRail 角色重新定位

**保留**（高层状态）：
- GOAL section
- ITER section（`set_iter`）
- COMPACT section（压缩事件）

**移除**（迁移到 transcript）：
- TIMELINE section
- `handle_event` 中 `tool_call` / `tool_result` / `tool_progress` / `tool_heartbeat` 分支
- `_timeline: list[TimelineEntry]` 状态

```python
# cli/tui/widgets/tools_rail.py
class ToolsRail(...):
    def handle_event(self, event_type, data):
        if event_type == "compact":
            self._handle_compact(data)
        # 删 tool_call / tool_result / tool_progress / tool_heartbeat
```

保留一个 hint：

```python
# 当无 goal / iter / compact 时显示
self.write("[muted](tool calls shown inline)[/muted]")
```

### C5. Tool 计数来源迁移

```python
# cli/tui/session.py
class ChatSession:
    def __init__(self, ...):
        ...
        self._tool_total = 0
        self._tool_ok = 0

# 在 dispatch() 中通过 app.route_agent_event 暴露的钩子递增；
# 或更简洁：让 App 维护 self.tool_count / self.tool_ok，
# session 调 app.update_header(tool_count=..., tool_ok=...)
```

`_update_header_stats()` 改为读 session 的字段而非 rail 的 `_timeline`：

```python
# cli/tui/session.py:_update_header_stats()
tool_count = self._tool_total
tool_ok = self._tool_ok
```

---

## 六、阶段 D — 测试矩阵

| 测试文件 | 覆盖 |
|---|---|
| `test_assistant_message_event.py`（新增） | A1-A3：单 Done、folder 生成、content 不丢失 |
| `test_streaming_chat_path.py`（新增） | B1-B3：chat 路径流式行为、spinner 启停、fallback |
| `test_inline_tools.py`（新增） | C1-C2：append_tool_call/update_tool_result、状态转换、args 截断 |
| `test_tools_rail_compact_only.py`（新增） | C4：rail 只剩 goal/iter/compact |
| `test_header_tool_count.py`（新增） | C5：tool count 来源迁移 |
| `test_tui_event_routing.py`（更新） | tool_call/result 路由到 transcript 不再 rail |
| `test_stage4_tools_rail.py`（更新） | 删除 tool_call/result 测试，保留 compact/goal/iter |
| `test_cli_llm_streaming.py`（更新） | chat 路径 mock 适配 |

---

## 七、阶段 E — 风险与回滚

| 风险 | 缓解 |
|---|---|
| Tool 行 baseline 与 transcript baseline 冲突 | transcript._tool_lines 与 _fold_baselines 各自维护，独立 truncate |
| 流式中途工具调用打断 streamer | `tool_call` 之前自动 end_streaming（`begin_streaming` 已处理 folder 折叠，无需改动） |
| ToolsRail 删能力后视觉空荡 | rail 加 hint: `(tool calls shown inline)` |
| `_run_agent_loop` 开 stream_mode 后稳定性 | 现有 `_astream_chat` 已在 stage 1 测试覆盖；fallback 到 `achat` |

---

## 八、视觉对照

参见 `tui-redesign-plan.md` 第五章 ASCII art：
- 纯 chat 路径：完整内容 + 折叠
- 带工具调用：流式内容 + 内联工具（⏳ → ✔）
- 长工具：心跳指示
- 失败工具：✘ + 错误信息
- Ctrl+E 折叠/展开

---

## 九、不做的事

- 不改三栏整体布局（保留 sidebar + transcript + rail）
- 不引入新 widget 类型（tool 行就是 RichLog 行）
- 不改 spinner 视觉（继续用品牌橙色 ThinkingSpinner）
- 不做多行输入 / 模糊补全（超出本次范围）
- 不迁移 chat 之外的 tool-heavy 路径（`/swarm`、`/journal` 继续走 rail）

---

## 十、相关文档

- `tui-display-philosophy.md` — 4 大设计原则
- `tui-comparison-analysis.md` — 与 vibe-trading 对照分析
- `streaming-implementation.md` — 上版（v1）流式实现计划
- `vibe-trading-core-patterns.md` — vibe-trading 架构模式提炼