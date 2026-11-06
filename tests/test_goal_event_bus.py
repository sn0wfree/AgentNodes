"""Tests for core/goal/event_bus.py — workflow event observer bus."""

from __future__ import annotations

import logging

from agentnodes.apps.goal.event_bus import (
    CollectingObserver,
    GoalPanelObserver,
    LoggerObserver,
    MetricsObserver,
    WorkflowEventBus,
)


class _RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def on_event(self, event: str, data: dict) -> None:
        self.events.append((event, data))


class _RaisingObserver:
    def on_event(self, event: str, data: dict) -> None:
        raise RuntimeError("observer boom")


# ────────────────────────── WorkflowEventBus ──────────────────────────


def test_subscribe_and_emit_dispatches_to_observer():
    bus = WorkflowEventBus()
    obs = _RecordingObserver()
    bus.subscribe(obs)
    bus.emit("agent_start", agent_id="researcher", layer=0)
    assert obs.events == [("agent_start", {"agent_id": "researcher", "layer": 0})]


def test_emit_with_no_observers_is_safe():
    bus = WorkflowEventBus()
    bus.emit("workflow_start", objective="x")  # must not raise


def test_unsubscribe_removes_observer():
    bus = WorkflowEventBus()
    obs = _RecordingObserver()
    bus.subscribe(obs)
    bus.unsubscribe(obs)
    bus.emit("agent_start", agent_id="x")
    assert obs.events == []


def test_unsubscribe_unknown_observer_is_noop():
    bus = WorkflowEventBus()
    bus.unsubscribe(_RecordingObserver())  # not subscribed → no exception


def test_clear_removes_all_observers():
    bus = WorkflowEventBus()
    bus.subscribe(_RecordingObserver())
    bus.subscribe(_RecordingObserver())
    bus.clear()
    assert len(bus) == 0


def test_emit_isolates_observer_failures(caplog):
    """A broken observer must not prevent later observers from running."""
    bus = WorkflowEventBus()
    good = _RecordingObserver()
    bus.subscribe(_RaisingObserver())
    bus.subscribe(good)
    with caplog.at_level(logging.WARNING, logger="agentnodes.apps.goal.event_bus"):
        bus.emit("agent_complete", agent_id="x")
    # The good observer still got the event.
    assert good.events == [("agent_complete", {"agent_id": "x"})]
    assert any("observer boom" in r.message for r in caplog.records)


def test_data_passed_to_observers_is_a_copy():
    """Mutating the data dict in an observer must not affect the bus."""
    bus = WorkflowEventBus()
    obs = _RecordingObserver()
    bus.subscribe(obs)
    bus.emit("agent_start", payload={"k": 1})
    obs.events[0][1]["payload"]["k"] = 999
    # Replay the same event — the bus has its own copy each emit.
    obs2 = _RecordingObserver()
    bus.subscribe(obs2)
    bus.emit("agent_start", payload={"k": 1})
    assert obs2.events[0][1]["payload"]["k"] == 1


# ────────────────────────── CollectingObserver ──────────────────────────


def test_collecting_observer_appends_and_clears():
    c = CollectingObserver()
    c.on_event("a", {"x": 1})
    c.on_event("b", {})
    assert c.events == [("a", {"x": 1}), ("b", {})]
    c.clear()
    assert c.events == []


# ────────────────────────── LoggerObserver ──────────────────────────


def test_logger_observer_emits_info_log(caplog):
    caplog.set_level(logging.INFO, logger="agentnodes.apps.goal.event_bus")
    LoggerObserver().on_event("layer_start", {"layer": 2})
    assert any("workflow event" in r.message for r in caplog.records)
    assert any("layer_start" in r.message for r in caplog.records)


# ────────────────────────── GoalPanelObserver ──────────────────────────


def test_goal_panel_observer_dispatches_to_panel():
    received: list[tuple[str, dict]] = []

    class _Panel:
        def on_workflow_event(self, event: str, data: dict) -> None:
            received.append((event, data))

    obs = GoalPanelObserver(_Panel())
    obs.on_event("agent_complete", {"agent_id": "a"})
    assert received == [("agent_complete", {"agent_id": "a"})]


def test_goal_panel_observer_logs_when_panel_missing_method(caplog):
    class _Panel:
        pass

    caplog.set_level(logging.WARNING, logger="agentnodes.apps.goal.event_bus")
    GoalPanelObserver(_Panel()).on_event("agent_complete", {})
    assert any("no on_workflow_event method" in r.message for r in caplog.records)


def test_goal_panel_observer_logs_unexpected_errors(caplog):
    class _Panel:
        def on_workflow_event(self, event, data):
            raise RuntimeError("panel boom")

    caplog.set_level(logging.WARNING, logger="agentnodes.apps.goal.event_bus")
    GoalPanelObserver(_Panel()).on_event("agent_complete", {})
    assert any("GoalPanel update failed" in r.message for r in caplog.records)


# ────────────────────────── MetricsObserver ──────────────────────────


def test_metrics_observer_counts_events():
    m = MetricsObserver()
    m.on_event("agent_start", {"agent_id": "a"})
    m.on_event("agent_start", {"agent_id": "a"})
    m.on_event("agent_complete", {"agent_id": "a"})
    assert m.event_counts == {"agent_start": 2, "agent_complete": 1}


def test_metrics_observer_records_agent_timings():
    m = MetricsObserver()
    m.on_event("agent_complete", {"agent_id": "a", "elapsed_s": 1.5})
    m.on_event("agent_complete", {"agent_id": "a", "elapsed_s": 2.5})
    assert m.agent_timings["a"] == [1.5, 2.5]


def test_metrics_observer_records_layer_timings(monkeypatch):
    """layer_start records _layer_start; layer_complete measures elapsed."""
    import time as _time

    times = iter([100.0, 102.5])
    monkeypatch.setattr(_time, "perf_counter", lambda: next(times))
    m = MetricsObserver()
    m.on_event("layer_start", {})
    m.on_event("layer_complete", {})
    assert m.layer_timings == [2.5]


def test_metrics_observer_summary():
    m = MetricsObserver()
    m.on_event("agent_complete", {"agent_id": "a", "elapsed_s": 1.0})
    m.on_event("agent_complete", {"agent_id": "a", "elapsed_s": 3.0})
    s = m.summary()
    assert s["agent_avg_timings"]["a"] == 2.0
    assert s["event_counts"]["agent_complete"] == 2
    assert s["total_layers"] == 0  # no layer_start/complete pair


def test_metrics_observer_clear_resets_state():
    m = MetricsObserver()
    m.on_event("agent_complete", {"agent_id": "a", "elapsed_s": 1.0})
    m.clear()
    assert m.summary()["event_counts"] == {}
