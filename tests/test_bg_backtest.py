"""Tests for RunBacktestTool(background=True) — in-process pipeline
backgrounded via a thread + log progress + shared task registry."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.builtin_tools import backtest_tools as bt
from strategy_research.kernel.agent.builtin_tools.bg_tools import RunBgCommandTool
from strategy_research.core.agent.tools import ToolContext


def _make_step(**returns):
    """Factory: a step class whose execute returns the given dict."""

    class _Step:
        def execute(self, **kwargs):
            return json.dumps(returns, ensure_ascii=False)

    return _Step


_CFG_OK = _make_step(status="ok", cfg={"market_type": "test"}, yaml_path=None)
_PREP_OK = _make_step(status="ok")
_GATE_OK = _make_step(status="ok", report={"ready": True})


class _FakeEngineStep:
    def execute(self, **kwargs):
        return json.dumps({
            "status": "ok", "run": "run_0001",
            "metrics": {"calmar": 0.62, "sharpe": 0.4, "max_dd": -0.1},
            "factor_failures": [], "warnings": [],
        })


class _FakeSlowEngineStep(_FakeEngineStep):
    def execute(self, **kwargs):
        time.sleep(0.5)
        return super().execute(**kwargs)


@pytest.fixture
def fake_steps(monkeypatch):
    monkeypatch.setattr(bt, "ConfigLoadStep", _CFG_OK)
    monkeypatch.setattr(bt, "DataPrepareStep", _PREP_OK)
    monkeypatch.setattr(bt, "DataReadinessStep", _GATE_OK)
    monkeypatch.setattr(bt, "EngineRunStep", _FakeEngineStep)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "strategies" / "demo").mkdir(parents=True)
    return tmp_path


def _invoke_bg(tool: RunBgCommandTool, ctx, **kw) -> dict:
    return json.loads(tool.execute(ctx, **kw))


def test_background_returns_running_and_completes(ws, fake_steps):
    ctx = ToolContext(workspace=ws, session_id="t")
    out = _invoke_bg(bt.RunBacktestTool(), ctx,
                     strategy_name="demo", background=True)
    assert out["status"] == "running"
    assert out["task_id"].startswith("bg_")
    log = Path(out["log"])
    assert log.parent.exists()

    bg = RunBgCommandTool()
    # poll until done (fake steps are instant)
    for _ in range(50):
        time.sleep(0.05)
        st = json.loads(bg.execute(ctx, action="status", task_id=out["task_id"]))
        if st["state"] == "done":
            break
    assert st["state"] == "done", st
    # done state carries the pipeline result
    assert st["result"]["status"] == "ok"
    assert st["result"]["run"] == "run_0001"
    assert st["result"]["metrics"]["calmar"] == 0.62
    # progress lines were written to the log
    content = log.read_text(encoding="utf-8")
    assert "[backtest] step: config load" in content
    assert "[backtest] done" in content


def test_background_slow_engine_logs_progress(ws, monkeypatch):
    """Slow engine step keeps the log advancing (heartbeat via steps)."""
    monkeypatch.setattr(bt, "ConfigLoadStep", _CFG_OK)
    monkeypatch.setattr(bt, "DataPrepareStep", _PREP_OK)
    monkeypatch.setattr(bt, "DataReadinessStep", _GATE_OK)
    monkeypatch.setattr(bt, "EngineRunStep", _FakeSlowEngineStep)

    ctx = ToolContext(workspace=ws, session_id="t")
    out = _invoke_bg(bt.RunBacktestTool(), ctx,
                     strategy_name="demo", background=True)
    # mid-run status must report running, not stalled
    time.sleep(0.2)
    bg = RunBgCommandTool()
    st = json.loads(bg.execute(ctx, action="status", task_id=out["task_id"]))
    assert st["state"] == "running", st
    for _ in range(50):
        time.sleep(0.05)
        st = json.loads(bg.execute(ctx, action="status", task_id=out["task_id"]))
        if st["state"] == "done":
            break
    assert st["state"] == "done"


def test_background_step_error_reported(ws, monkeypatch):
    class _FailingPrepare:
        def execute(self, **kwargs):
            return json.dumps({"status": "error", "error": "no data"})

    monkeypatch.setattr(bt, "ConfigLoadStep", _CFG_OK)
    monkeypatch.setattr(bt, "DataPrepareStep", _FailingPrepare)

    ctx = ToolContext(workspace=ws, session_id="t")
    out = _invoke_bg(bt.RunBacktestTool(), ctx,
                     strategy_name="demo", background=True)
    bg = RunBgCommandTool()
    for _ in range(50):
        time.sleep(0.05)
        st = json.loads(bg.execute(ctx, action="status", task_id=out["task_id"]))
        if st["state"] == "done":
            break
    assert st["state"] == "done"
    assert st["result"]["status"] == "error"
    assert "no data" in st["result"]["error"]


def test_foreground_mode_unchanged(ws, fake_steps):
    """background=False (default) keeps the blocking ok result shape."""
    ctx = ToolContext(workspace=ws, session_id="t")
    out = _invoke_bg(bt.RunBacktestTool(), ctx, strategy_name="demo")
    assert out["status"] == "ok"
    assert out["run"] == "run_0001"
    assert "task_id" not in out


# ── prompt/whitelist assertions (agent collaboration surfaces) ───────


def test_long_task_rule_exists():
    from pathlib import Path as P
    root = P(__file__).resolve().parents[1] / "src" / "strategy_research"
    rule = root / "templates" / ".prompts" / "_common" / "rules" / "long-task.md"
    assert rule.exists()
    content = rule.read_text(encoding="utf-8")
    assert "run_bg_command" in content
    assert "background=True" in content
    assert "观察窗" in content


def test_long_task_rule_indexed():
    from pathlib import Path as P
    root = P(__file__).resolve().parents[1] / "src" / "strategy_research"
    index = root / "templates" / ".prompts" / "_common" / "rules" / "INDEX.md"
    assert "long-task.md" in index.read_text(encoding="utf-8")


def test_whitelist_has_bg_tool_for_long_task_roles():
    from strategy_research.core.agent.role_factory import _ROLE_TOOL_WHITELIST
    for role in ("strategist", "backtest_diagnostics",
                 "factor_analyst", "data_quality"):
        assert "run_bg_command" in _ROLE_TOOL_WHITELIST[role], role


def test_study_phase_iteration_budget_raised():
    """Study 场景三 phase 传 max_iterations=10（观察窗预算），CLI 默认 8 不动."""
    import inspect

    import strategy_research.apps.study.runner as runner_mod
    src = inspect.getsource(runner_mod.AutoresearchRunner._run_one_round_impl)
    assert src.count("max_iterations=10") == 3
    import strategy_research.apps.autoresearch as ar
    sig = inspect.signature(ar.run_researcher_phase)
    assert sig.parameters["max_iterations"].default == 8  # CLI 默认不变
