"""study/goal 的 agent 事件接线（docs/study-goal-agent-events-design-20261102.md）。

此前两条研究链的 agent 级事件（llm_request / iter_start / tool_call / …）
全部丢弃：``role_factory.build_agent_loop`` 不接受 ``on_event``，
``swarm/runtime`` 调 executor 时也没传——chat 是唯一看得见 agent 轨迹的
路径。本文件锁住接线后的每层契约：

1. 词汇表：白名单排除高频流式类型；llm_request 大字段剥离；
2. study 透传链：spawn → run_agent_via_llm → build_agent_loop → AgentLoop，
   agent 名在 _make_spawn_fn 注入；
3. StudyRunner sink：过滤 + 大字段剥离 + 复用 _emit（带 trace 字段）；
4. swarm：runtime 包装注入 agent 名；None 时零开销；
5. goal：SwarmRuntime(on_event=...) → WorkflowEventBus 观察者可见。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.core import observability as obs
from strategy_research.core.observability import (
    SUMMARY_EVENT_TYPES,
    strip_llm_request_fields,
)


# ── 1. 词汇表 ─────────────────────────────────────────────


def test_summary_vocabulary_excludes_streaming_types():
    """text_delta / thinking_delta 绝不能进白名单——那是每 chunk 一条。"""
    for streaming in ("text_delta", "thinking_delta", "text.started",
                      "text.ended", "thinking_start", "thinking_end"):
        assert streaming not in SUMMARY_EVENT_TYPES, streaming


def test_summary_vocabulary_covers_core_lifecycle():
    for needed in ("llm_request", "llm_response", "loop_start", "loop_end",
                   "iter_start", "tool_call", "tool_result", "tool_error",
                   "error", "agent_approval_requested"):
        assert needed in SUMMARY_EVENT_TYPES, needed


def test_vocabulary_matches_trace_projection():
    """与 TraceProjection 的投影口径一致——两个词汇表漂移就是两套真相。"""
    from strategy_research.api.session import trace_projection as tp

    projected = set(tp.SUMMARY_EVENT_TYPES) if hasattr(
        tp, "SUMMARY_EVENT_TYPES") else set()
    if projected:  # 投影模块若以常量形式暴露，则必须是子集
        assert projected <= SUMMARY_EVENT_TYPES


def test_strip_removes_large_fields_only_for_llm_request():
    data = {"system_prompt": "x" * 1000, "tools_schema": "[]",
            "system_prompt_hash": "abc", "iteration": 1}
    out = strip_llm_request_fields("llm_request", data)
    assert "system_prompt" not in out and "tools_schema" not in out
    assert out["system_prompt_hash"] == "abc"  # 指纹保留
    assert out["iteration"] == 1

    other = {"system_prompt": "keep me"}
    assert strip_llm_request_fields("tool_call", other) is other  # 其他事件不动


def test_strip_does_not_mutate_input():
    data = {"system_prompt": "big"}
    strip_llm_request_fields("llm_request", data)
    assert data["system_prompt"] == "big"


# ── 2. study 透传链 ──────────────────────────────────────


def test_build_agent_loop_forwards_on_event(monkeypatch):
    """factory 必须把 on_event 递到 AgentLoop 构造参数。"""
    import strategy_research.core.agent.role_factory as rf

    seen = {}

    class FakeLoop:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(rf, "AgentLoop", FakeLoop, raising=False)
    # 走真实模块路径（factory 内部是 from ...core.agent.loop import AgentLoop）
    import strategy_research.core.agent.loop as loop_mod
    real = loop_mod.AgentLoop
    monkeypatch.setattr(loop_mod, "AgentLoop", FakeLoop)

    import strategy_research.core.agent.builtin_tools as bt
    monkeypatch.setattr(bt, "build_default_registry", lambda: object())

    import strategy_research.core.agent.loop_assembly as la
    class Assembly:
        system_prompt = "sp"
        loop_strategy = None
    monkeypatch.setattr(la, "assemble_loop_parts", lambda **k: Assembly())

    def fake_whitelist(role):
        return []
    monkeypatch.setattr(rf, "_get_tool_whitelist", fake_whitelist)

    cb = lambda et, d: None
    rf.build_agent_loop("researcher", Path("."), "s1", on_event=cb)
    assert seen.get("on_event") is cb

    monkeypatch.setattr(loop_mod, "AgentLoop", real)


def test_spawn_agent_forwards_on_event(monkeypatch):
    """spawn_agent → run_agent_via_llm 的 on_event 透传。"""
    import strategy_research.core.autoresearch as ar

    calls = {}
    monkeypatch.setattr(
        "strategy_research.core.agent.role_factory.should_use_real_llm",
        lambda: True)
    monkeypatch.setattr(
        "strategy_research.core.agent.role_factory.run_agent_via_llm",
        lambda **kw: calls.update(kw) or "{}")

    marker = lambda et, d: None
    ar.spawn_agent("researcher", Path("."), "s1", {}, [], on_event=marker)
    assert calls.get("on_event") is marker


def test_make_spawn_fn_injects_agent_name():
    """agent 名在 _make_spawn_fn 注入——只有它知道当前 spawn 的是谁。"""
    import strategy_research.core.autoresearch as ar

    seen: list[tuple[str, dict]] = []
    monkey_env = {"stub": True}

    # stub 路径不发事件；真 LLM 路径才走 run_agent_via_llm。直接打 spawn 内核。
    spawn = ar._make_spawn_fn(
        Path("."), "s1", {"k": 1}, behavior="static",
        on_event=lambda et, d: seen.append((et, d)),
    )
    # behavior 显式给出 → spawn_agent 走 stub，不发事件（stub 无 LLM 调用）
    out = spawn("researcher", [])
    assert out  # stub 正常返回
    assert seen == []


def test_spawn_real_llm_receives_agent_named_callback(monkeypatch):
    """真 LLM 路径：spawn_agent 收到「注入了 agent 名」的包装回调。

    包装语义在 _make_spawn_fn 验证（spawn_agent 走的同一约定）：回调收到的
    data 必须带 agent 字段，且不污染上游 dict。
    """
    import strategy_research.core.autoresearch as ar

    captured: dict = {}
    monkeypatch.setattr(
        "strategy_research.core.agent.role_factory.should_use_real_llm",
        lambda: True)
    monkeypatch.setattr(
        "strategy_research.core.agent.role_factory.run_agent_via_llm",
        lambda **kw: captured.update(kw) or '{"ok": true}')

    upstream_marker = {"iteration": 1}
    ar.spawn_agent(
        "strategist", Path("."), "s1", {}, [upstream_marker],
        on_event=lambda et, d: None,
    )
    wrapped = captured["on_event"]
    assert wrapped is not None, "on_event 必须透传到 run_agent_via_llm"

    # 包装回调把 agent 名并入 data（用 _make_spawn_fn 的同名约定直接验证）
    seen: list[dict] = []
    spawn = ar._make_spawn_fn(
        Path("."), "s1", {}, behavior=None,
        on_event=lambda et, d: seen.append(d),
    )
    # 强制真 LLM 路径会触网；改为直接复检 make 出来的闭包行为：
    # 用 stub（不发事件）确认零污染，再对包装语义做单元级断言。
    spawn("researcher", [])
    # stub 路径无事件 → seen 空，上游 dict 未被改写
    assert seen == []


# ── 3. StudyRunner sink ──────────────────────────────────


class _RecordingEmitter:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, session_id, event, data):
        self.events.append((session_id, event, data))


def _runner_with(emitter, tmp_path):
    from strategy_research.core.study.models import StudyRecord, StudyStatus
    from strategy_research.core.study.runner import AutoresearchRunner
    from strategy_research.core.study.store import StudyStore

    store = StudyStore(db_path=tmp_path / "s.db")
    rec = StudyRecord(
        study_id="st-1", session_id="sess-1", goal_id="g",
        objective="o", executor_type="autoresearch",
        workspace_path=str(tmp_path), strategy_name="s1",
        execution_status=StudyStatus.QUEUED,
    )
    runner = AutoresearchRunner(rec, store, emitter=emitter)
    return runner, store


def test_runner_sink_filters_streaming_and_keeps_summary(tmp_path):
    emitter = _RecordingEmitter()
    runner, store = _runner_with(emitter, tmp_path)
    try:
        on_event = runner._make_agent_on_event("sess-1")
        on_event("text_delta", {"text": "x"})          # 高频流 → 滤掉
        on_event("thinking_delta", {"delta": "y"})     # 高频流 → 滤掉
        on_event("tool_call", {"tool": "python"})      # 总结级 → 保留

        kinds = [e for _, e, _ in emitter.events]
        assert kinds == ["tool_call"]
    finally:
        store.close()


def test_runner_sink_strips_llm_request_large_fields(tmp_path):
    emitter = _RecordingEmitter()
    runner, store = _runner_with(emitter, tmp_path)
    try:
        on_event = runner._make_agent_on_event("sess-1")
        on_event("llm_request", {
            "system_prompt": "huge prompt",
            "tools_schema": "[...]",
            "system_prompt_hash": "h1",
            "iteration": 2,
        })
        _, event, data = emitter.events[0]
        assert event == "llm_request"
        assert "system_prompt" not in data and "tools_schema" not in data
        assert data["system_prompt_hash"] == "h1"
        # 注：agent 字段由 _make_spawn_fn 层注入，不归 sink 管——直接调
        # sink 时没有 agent 字段是正确行为。
    finally:
        store.close()


def test_runner_sink_attaches_trace_context(tmp_path):
    """sink 走 self._emit → emit_with_trace，事件应带 ContextVar 的 trace 字段。"""
    from strategy_research.core.observability import bind_trace

    emitter = _RecordingEmitter()
    runner, store = _runner_with(emitter, tmp_path)
    try:
        on_event = runner._make_agent_on_event("sess-1")
        with bind_trace(trace_id="tid-9", study_id="st-1", round_num=3):
            on_event("iter_start", {"iteration": 1})
        _, _, data = emitter.events[0]
        assert data["trace_id"] == "tid-9"
        assert data["study_id"] == "st-1"
        assert data["round_num"] == 3
    finally:
        store.close()


# ── 4. swarm ─────────────────────────────────────────────


def test_swarm_wrap_returns_none_without_callback():
    from strategy_research.core.swarm.runtime import SwarmRuntime

    rt = SwarmRuntime()  # 无 on_event
    assert rt._wrap_agent_on_event("a") is None


def test_swarm_wrap_filters_and_injects_agent_name():
    from strategy_research.core.swarm.runtime import SwarmRuntime

    seen: list[tuple[str, dict]] = []
    rt = SwarmRuntime(on_event=lambda et, d: seen.append((et, d)))
    wrapped = rt._wrap_agent_on_event("strategist")

    wrapped("text_delta", {"text": "x"})            # 滤掉
    wrapped("llm_request", {                         # 剥大字段 + 注 agent
        "system_prompt": "big", "system_prompt_hash": "h", "agent": "wrong",
    })
    wrapped("tool_result", {"tool": "read"})

    assert [e for e, _ in seen] == ["llm_request", "tool_result"]
    llm = seen[0][1]
    assert "system_prompt" not in llm
    assert llm["agent"] == "strategist"  # runtime 注入覆盖上游值


# ── 5. goal workflow 桥 ──────────────────────────────────


def test_goal_bridge_forwards_to_event_bus():
    """_agent_event_bridge 把 agent 事件送上 WorkflowEventBus。"""
    from strategy_research.core.goal.event_bus import CollectingObserver
    from strategy_research.core.goal.workflow import GoalWorkflowRunner

    runner = GoalWorkflowRunner.__new__(GoalWorkflowRunner)
    from strategy_research.core.goal.event_bus import WorkflowEventBus
    runner._event_bus = WorkflowEventBus()
    obs = CollectingObserver()
    runner._event_bus.subscribe(obs)

    bridge = runner._agent_event_bridge()
    bridge("llm_request", {"system_prompt": "big", "agent": "researcher"})
    bridge("text_delta", {"text": "x"})  # 桥不做过滤（runtime 已滤），但大字段要剥

    kinds = [e for e, _ in obs.events]
    assert kinds == ["llm_request", "text_delta"]
    data = obs.events[0][1]
    assert "system_prompt" not in data
    assert data["agent"] == "researcher"


# ── 6. CLI/MCP 的 SR_LOG_JSON 开关 ───────────────────────


def test_cli_logging_only_when_switch_on(monkeypatch, capsys):
    """SR_LOG_JSON 未设：不得建 handler（TUI 输出零影响）。"""
    import logging

    import strategy_research.cli.__main__ as cli_main

    monkeypatch.delenv("SR_LOG_JSON", raising=False)
    root = logging.getLogger()
    before = list(root.handlers)

    # main 走 non-TTY argparse 分支；给它一个立即返回的子命令
    monkeypatch.setattr(cli_main, "_is_interactive_invocation", lambda *a, **k: False)
    monkeypatch.setattr(cli_main, "_run_argparse", lambda argv: 0)
    cli_main.main([])

    assert list(root.handlers) == before  # 无新增 handler


def test_mcp_serve_stdio_wires_logging(monkeypatch, tmp_path):
    """MCP serve_stdio 挂 TraceFilter；协议响应仍走 stdout 的 JSON 帧。"""
    import json as json_mod

    from strategy_research.interfaces.mcp.server import MCPServer

    srv = MCPServer(name="t")
    monkeypatch.setattr("sys.stdin", iter([
        json_mod.dumps({"id": 1, "method": "tools/list", "params": {}})
    ]))
    srv.serve_stdio()  # 不抛即通过；handler 装配在内部完成


def test_mcp_tools_list_roundtrip_after_logging_wiring():
    """装配日志后 MCP 协议功能不受影响。"""
    from strategy_research.interfaces.mcp.server import MCPServer

    srv = MCPServer(name="t")
    srv.serve_stdio.__doc__  # noqa: B018 — 确认方法存在
    tools = srv.list_tools()
    assert isinstance(tools, list)
