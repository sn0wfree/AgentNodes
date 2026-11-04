"""engine_common 单元测试——纯函数全覆盖。

覆盖 docs/coverage-90-plan-20261030.md §3.2 的第 6 优先项（原 39%）。
模块内是 JSON 解析、上下文构造、SSE 发射、取消守卫、会话建行——全部
可脱离 runner 实例用替身测试。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from strategy_research.apps.study import engine_common as ec
from strategy_research.apps.study.contracts import ShutdownReason


# ── safe_json_loads ──────────────────────────────────────


def test_safe_json_loads_plain_object():
    assert ec.safe_json_loads('{"a": 1}') == {"a": 1}


def test_safe_json_loads_plain_array():
    assert ec.safe_json_loads("[1, 2]") == [1, 2]


def test_safe_json_loads_markdown_json_fence():
    assert ec.safe_json_loads('```json\n{"a": 2}\n```') == {"a": 2}


def test_safe_json_loads_markdown_bare_fence():
    assert ec.safe_json_loads('```\n{"b": 3}\n```') == {"b": 3}


def test_safe_json_loads_embedded_object():
    assert ec.safe_json_loads('prefix {"c": 4} suffix') == {"c": 4}


def test_safe_json_loads_embedded_array():
    assert ec.safe_json_loads("noise [5, 6] noise") == [5, 6]


def test_safe_json_loads_empty_returns_fallback():
    assert ec.safe_json_loads("") is None
    assert ec.safe_json_loads("   ", fallback="fb") == "fb"


def test_safe_json_loads_non_string():
    assert ec.safe_json_loads({"already": "dict"}) == {"already": "dict"}
    assert ec.safe_json_loads(123, fallback="fb") == "fb"


def test_safe_json_loads_unparseable_returns_fallback():
    assert ec.safe_json_loads("not json at all", fallback="fb") == "fb"


def test_safe_json_loads_whitespace_stripped():
    assert ec.safe_json_loads('  \n {"d": 7} \n ') == {"d": 7}


# ── build_agent_ctx ──────────────────────────────────────


def test_build_agent_ctx_shape(tmp_path: Path):
    ctx = ec.build_agent_ctx("strat", tmp_path, "sess-1")
    assert ctx["strategy_name"] == "strat"
    assert ctx["strategy_dir"] == tmp_path
    assert ctx["runs_dir"] == tmp_path
    assert ctx["results_tsv"] == tmp_path / "results.tsv"
    assert ctx["session_id"] == "sess-1"
    assert ctx["session_manager"] is None


def test_build_agent_ctx_uses_runner_session_manager(tmp_path: Path):
    runner = SimpleNamespace(_session_manager="SM")
    ctx = ec.build_agent_ctx("s", tmp_path, "sess", runner)
    assert ctx["session_manager"] == "SM"


def test_build_agent_ctx_runner_without_attribute(tmp_path: Path):
    ctx = ec.build_agent_ctx("s", tmp_path, "sess", SimpleNamespace())
    assert ctx["session_manager"] is None


# ── phase_emitter ────────────────────────────────────────


def test_phase_emitter_emits_started_and_done():
    events: list[tuple] = []
    emit = lambda session, kind, data: events.append((session, kind, data))

    with ec.phase_emitter(emit, "sess", "sid", 3, "researcher"):
        assert len(events) == 1
        assert events[0][2]["status"] == "started"

    assert len(events) == 2
    assert events[1][2]["status"] == "done"
    assert events[1][2]["phase"] == "researcher"
    assert events[1][2]["round"] == 3


def test_phase_emitter_emits_done_on_exception():
    events: list[tuple] = []
    emit = lambda session, kind, data: events.append((session, kind, data))

    with pytest.raises(RuntimeError):
        with ec.phase_emitter(emit, "s", "sid", 1, "execution"):
            raise RuntimeError("boom")

    assert [e[2]["status"] for e in events] == ["started", "done"]


# ── save_agent_outputs ───────────────────────────────────


class _FakeRunner:
    def __init__(self) -> None:
        self.saved: list[tuple] = []

    def _save_agent_output(self, run_dir, agent_id, payload):
        self.saved.append((run_dir, agent_id, payload))


def test_save_agent_outputs_dict_and_str(tmp_path: Path):
    runner = _FakeRunner()
    ec.save_agent_outputs(runner, tmp_path, {
        "a": {"k": "v"},
        "b": "plain",
        "c": 42,
    })
    assert len(runner.saved) == 3
    by_id = {aid: payload for _d, aid, payload in runner.saved}
    assert json.loads(by_id["a"]["output"]) == {"k": "v"}
    assert by_id["b"]["output"] == "plain"
    assert by_id["c"]["output"] == "42"
    assert all(p["status"] == "success" for p in by_id.values())


def test_save_agent_outputs_histories(tmp_path: Path):
    runner = _FakeRunner()
    ec.save_agent_outputs(
        runner, tmp_path, {"a": "x"},
        agent_histories={"a": [{"step": 1}], "b": []},
    )
    hist = tmp_path / "agents" / "a_history.json"
    assert hist.exists()
    assert json.loads(hist.read_text(encoding="utf-8")) == [{"step": 1}]
    # 空历史不落盘
    assert not (tmp_path / "agents" / "b_history.json").exists()


# ── check_cancelled ──────────────────────────────────────


class _GuardRunner:
    def __init__(self, *, cancelled: bool, live_status):
        self.control = SimpleNamespace(cancelled=cancelled)
        self._live = live_status
        self.terminal: list[tuple] = []
        self.events: list[tuple] = []

    def _current_db_status(self):
        return self._live

    def _mark_terminal(self, status, *, reason=None):
        self.terminal.append((status, reason))

    def _emit(self, session, kind, data):
        self.events.append((session, kind, data))


def test_check_cancelled_returns_none_when_running():
    from strategy_research.apps.study.models import StudyStatus

    runner = _GuardRunner(cancelled=False, live_status=StudyStatus.RUNNING)
    assert ec.check_cancelled(runner, "s", "sid") is None
    assert runner.terminal == []


def test_check_cancelled_marks_terminal():
    from strategy_research.apps.study.models import StudyStatus

    runner = _GuardRunner(cancelled=True, live_status=StudyStatus.RUNNING)
    assert ec.check_cancelled(runner, "s", "sid") == ShutdownReason.CANCELLED
    assert runner.terminal == [(StudyStatus.CANCELLED, ShutdownReason.CANCELLED)]
    assert runner.events[-1][1] == "study_cancelled"


def test_check_cancelled_preserves_archived_status():
    from strategy_research.apps.study.models import StudyStatus

    runner = _GuardRunner(cancelled=True, live_status=StudyStatus.ARCHIVED)
    assert ec.check_cancelled(runner, "s", "sid") == ShutdownReason.CANCELLED
    # ARCHIVED 不被覆盖成 CANCELLED
    assert runner.terminal == []
    assert "preserved live status" in runner.events[-1][2]["note"]


# ── session helpers ──────────────────────────────────────


def test_get_study_session_db_path(tmp_path: Path):
    assert ec.get_study_session_db_path(tmp_path) == tmp_path / ec.SESSION_DB_FILENAME


def _make_sessions_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, title TEXT, user_id TEXT,
          created_at REAL, updated_at REAL
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, title, user_id, created_at, updated_at) "
        "VALUES ('existing', 't', 'alice', 1, 1)"
    )
    conn.commit()
    conn.close()


def test_ensure_study_session_inherits_owner(tmp_path: Path):
    db = tmp_path / "s.db"
    _make_sessions_db(db)

    ec.ensure_study_session(db, "new-sess", "Study R1")

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT title, user_id FROM sessions WHERE id = 'new-sess'"
    ).fetchone()
    conn.close()
    assert row == ("Study R1", "alice")


def test_ensure_study_session_defaults_to_system(tmp_path: Path):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, user_id TEXT, "
        "created_at REAL, updated_at REAL);"
    )
    conn.commit()
    conn.close()

    ec.ensure_study_session(db, "s2", "T")
    conn = sqlite3.connect(str(db))
    uid = conn.execute("SELECT user_id FROM sessions WHERE id='s2'").fetchone()[0]
    conn.close()
    assert uid == "system"


def test_ensure_study_session_is_idempotent(tmp_path: Path):
    db = tmp_path / "s.db"
    _make_sessions_db(db)
    ec.ensure_study_session(db, "dup", "first")
    ec.ensure_study_session(db, "dup", "second")  # INSERT OR IGNORE
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT title FROM sessions WHERE id='dup'").fetchall()
    conn.close()
    assert rows == [("first",)]


def test_ensure_study_session_swallows_missing_table(tmp_path: Path):
    """无 sessions 表 → 记录告警但不抛（研究不应因会话登记失败而中断）。"""
    db = tmp_path / "nodb.db"
    ec.ensure_study_session(db, "x", "t")  # 不抛
