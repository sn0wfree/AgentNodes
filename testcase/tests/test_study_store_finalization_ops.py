"""Additional study store lifecycle tests for round finalization
operations called by run_round_phases.

Covers: update_last_metrics, update_round_heartbeat, list_rounds
with more detail, and the IDOR isolation check pattern.
"""

from __future__ import annotations

import pytest

from strategy_research.apps.study.store import StudyStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    db_path = tmp_path / "goals.db"
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    s = StudyStore(db_path=db_path)
    s.create_study(
        owner_session_id="tester", goal_id=None, objective="test",
        workspace_path=str(tmp_path), strategy_name="demo",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
    )
    yield s


def _get_study_id(store):
    return store._conn.execute("SELECT study_id FROM studies LIMIT 1").fetchone()["study_id"]


# ── update_last_metrics ──────────────────────────────────────


def test_update_last_metrics_persists_metrics(store):
    """update_last_metrics writes metrics and verdict to the studies row."""
    sid = _get_study_id(store)
    metrics = {"calmar": 0.6, "sharpe": 0.4, "max_dd": -0.1}

    store.update_last_metrics(sid, metrics, "keep")

    row = store._conn.execute(
        "SELECT last_metrics, last_verdict FROM studies WHERE study_id = ?",
        (sid,),
    ).fetchone()
    import json
    saved = json.loads(row["last_metrics"])
    assert saved["calmar"] == 0.6
    assert saved["sharpe"] == 0.4
    assert saved["max_dd"] == -0.1
    assert row["last_verdict"] == "keep"


def test_update_last_metrics_overwrites_previous(store):
    """Subsequent calls overwrite the previous metrics."""
    sid = _get_study_id(store)

    store.update_last_metrics(sid, {"calmar": 0.6}, "keep")
    store.update_last_metrics(sid, {"calmar": 0.9}, "keep")

    row = store._conn.execute(
        "SELECT last_metrics FROM studies WHERE study_id = ?",
        (sid,),
    ).fetchone()
    import json
    assert json.loads(row["last_metrics"])["calmar"] == 0.9


def test_update_last_metrics_empty_metrics(store):
    """Empty metrics dict is persisted."""
    sid = _get_study_id(store)
    store.update_last_metrics(sid, {}, "discard")

    row = store._conn.execute(
        "SELECT last_metrics, last_verdict FROM studies WHERE study_id = ?",
        (sid,),
    ).fetchone()
    import json
    assert json.loads(row["last_metrics"]) == {}
    assert row["last_verdict"] == "discard"


# ── update_round_heartbeat ──────────────────────────────────


def test_update_round_heartbeat_bumps_counter(store):
    """update_round_heartbeat sets current_round."""
    sid = _get_study_id(store)
    store.update_round_heartbeat(sid, 3)

    row = store._conn.execute(
        "SELECT current_round FROM studies WHERE study_id = ?",
        (sid,),
    ).fetchone()
    assert row["current_round"] == 3


def test_update_round_heartbeat_updates_timestamp(store):
    """update_round_heartbeat bumps heartbeat and updated_at."""
    import time
    sid = _get_study_id(store)

    before = time.time()
    store.update_round_heartbeat(sid, 1)

    row = store._conn.execute(
        "SELECT heartbeat, updated_at FROM studies WHERE study_id = ?",
        (sid,),
    ).fetchone()
    import datetime
    hb = datetime.datetime.fromisoformat(row["heartbeat"])
    ua = datetime.datetime.fromisoformat(row["updated_at"])
    # datetime carries only microsecond precision while time.time() is
    # a full float — a heartbeat written in the same microsecond can
    # compare 1e-6 smaller. Tolerate that quantization.
    assert hb.timestamp() >= before - 1e-6
    assert ua.timestamp() >= before - 1e-6


# ── mark_pending_objectives_applied ──────────────────────────


def test_mark_pending_returns_zero_when_no_pending(store):
    """mark_pending returns 0 when no pending objectives exist."""
    sid = _get_study_id(store)
    result = store.mark_pending_objectives_applied(sid, round_num=1)
    assert result == 0


# ── list_directives ──────────────────────────────────────────


def test_add_and_list_directives(store):
    """add_directive + list_pending_directives lifecycle."""
    sid = _get_study_id(store)

    store.add_directive(sid, "test directive 1", issued_by="user")
    store.add_directive(sid, "test directive 2", issued_by="user")

    pending = store.list_pending_directives(sid)
    assert len(pending) == 2
    assert pending[0].content == "test directive 1"


def test_mark_directives_consumed(store):
    """After consuming, list_pending_directives returns empty."""
    sid = _get_study_id(store)
    store.add_directive(sid, "d1", issued_by="user")
    store.add_directive(sid, "d2", issued_by="user")

    pending = store.list_pending_directives(sid)
    directive_ids = [d.directive_id for d in pending]
    store.mark_directives_consumed(sid, directive_ids)

    remaining = store.list_pending_directives(sid)
    assert len(remaining) == 0
