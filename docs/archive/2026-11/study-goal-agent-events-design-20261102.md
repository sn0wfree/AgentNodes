# 设计 — study/goal 的 agent 事件接线（2026-11-02）

> 用户决策：先补 study/goal 事件接线；日志统一入口后议；CLI/MCP 的 JSON
> 日志走 `AN_LOG_JSON` 开关。
> 分支：`refactor/v6-gateway`。

## 0. 问题

同一次 agent 执行，chat 路径与 study/goal 路径的可观测性完全不对称：

| 路径 | agent 级事件（llm_request / iter_start / tool_call / …） |
|---|---|
| chat | ✅ `service.py:937 on_event=forwarder` → event_log（Trajectory View 单一事实源） |
| **study** | ❌ `role_factory.build_agent_loop` 不接受 `on_event`——9 个研究 agent 的全部执行轨迹**丢弃** |
| **goal/swarm** | ❌ `executor.execute` 支持 `on_event`（`executor.py:136/421` 已传 AgentLoop），但 `swarm/runtime.py:580` 调用时没传 → **丢弃** |

研究是这个项目的核心业务；核心业务的 agent 轨迹不可见，是当前最大的观测盲区。

## 1. 现状链路（实测）

**study 已有的一半**：`study_*` 汇总事件已经进 event_log——
`StudyScheduler._make_emitter` (`scheduler.py:1025-1041`) 在
`session_service.event_bus` 存在时构造 `_EventBusEmitter(session_id, bus)`，
runner 的 `_emit` (`runner.py:1139`) 经 `state_utils.emit_with_trace` 补上
ContextVar 的 trace_id/study_id/round_num 后发到 EventStore。缺的只是
agent 级事件。

**ContextVar 已就位**：`_run_one_round` (`runner.py:958-969`) 在 round
外层 `bind_trace(trace_id, study_id, round_num)`；`EventStore._inject_trace_context`
(`event_store.py:145-179`) 会自动把这些字段并入事件 data。也就是说：
只要 agent 事件走 `runner._emit`，trace 字段免费获得。

**goal 已有的一半**：`WorkflowEventBus`（`goal/event_bus.py`）已有
agent_start/agent_complete/agent_error 三个层级事件（observer 总线，
LoggerObserver 落日志 / GoalPanelObserver 推 UI），但 agent **内部**的
迭代/工具/LLM 调用不可见。

## 2. 设计

### 2.1 词汇表：`SUMMARY_EVENT_TYPES`（新，唯一来源）

`core/observability/event_vocab.py`：

```python
SUMMARY_EVENT_TYPES = frozenset({
    "llm_request", "llm_response", "llm_usage",
    "loop_start", "loop_end", "loop_final",
    "iter_start", "iter_end",
    "tool_call", "tool_result", "tool_error",
    "compression", "compact", "error",
    "agent_approval_requested", "budget_exhausted",
})
```

**为什么必须过滤**：AgentLoop 的 `_emit` 对每个流式 chunk 都发
`text_delta`/`thinking_delta`（`loop_stream.py:190-206`）。chat 转发全量
是因为 SSE 需要；study 一轮 9 agent × 每 LLM 调用几百 chunk，不过滤就是
每轮几千行 event_log。总结级词汇与 `TraceProjection` 的投影口径一致。

### 2.2 study 链：on_event 透传 + agent 名注入

透传（全部加 `on_event: Callable | None = None`，默认 None 行为不变）：

```
build_agent_loop        (role_factory.py)   → AgentLoop(on_event=...)
run_agent_via_llm       (role_factory.py)   → build_agent_loop
spawn_agent             (autoresearch.py)   → run_agent_via_llm
_make_spawn_fn          (autoresearch.py)   → spawn_agent，并在 _spawn 内包装：
    on_event' = (et, d) => on_event(et, {**d, "agent": name})
run_researcher_phase / run_execution_phase / run_evaluation_phase
                        (autoresearch.py)   → _make_spawn_fn
```

**agent 名注入点选 `_make_spawn_fn`**：只有它知道当前 spawn 的是哪个
agent（phase 函数只传一个工厂下去）。事件带上 `agent` 字段后，一轮内
9 个 agent 的事件才能区分归属。

### 2.3 StudyRunner：适配到已有 emitter

```python
def _make_agent_on_event(self, session_id: str):
    sink = self._emit
    def on_event(event_type, data):
        if event_type in SUMMARY_EVENT_TYPES:
            sink(session_id, event_type, dict(data))
    return on_event
```

- `self._emit` → `emit_with_trace`（补 trace 字段）→ `self.emitter`
  → 有 service 时 EventStore.emit（进 event_log + SSE + projector），
  无 service 时 NullEmitter（no-op，零成本）。
- `phase_engine._run_phases_branch` / `_run_execution_with_parse_retry`
  从 runner 拿此回调传给三个 phase 函数。

### 2.4 goal/swarm 链

- `SwarmRuntime.__init__(..., on_event=None)`；`_execute_llm_executor`
  里传给 `executor.execute(..., on_event=包装)`，包装注入
  `{"agent": plugin_id}` 并过滤白名单（过滤放 runtime 侧：调用方拿到的
  必然是干净流，责任单一）。
- goal workflow 三处 `SwarmRuntime()` 构造传：
  `on_event=lambda et, d: self._event_bus.emit(et, **d)`
  （`emit(event, **data)` → `observer.on_event(event, dict(data))`，
  数据面安全；agent 级事件与既有 agent_start/agent_complete 层级事件
  并存于同一总线，观察者按类型自取）。
- goal 侧**不**进跨进程 event_log（goal 执行宿主的 EventStore 归属
  属于「日志统一入口」轮，已延后）。本轮 goal 只到 WorkflowEventBus。

### 2.5 CLI/MCP：`AN_LOG_JSON` 开关

`setup_trace_logging()` 幂等（filter 只挂一次）且 `json_output=None`
时读 `AN_LOG_JSON`。实施时发现两个入口的约束不同，最终形态：

- **CLI/TUI**：`AN_LOG_JSON=1` 才装配整条管线（basicConfig stderr handler
  + JSON 格式 + trace 过滤）；默认**零改动**——TUI 是全屏交互界面，无条件
  挂 handler 会让 523 处 `logger.info` 刷穿渲染。
- **MCP stdio**：总是装配（此前进程无任何 handler，INFO 日志全部不可见，
  只剩 lastResort 的 WARNING 兜底）。日志落 stderr（`basicConfig` 默认流），
  stdout 只走协议帧，`AN_LOG_JSON=1` 同样生效。

## 3. 不做什么

- **不**把日志行写进 event_log（那是「统一入口」轮的决策，用户已延后）。
- **不**给 `dag_planner.py:176`、`runner_knowledge.py:41` 的
  run_agent_via_llm 调用接事件（一次性规划/知识抽取，事件价值低；
  参数已具备，需要时一行接上）。
- **不**改 chat 路径（它已完整，全量转发是有意的 SSE 语义）。
- **不**改 TraceProjection 的投影逻辑（词汇表趋同是后续收敛点）。

## 4. 验收

1. 新测试：透传链每层、agent 名注入、白名单过滤（高频类型被滤）、
   runner sink 的 trace 字段注入、swarm 包装、goal 观察者收到、
   NullEmitter 零副作用。
2. 行为等价：默认 `on_event=None` 全链不变；四道门
   （cov_gate 四档 ≥90 / c1_gate 不扩大 / inventory 只增 / e2e）。
3. 文档：observability.md 补一节「study/goal agent 事件」。

## 5. 风险

- **事件量**：白名单后每 agent 每轮约 30–60 行（llm_request/response +
  iter + tool）。一轮 9 agent ≈ 300–500 行/轮，与现有 study_* 事件同量级，
  event_log 增长可控（blob offload 已有，llm_request 大字段不进 data）。
  注意：`llm_request` 的 system_prompt/tools_schema 大字段 offload 逻辑在
  chat 的 `_LoopEventForwarder` 里——study 路径直发 EventStore 不经
  forwarder，大字段会**直接进 event_log**。缓解：runner sink 里对
  `llm_request` 剥离 `system_prompt`/`tools_schema` 两个键（study 的
  system prompt 每轮固定，排障价值低、体积大）。
