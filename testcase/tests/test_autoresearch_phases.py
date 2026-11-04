"""H 档补测（P1-2b）—— `apps/autoresearch.py` 的 run 目录与阶段函数。

- ``_create_run_dir``：run 编号必须单调递增（重启后从 max+1 继续），
  且能接受外部 runs_dir（study 场景）。
- ``run_researcher_phase`` / ``run_execution_phase`` / ``run_evaluation_phase``：
  用 monkeypatch 把 spawn 换成纯 lambda，验证编排顺序、落盘、
  以及 backtest 失败时 metrics 归零但流程继续。

这些分支决定了「研究轮能否在 agent 失败时优雅降级」——降级错了要么整轮
崩，要么带着上轮的 metrics 继续算，两种都很难在生产里发现。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.apps import autoresearch as ar


# ── _create_run_dir ───────────────────────────────────────


def test_create_run_dir_default_location(tmp_path):
    runs_dir, run_name, run_dir = ar._create_run_dir(tmp_path, "s1")
    assert run_name == "run_0001"
    assert run_dir.is_dir()
    assert (run_dir / "agents").is_dir()
    assert runs_dir == tmp_path / "strategies" / "s1" / "runs"


def test_create_run_dir_increments_from_existing(tmp_path):
    runs = tmp_path / "rounds"
    (runs / "run_0003").mkdir(parents=True)
    (runs / "run_0007").mkdir()
    _, run_name, _ = ar._create_run_dir(tmp_path, "s1", runs_dir=runs)
    assert run_name == "run_0008"  # max+1，不是数量+1


def test_create_run_dir_ignores_non_run_entries(tmp_path):
    runs = tmp_path / "rounds"
    runs.mkdir()
    (runs / "notes.txt").write_text("x", encoding="utf-8")
    (runs / "run_abc").mkdir()
    _, run_name, _ = ar._create_run_dir(tmp_path, "s1", runs_dir=runs)
    assert run_name == "run_0001"


def test_create_run_dir_accepts_string_path(tmp_path):
    runs = tmp_path / "rounds"
    runs.mkdir()
    out_runs, _, _ = ar._create_run_dir(tmp_path, "s1", runs_dir=str(runs))
    assert isinstance(out_runs, Path)


# ── 阶段函数（spawn / backtest 全部替换为纯函数） ──────────


def _install_spawn(monkeypatch, record: list):
    """把 _make_spawn_fn 换成记录调用顺序的假实现。"""
    def fake_make_spawn(path, strategy_name, state, behavior=None, max_retries=3, **kw):
        def _spawn(name, prevs):
            record.append((name, len(prevs)))
            return {"agent": name, "prev_count": len(prevs)}
        return _spawn

    monkeypatch.setattr(ar, "_make_spawn_fn", fake_make_spawn)


def test_researcher_phase_records_agent_and_output(tmp_path, monkeypatch):
    calls: list = []
    _install_spawn(monkeypatch, calls)
    run_dir = tmp_path / "run_0001"
    (run_dir / "agents").mkdir(parents=True)

    out = ar.run_researcher_phase(
        tmp_path, "s1", {"state": 1}, run_dir,
        round_num=1, lazy_detection_interval=999,
    )
    assert calls == [("researcher", 0)]
    assert out["researcher_output"]["agent"] == "researcher"
    assert (run_dir / "agents" / "researcher.json").exists()


def test_researcher_phase_injects_directives(tmp_path, monkeypatch):
    captured = {}

    def fake_make_spawn(path, strategy_name, state, behavior=None, max_retries=3, **kw):
        captured["state"] = state

        def _spawn(name, prevs):
            return {"ok": True}
        return _spawn

    monkeypatch.setattr(ar, "_make_spawn_fn", fake_make_spawn)
    run_dir = tmp_path / "run_0001"
    (run_dir / "agents").mkdir(parents=True)
    ar.run_researcher_phase(
        tmp_path, "s1", {"state": 1}, run_dir,
        directives="focus on momentum", round_num=1, lazy_detection_interval=999,
    )
    assert captured["state"]["user_directives"] == "focus on momentum"
    assert captured["state"]["state"] == 1


def test_researcher_phase_runs_lazy_detection_when_due(tmp_path, monkeypatch):
    _install_spawn(monkeypatch, [])
    run_dir = tmp_path / "run_0001"
    (run_dir / "agents").mkdir(parents=True)
    seen = {}
    monkeypatch.setattr(
        ar, "detect_lazy_behavior",
        lambda name, out, hist: seen.setdefault(name, {"lazy_score": 0.0}),
    )
    runs_dir = tmp_path / "rounds"
    runs_dir.mkdir()
    ar.run_researcher_phase(
        tmp_path, "s1", {}, run_dir,
        round_num=10, lazy_detection_interval=10, runs_dir=runs_dir,
    )
    # 无历史 → detect 不该被调用（history 为空则跳过）
    assert seen == {}


def test_execution_phase_order_and_backtest_success(tmp_path, monkeypatch):
    calls: list = []
    _install_spawn(monkeypatch, calls)

    from strategy_research.apps import backtest as bt
    monkeypatch.setattr(
        bt, "run_backtest_script",
        lambda **kw: {"success": True, "metrics": {"calmar": 1.2}},
    )

    run_dir = tmp_path / "run_0001"
    (run_dir / "agents").mkdir(parents=True)
    out = ar.run_execution_phase(
        tmp_path, "s1", {}, {"researcher": True}, run_dir,
    )
    assert [c[0] for c in calls] == [
        "data_quality", "factor_analyst", "strategist", "portfolio_construction",
    ]
    assert out["metrics"] == {"calmar": 1.2}
    assert out["backtest_error"] is None


def test_execution_phase_backtest_failure_zeroes_metrics(tmp_path, monkeypatch):
    """回测失败时 metrics 必须清空——带上轮的会污染本轮决策。"""
    _install_spawn(monkeypatch, [])

    from strategy_research.apps import backtest as bt
    monkeypatch.setattr(
        bt, "run_backtest_script",
        lambda **kw: {"success": False, "error": "data missing"},
    )

    run_dir = tmp_path / "run_0001"
    (run_dir / "agents").mkdir(parents=True)
    out = ar.run_execution_phase(
        tmp_path, "s1", {}, {"researcher": True}, run_dir,
    )
    assert out["metrics"] == {}
    assert out["backtest_error"] == "data missing"


def test_execution_phase_backtest_failure_without_error_field(tmp_path, monkeypatch):
    _install_spawn(monkeypatch, [])
    from strategy_research.apps import backtest as bt
    monkeypatch.setattr(bt, "run_backtest_script", lambda **kw: {"success": False})
    run_dir = tmp_path / "run_0001"
    (run_dir / "agents").mkdir(parents=True)
    out = ar.run_execution_phase(tmp_path, "s1", {}, {}, run_dir)
    assert out["backtest_error"] == "unknown"


def test_evaluation_phase_runs_four_agents_then_decides(tmp_path, monkeypatch):
    calls: list = []
    _install_spawn(monkeypatch, calls)

    from strategy_research.apps import strategy_acceptance as sa

    class _Decision:
        accept = True
        def to_dict(self):
            return {"accept": True, "reasons": ["ok"]}

    monkeypatch.setattr(sa, "decide", lambda **kw: _Decision())
    monkeypatch.setattr(sa, "load_config", lambda **kw: object())

    run_dir = tmp_path / "run_0001"
    (run_dir / "agents").mkdir(parents=True)
    out = ar.run_evaluation_phase(
        tmp_path, "s1",
        backtest_result={"success": True, "metrics": {}},
        metrics={"calmar": 1.0},
        run_dir=run_dir,
    )
    assert [c[0] for c in calls] == [
        "risk_controller", "attribution_analyst",
        "anti_overfit_analyst", "backtest_diagnostics",
    ]
    assert out["verdict"] == "keep"
    # decision 是 Decision 对象（不是 dict）——上层用 .to_dict() 序列化
    assert out["decision"].accept is True


def test_evaluation_phase_maps_llm_verdict_fields(tmp_path, monkeypatch):
    captured = {}

    def fake_make_spawn(path, strategy_name, state, behavior=None, max_retries=3, **kw):
        def _spawn(name, prevs):
            if name == "anti_overfit_analyst":
                return {
                    "overfit_passed": True,
                    "overfit_score": 0.8,
                    "verdict_reason": "robust",
                    "methods_passed": ["walk_forward"],
                    "stagnation_count": 2,
                }
            return {}
        return _spawn

    monkeypatch.setattr(ar, "_make_spawn_fn", fake_make_spawn)

    from strategy_research.apps import strategy_acceptance as sa
    monkeypatch.setattr(sa, "decide", lambda **kw: captured.update(kw) or _FakeDecision())
    monkeypatch.setattr(sa, "load_config", lambda **kw: object())

    run_dir = tmp_path / "run_0001"
    (run_dir / "agents").mkdir(parents=True)
    ar.run_evaluation_phase(
        tmp_path, "s1",
        backtest_result={"success": True}, metrics={"calmar": 0.5}, run_dir=run_dir,
    )
    assert captured["llm_verdict"]["passed"] is True
    assert captured["llm_verdict"]["score"] == 0.8
    assert captured["llm_verdict"]["source"] == "anti_overfit_analyst"
    assert captured["stagnation_count"] == 2


def test_evaluation_phase_discards_when_rejected(tmp_path, monkeypatch):
    _install_spawn(monkeypatch, [])

    from strategy_research.apps import strategy_acceptance as sa
    monkeypatch.setattr(sa, "decide", lambda **kw: _RejectDecision())
    monkeypatch.setattr(sa, "load_config", lambda **kw: object())

    run_dir = tmp_path / "run_0001"
    (run_dir / "agents").mkdir(parents=True)
    runs_dir = run_dir.parent
    out = ar.run_evaluation_phase(
        tmp_path, "s1",
        backtest_result={"success": True}, metrics={}, run_dir=run_dir,
    )
    assert out["verdict"] == "discard"


class _FakeDecision:
    accept = True
    def to_dict(self):
        return {"accept": True}


class _RejectDecision:
    accept = False
    def to_dict(self):
        return {"accept": False, "reasons": ["low calmar"]}


# ── _spawn 的 inter_agent_sleep 不应真的睡 ────────────────


def test_make_spawn_fn_skips_sleep_when_zero(tmp_path, monkeypatch):
    """inter_agent_sleep=0 时不 sleep；返回值取自 retry_agent_spawn。"""
    slept = []
    monkeypatch.setattr(ar.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ar, "retry_agent_spawn", lambda fn, name, **kw: {"ok": True})
    monkeypatch.setattr(ar, "spawn_agent", lambda *a, **kw: '{"ok": true}')

    spawn = ar._make_spawn_fn(tmp_path, "s1", {}, inter_agent_sleep=0.0)
    assert spawn("researcher", []) == {"ok": True}
    assert slept == []


def test_make_spawn_fn_sleeps_between_agents(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(ar.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ar, "retry_agent_spawn", lambda fn, name, **kw: {"ok": True})
    monkeypatch.setattr(ar, "spawn_agent", lambda *a, **kw: '{"ok": true}')

    spawn = ar._make_spawn_fn(tmp_path, "s1", {}, inter_agent_sleep=0.25)
    spawn("researcher", [])
    assert slept == [0.25]
