"""H 档补测（P1-2）—— `core/autoresearch.py` 的状态读取与重试梯。

补齐四块真实分支：

1. ``read_current_state``：strategy.py 缺失 / results.tsv 空 / 表头坏 /
   Calmar 列解析失败时的降级。它决定下一轮 agent 看到什么历史——读错了
   整个研究轮就基于错误前提。
2. ``retry_agent_spawn``：三层降级（解析失败 / 执行异常 / 超预算），
   且**不能真的 sleep**（测试用 base_delay=0）。
3. ``save_laziness_report`` 与 ``get_run_data``：空结果与「近详远简」
   的两级读取策略。
4. ``read_agent_history`` 的 detailed → summary 回退。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core import autoresearch as ar


# ── read_current_state ────────────────────────────────────


def _ws(tmp_path: Path, name: str = "s1") -> Path:
    d = tmp_path / "strategies" / name
    d.mkdir(parents=True)
    return d


def test_read_state_without_any_files(tmp_path):
    """全新 workspace：所有字段都是空值，不抛。"""
    state = ar.read_current_state(tmp_path, "s1")
    assert state["strategy_py"] == ""
    assert state["best_calmar"] == 0.0
    assert state["total_runs"] == 0
    assert state["recent_runs"] == ""


def test_read_state_reads_strategy_py(tmp_path):
    d = _ws(tmp_path)
    (d / "strategy.py").write_text("def generate_signals(): ...", encoding="utf-8")
    assert "generate_signals" in ar.read_current_state(tmp_path, "s1")["strategy_py"]


def test_read_state_counts_runs_and_picks_best_calmar(tmp_path):
    d = _ws(tmp_path)
    runs = d / "runs"
    runs.mkdir()
    (runs / "results.tsv").write_text(
        "run\tcalmar\tsharpe\tcalmar_value\n"
        "run_0001\t0.5\t1.0\t0.5\n"
        "run_0002\t0.9\t1.2\t1.8\n",
        encoding="utf-8",
    )
    state = ar.read_current_state(tmp_path, "s1")
    assert state["total_runs"] == 2
    assert state["best_calmar"] == 1.8


def test_read_state_ignores_unparseable_calmar(tmp_path):
    """坏数字不致命——跳过该行，其余照常统计。"""
    d = _ws(tmp_path)
    runs = d / "runs"
    runs.mkdir()
    (runs / "results.tsv").write_text(
        "h1\th2\th3\th4\n"
        "a\tb\tc\tnot-a-number\n"
        "a\tb\tc\t2.5\n",
        encoding="utf-8",
    )
    assert ar.read_current_state(tmp_path, "s1")["best_calmar"] == 2.5


def test_read_state_skips_rows_with_too_few_columns(tmp_path):
    d = _ws(tmp_path)
    runs = d / "runs"
    runs.mkdir()
    (runs / "results.tsv").write_text("h1\th2\nonly\ttwo\n", encoding="utf-8")
    assert ar.read_current_state(tmp_path, "s1")["best_calmar"] == 0.0


def test_read_state_empty_results_file(tmp_path):
    """只有空白的 tsv 与「不存在」等价——total_runs 必须是 0 而非 -1。"""
    d = _ws(tmp_path)
    runs = d / "runs"
    runs.mkdir()
    (runs / "results.tsv").write_text("   \n\n", encoding="utf-8")
    assert ar.read_current_state(tmp_path, "s1")["total_runs"] == 0


def test_read_state_recent_runs_caps_at_ten(tmp_path):
    d = _ws(tmp_path)
    runs = d / "runs"
    runs.mkdir()
    rows = ["h1\th2\th3\th4"] + [f"r{i}\tb\tc\t{i}.0" for i in range(20)]
    (runs / "results.tsv").write_text("\n".join(rows), encoding="utf-8")
    state = ar.read_current_state(tmp_path, "s1")
    assert state["total_runs"] == 20
    assert len(state["recent_runs"].splitlines()) == 10


def test_read_state_honours_explicit_strategy_file(tmp_path):
    """显式传入的 strategy_file 优先于默认位置。"""
    d = _ws(tmp_path)
    alt = tmp_path / "elsewhere.py"
    alt.write_text("MARKER", encoding="utf-8")
    state = ar.read_current_state(tmp_path, "s1", strategy_file=alt)
    assert state["strategy_py"] == "MARKER"


# ── retry_agent_spawn ─────────────────────────────────────


def test_retry_returns_parsed_on_first_success():
    calls = []

    def spawn():
        calls.append(1)
        return '{"ok": true}'

    assert ar.retry_agent_spawn(spawn, "a", max_retries=3, retry_delay=0) == {"ok": True}
    assert len(calls) == 1


def test_retry_then_succeeds():
    seq = iter(["not json at all", '{"ok": 2}'])

    def spawn():
        return next(seq)

    assert ar.retry_agent_spawn(spawn, "a", max_retries=3, retry_delay=0) == {"ok": 2}


def test_parse_failure_exhausts_budget_and_reports_hint():
    out = ar.retry_agent_spawn(lambda: "nope", "agent-x", max_retries=2, retry_delay=0)
    assert out["error"] == "parse_failed"
    assert out["agent"] == "agent-x"
    assert "hint" in out
    assert out["raw_output"] == "nope"


def test_execution_exception_is_reported_not_raised():
    def boom():
        raise RuntimeError("spawn blew up")

    out = ar.retry_agent_spawn(boom, "agent-y", max_retries=2, retry_delay=0)
    assert out["error"] == "execution_failed"
    assert "spawn blew up" in out["detail"]


def test_execution_exception_then_success():
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("transient")
        return '{"ok": 3}'

    assert ar.retry_agent_spawn(flaky, "a", max_retries=3, retry_delay=0) == {"ok": 3}


def test_retry_attempt_count_matches_budget():
    calls = {"n": 0}

    def always_bad():
        calls["n"] += 1
        return "still not json"

    ar.retry_agent_spawn(always_bad, "a", max_retries=4, retry_delay=0)
    assert calls["n"] == 4


def test_raw_output_is_truncated_in_failure_payload():
    """失败负载要截断——否则一条巨型输出会把事件/日志撑爆。"""
    out = ar.retry_agent_spawn(lambda: "x" * 5000, "a", max_retries=1, retry_delay=0)
    assert len(out["raw_output"]) == 500


# ── save_laziness_report ──────────────────────────────────


def test_laziness_report_no_issues(tmp_path):
    path = ar.save_laziness_report(tmp_path, 3, [{"agent": "a", "issues": []}], 0.0)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["summary"] == "所有 Agent 行为正常"
    assert report["round"] == 3


def test_laziness_report_lists_lazy_agents(tmp_path):
    results = [
        {"agent": "researcher", "issues": ["too short"]},
        {"agent": "strategist", "issues": []},
        {"agent": "risk_controller", "issues": ["no numbers"]},
    ]
    path = ar.save_laziness_report(tmp_path, 1, results, 0.4)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert "researcher" in report["summary"]
    assert "risk_controller" in report["summary"]
    assert "strategist" not in report["summary"]
    assert report["overall_lazy_score"] == 0.4


# ── should_read_detailed / get_run_data ──────────────────


def test_should_read_detailed_boundary():
    assert ar.should_read_detailed(10, 10) is True       # 差值 0
    assert ar.should_read_detailed(1, 10) is True        # 差值 9
    assert ar.should_read_detailed(0, 10) is False       # 差值 10
    assert ar.should_read_detailed(0, 100) is False


def test_get_run_data_prefers_agents_when_recent(tmp_path):
    """近轮走 agents/ 目录。注意 run_dir 名字必须是 run_NNNN。"""
    run = tmp_path / "run_0005"
    (run / "agents").mkdir(parents=True)
    (run / "agents" / "x.json").write_text(
        json.dumps({"output": {"v": 1}, "timestamp": "t"}), encoding="utf-8")
    (run / "summary.json").write_text(
        json.dumps({"agent_summaries": {"x": "short"}, "verdict": "keep"}),
        encoding="utf-8")
    data = ar.get_run_data(run, current_round=5, keep_recent=10)
    assert data["detailed"] is True
    assert data["source"] == "agents"
    assert data["round"] == 5


def test_get_run_data_falls_back_to_summary_when_old(tmp_path):
    run = tmp_path / "run_0001"
    (run / "agents").mkdir(parents=True)
    (run / "agents" / "x.json").write_text(
        json.dumps({"output": {"v": 1}}), encoding="utf-8")
    (run / "summary.json").write_text(
        json.dumps({"agent_summaries": {"x": "short"}, "verdict": "keep"}),
        encoding="utf-8")
    data = ar.get_run_data(run, current_round=100, keep_recent=10)
    assert data["detailed"] is False
    assert data["source"] == "summary"


def test_get_run_data_returns_none_source_without_summary(tmp_path):
    run = tmp_path / "run_0001"
    (run / "agents").mkdir(parents=True)
    data = ar.get_run_data(run, current_round=100, keep_recent=10)
    assert data["source"] == "none"
    assert data["data"] == {}


def test_get_run_data_rejects_non_run_dir_name(tmp_path):
    """名字不是 run_NNNN 时无法确定轮数——返回 none 而不是猜。"""
    d = tmp_path / "notarun"
    d.mkdir()
    data = ar.get_run_data(d, current_round=1)
    assert data["source"] == "none"
    assert data["round"] == 0


def test_get_run_data_empty_when_nothing_exists(tmp_path):
    data = ar.get_run_data(tmp_path / "run_0001", current_round=1, keep_recent=10)
    assert data["source"] == "none"


# ── read_agent_history ───────────────────────────────────
# 第一个参数是 runs/ 目录本身（不是 workspace 根）。


def test_read_agent_history_detailed_then_summary(tmp_path):
    recent = tmp_path / "run_0002"
    old = tmp_path / "run_0001"
    for d in (recent, old):
        (d / "agents").mkdir(parents=True)

    (recent / "agents" / "researcher.json").write_text(
        json.dumps({"output": {"recent": True}, "timestamp": "t2"}), encoding="utf-8")
    (old / "summary.json").write_text(
        json.dumps({"agent_summaries": {"researcher": "old summary"},
                    "verdict": "discard", "metrics": {"c": 0.1}}),
        encoding="utf-8")

    hist = ar.read_agent_history(tmp_path, "researcher", current_round=2, keep_recent=2)
    by_round = {h["round"]: h for h in hist}
    assert by_round[2]["source"] == "detailed"
    assert by_round[1]["source"] == "summary"
    assert by_round[1]["output"]["summary"] == "old summary"


def test_read_agent_history_tolerates_corrupt_json(tmp_path):
    run = tmp_path / "run_0001"
    (run / "agents").mkdir(parents=True)
    (run / "agents" / "researcher.json").write_text("{not json", encoding="utf-8")
    (run / "summary.json").write_text(
        json.dumps({"agent_summaries": {"researcher": "fallback"}}), encoding="utf-8")
    hist = ar.read_agent_history(tmp_path, "researcher", current_round=1, keep_recent=5)
    assert hist and hist[0]["source"] == "summary"


def test_read_agent_history_empty_when_no_runs(tmp_path):
    assert ar.read_agent_history(tmp_path, "researcher", current_round=0) == []


def test_read_agent_history_skips_unparseable_run_names(tmp_path):
    (tmp_path / "run_bad").mkdir()
    (tmp_path / "run_0001").mkdir()
    assert ar.read_agent_history(tmp_path, "researcher", current_round=1) == []
