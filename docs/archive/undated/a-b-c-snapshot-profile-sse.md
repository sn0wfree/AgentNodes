# A+B+C — CompactionStep snapshot / Profile YAML / SSE 收尾

> **Status:** A ✅ 完成（CompactionStep 通过 Projector flush 间接写 snapshot）；B ⏭ 待实施；C ⏭ 待实施
> **承接:** L7 v0.5 完成步骤链迁移。本 patch 处理三个收尾任务。

## A: CompactionStep snapshot 集成

**已完成**。CompactionStep 迁移让骨架调用 `_maybe_compact`（L7 v0.5），
`_maybe_compact` 内部调 Projector flush → snapshot 写入（P0-1 B2 机制）。
无需额外代码——snapshot interval 已由 `Projector.flush(snapshot_interval=200)` 在
`EventStore._should_flush` 触发时执行。

## B: Profile YAML loop_strategy 集成

`build_agent_loop()` / `run_agent_via_llm()` 新增 `loop_strategy` 参数，
通过 `resolve_loop_strategy()` 解析后传给 `AgentLoop(strategy=...)`。

```python
def build_agent_loop(role, workspace_path, strategy_name, *, loop_strategy=None):
    ...
    return AgentLoop(..., strategy=loop_strategy)

def run_agent_via_llm(role, ..., loop_strategy=None):
    ...
    return build_agent_loop(role, ..., loop_strategy=loop_strategy)
```

## C: SSE 双路径统一（v0.1 状态更新）

SSE 双路径（EventStore + legacy EventBus → SSEEventBuffer）在 L7 系列完成后
已通过 `bridge_v2.py` 稳定：EventStore 是唯一事件源，EventBus 是兼容层。
**v0.1 不统一**——移除 legacy EventBus 路径风险太大（涉及 autoresearch + study）。

后续 P0-2 或独立 PR 做统一。
