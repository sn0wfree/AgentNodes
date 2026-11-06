"""GoalWorkflowHook 单元测试——证据收集/完成判定/指令注入。

覆盖 docs/coverage-90-plan-20261030.md §3.2：`goal/workflow_hook.py`
原 61.4%（77 miss），是 B 档第三大缺口。hook 回调全部可用
FakeRunner/FakeStore 驱动。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentnodes.apps.goal.workflow_hook import GoalWorkflowHook


class FakeStore:
    def __init__(self, snapshot=None):
        self._snapshot = snapshot
        self.appended: list[dict] = []

    def get_current_snapshot(self, session_id):
        return self._snapshot

    def append_evidence(self, **kw):
        self.appended.append(kw)


def _snapshot(*cids):
    return {"criteria": [{"criterion_id": c} for c in cids], "evidence_count": 0}


class FakeEventBus:
    def __init__(self):
        self.events: list[tuple] = []

    def emit(self, *a, **k):
        self.events.append((a, k))


def _runner(state=None, directives=None):
    r = SimpleNamespace(_state=state)
    if directives is not None:
        r.consume_directives = lambda: directives
    return r


def _result(output, status="SUCCESS"):
    return SimpleNamespace(status=status, output=json.dumps(output))


def _hook(store, **kw):
    defaults = dict(
        session_id="s1", goal_id="g1",
        evidence_map={"researcher": 0, "strategist": 1},
        store=store,
        event_bus=FakeEventBus(),
    )
    defaults.update(kw)
    return GoalWorkflowHook(**defaults)


# ── 基本属性 ─────────────────────────────────────────────


def test_initial_state():
    h = _hook(FakeStore(_snapshot("c1", "c2")))
    assert h.completed is False
    assert h.evidence_count == 0
    assert h.name == "GoalWorkflowHook"


def test_on_layer_start_updates_runner_state():
    state = SimpleNamespace(
        current_layer=0,
        agent_statuses={},
        set_agent_status=lambda aid, st: state_statuses.setdefault(aid, st),
    )
    state_statuses: dict = {}
    h = _hook(FakeStore(_snapshot("c1")), runner=SimpleNamespace(_state=state))
    h.on_layer_start(0, ["researcher"], {})
    assert state.current_layer == 1
    assert state_statuses == {"researcher": "running"}


def test_on_layer_start_injects_directives():
    state = SimpleNamespace(current_layer=0, agent_statuses={},
                            set_agent_status=lambda *a: None)
    ctx: dict = {}
    h = _hook(FakeStore(_snapshot("c1")),
              runner=_runner(state=state, directives="优先动量"))
    h.on_layer_start(0, ["researcher"], ctx)
    assert ctx["user_directives"] == "优先动量"


# ── on_agent_complete / 证据收集 ─────────────────────────


def test_agent_complete_collects_evidence():
    store = FakeStore(_snapshot("c1", "c2"))
    h = _hook(store)
    h.on_agent_complete("researcher", _result({"answer": "足够长的分析结论文本。"}), {})
    assert h.evidence_count == 1
    assert len(store.appended) == 1


def test_agent_complete_skips_short_output():
    """真实契约：_extract_output 后 <10 字符 → 不收集。"""
    store = FakeStore(_snapshot("c1"))
    h = _hook(store)
    h.on_agent_complete("researcher",
                        SimpleNamespace(status="SUCCESS", output="短"), {})
    assert h.evidence_count == 0


def test_agent_complete_skips_empty_output():
    store = FakeStore(_snapshot("c1"))
    h = _hook(store)
    h.on_agent_complete("researcher", SimpleNamespace(status="SUCCESS", output=""), {})
    assert h.evidence_count == 0


def test_agent_complete_plain_string_output():
    store = FakeStore(_snapshot("c1"))
    h = _hook(store)
    h.on_agent_complete("researcher", "足够长的纯文本输出内容。", {})
    assert h.evidence_count == 1


def test_agent_complete_marked_skipped_in_state():
    """skipped + 非空输出 → 状态记 skipped（不收集——skipped 输出不算证据）。"""
    statuses: dict = {}
    state = SimpleNamespace(agent_statuses={}, set_agent_status=lambda a, s: statuses.setdefault(a, s))
    store = FakeStore(_snapshot("c1"))
    h = _hook(store, runner=SimpleNamespace(_state=state))
    h.on_agent_complete("researcher",
                        SimpleNamespace(status="skipped",
                                        output="跳过的占位输出内容。"), {})
    assert statuses.get("researcher") == "skipped"


def test_agent_complete_empty_output_returns_before_state_update():
    """空输出 → 提前 return，状态**不**更新（锁定现行为）。"""
    statuses: dict = {}
    state = SimpleNamespace(agent_statuses={}, set_agent_status=lambda a, s: statuses.setdefault(a, s))
    store = FakeStore(_snapshot("c1"))
    h = _hook(store, runner=SimpleNamespace(_state=state))
    h.on_agent_complete("researcher",
                        SimpleNamespace(status="skipped", output=""), {})
    assert statuses == {}


# ── on_layer_complete / should_stop / auto complete ──────


def test_layer_complete_captures_results_and_emits():
    state = SimpleNamespace(current_layer=0, agent_statuses={},
                            set_agent_status=lambda *a: None)
    store = FakeStore(_snapshot("c1"))
    h = _hook(store, runner=SimpleNamespace(_state=state))
    h.on_layer_complete(0, ["researcher"],
                        {"researcher": SimpleNamespace(output='{"a": 1}')})
    # P1.3：真实层结果被捕获（checkpoint 恢复依赖）
    assert h._layer_results["researcher"] == {"output": {"a": 1}}
    # 事件总线收到 layer_complete
    events = [a[0] for a, k in h._event_bus.events]
    assert "layer_complete" in events


def test_should_stop_false_until_completed():
    h = _hook(FakeStore(_snapshot("c1")))
    assert h.should_stop() is False
    h._completed = True
    assert h.should_stop() is True


def test_should_stop_respects_runner_cancelled():
    """真实契约：读 runner._state.cancelled（P1.2 immediate cancel）。"""
    h = _hook(FakeStore(_snapshot("c1")),
              runner=SimpleNamespace(_state=SimpleNamespace(cancelled=True)))
    assert h.should_stop() is True


# ── metric targets ───────────────────────────────────────


def test_extract_metrics_and_meets():
    results = {"backtest": {"output": {"sharpe": 1.5}}}
    metrics = GoalWorkflowHook._extract_metrics(results)
    assert metrics.get("sharpe") == 1.5


def test_meets_metric_targets_true_and_false():
    """真实 target 形状：{"name", "op", "value"}。"""
    ok = GoalWorkflowHook._meets_metric_targets(
        {"sharpe": 1.5}, [{"name": "sharpe", "op": ">=", "value": 1.0}])
    assert ok is True
    bad = GoalWorkflowHook._meets_metric_targets(
        {"sharpe": 0.5}, [{"name": "sharpe", "op": ">=", "value": 1.0}])
    assert bad is False
    # 缺指标 → False（不能默认通过）
    missing = GoalWorkflowHook._meets_metric_targets(
        {}, [{"name": "sharpe", "op": ">=", "value": 1.0}])
    assert missing is False


# ── auto-complete（metric 达标路径）──────────────────────


class FakeStrategy:
    def __init__(self):
        self.calls: list[tuple] = []

    async def complete(self, store, session_id, goal_id, criteria, evidence, name):
        self.calls.append((session_id, goal_id, name))


def test_metric_meet_triggers_auto_complete():
    """指标达标 → _auto_complete 触发 strategy.complete。"""
    store = FakeStore(_snapshot("c1", "c2"))
    strategy = FakeStrategy()
    h = _hook(store, metric_targets=[{"name": "sharpe", "op": ">=", "value": 1.0}],
              completion_strategy=strategy)
    h.on_layer_complete(
        0, ["backtest"],
        {"backtest": SimpleNamespace(
            output=json.dumps({"sharpe": 1.8, "answer": "达标"}))},
    )
    assert h.completed is True
    assert strategy.calls and strategy.calls[0][0] == "s1"


def test_metric_miss_does_not_complete():
    store = FakeStore(_snapshot("c1", "c2"))
    strategy = FakeStrategy()
    h = _hook(store, metric_targets=[{"name": "sharpe", "op": ">=", "value": 3.0}],
              completion_strategy=strategy)
    h.on_layer_complete(
        0, ["backtest"],
        {"backtest": SimpleNamespace(output=json.dumps({"sharpe": 1.0}))},
    )
    assert h.completed is False
    assert strategy.calls == []


def test_auto_complete_idempotent():
    store = FakeStore(_snapshot("c1", "c2"))
    strategy = FakeStrategy()
    h = _hook(store, metric_targets=[{"name": "sharpe", "op": ">=", "value": 0.0}],
              completion_strategy=strategy)
    results = {"backtest": SimpleNamespace(output=json.dumps({"sharpe": 1.0}))}
    h.on_layer_complete(0, ["backtest"], results)
    h.on_layer_complete(1, ["backtest"], results)
    # completed 已置位 → 第二次不再触发
    assert len(strategy.calls) == 1


def test_criteria_coverage_check():
    """全部 required 准则都有证据 → _check_all_criteria_covered True。"""
    store = FakeStore({
        "criteria": [{"criterion_id": "c1", "required": True},
                     {"criterion_id": "c2", "required": False}],
        "evidence": [{"criterion_id": "c1"}],
    })
    h = _hook(store)
    assert h._check_all_criteria_covered() is True

    store2 = FakeStore({
        "criteria": [{"criterion_id": "c1", "required": True},
                     {"criterion_id": "c2", "required": True}],
        "evidence": [{"criterion_id": "c1"}],
    })
    h2 = _hook(store2)
    assert h2._check_all_criteria_covered() is False
