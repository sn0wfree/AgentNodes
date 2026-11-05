"""P0-1 B2 — Projector snapshot tests.

Covers: schema/idempotent create, save/load round-trip, project() uses
snapshot as seed, flush() triggers snapshot every ``snapshot_interval``
events, multi-session isolation, snapshot never regresses (only updates
on higher seq).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.interfaces.api.session.projector import (
    ProjectedSession,
    Projector,
)
from agentnodes.kernel.events.event_v2 import EventType


def _make_event(sid: str, seq: int, etype: str, data: dict | None = None):
    """Build a minimal EventV2-like object the projector can consume.

    The Projector reads ``event.type`` / ``event.data`` / ``event.seq`` —
    we mimic that without going through EventV2.create so tests stay
    terse.
    """
    from agentnodes.kernel.events.event_v2 import EventV2

    return EventV2.create(
        aggregate_id=sid,
        seq=seq,
        type=etype,
        data=data or {},
    )


@pytest.fixture
def projector(tmp_path):
    db = tmp_path / "events.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    # Projector's load_events reads via its own connection; web_session
    # ensures sessions exists, so we mirror that here.
    conn.execute(
        "CREATE TABLE sessions ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT 'anonymous', "
        "title TEXT NOT NULL DEFAULT 'x', created_at REAL NOT NULL, "
        "updated_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE messages ("
        "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, "
        "content TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, "
        "metadata_json TEXT, message_type TEXT, seq INTEGER NOT NULL DEFAULT 0, "
        "FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE)"
    )
    conn.execute(
        "CREATE TABLE message_parts ("
        "id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL, "
        "type TEXT NOT NULL, data_json TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0, "
        "time_created REAL NOT NULL, "
        "FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE, "
        "FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE)"
    )
    # Pre-seed sessions rows so the FK on messages.session_id passes
    # when flush() UPSERTs message_rows. The projector itself doesn't
    # create parent rows; that responsibility belongs to web_session.
    for sid in ("s1", "s2"):
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, created_at, updated_at) VALUES (?, ?, ?)",
            (sid, 0.0, 0.0),
        )
    # event_log (P0-1 canonical schema) so load_events can issue its
    # SELECT — for snapshot tests we leave it empty.
    conn.execute(
        "CREATE TABLE event_log ("
        "id TEXT PRIMARY KEY, aggregate_id TEXT NOT NULL, seq INTEGER NOT NULL, "
        "type TEXT NOT NULL, data_json TEXT NOT NULL, time_created REAL NOT NULL, "
        "parent_event_id TEXT, branch_id TEXT NOT NULL DEFAULT 'main')"
    )
    conn.commit()
    conn.close()
    yield Projector(db)


async def _build_state_with_n_messages(projector: Projector, sid: str, n: int):
    """Drive the projector through ``n`` message_received events, return
    the resulting ProjectedSession.

    Each event carries a unique ``message_id`` so the projector can
    attach it to a ProjectedMessage (it would otherwise warn and skip).
    """
    state = ProjectedSession(session_id=sid)
    for i in range(1, n + 1):
        ev = _make_event(
            sid, i, EventType.MESSAGE_RECEIVED,
            {
                "text": f"hello {i}",
                "role": "user",
                "message_id": f"m-{sid}-{i}",
            },
        )
        projector._apply(ev, state)
        state.last_seq = i
    return state


class TestSnapshotSchema:
    def test_ensure_table_is_idempotent(self, projector: Projector, tmp_path):
        conn = sqlite3.connect(str(projector.db_path))
        try:
            projector._ensure_snapshots_table(conn)
            projector._ensure_snapshots_table(conn)  # second call: no error
            row = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='snapshots'"
            ).fetchone()
            assert row is not None
            idx = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='index' AND name='idx_snapshots_session_seq'"
            ).fetchone()
            assert idx is not None
        finally:
            conn.close()


class TestProjectedSessionRoundTrip:
    def test_to_dict_from_dict_roundtrip(self):
        s1 = ProjectedSession(session_id="s1", last_seq=5)
        # No messages: round-trip is trivial.
        s2 = ProjectedSession.from_dict(s1.to_dict())
        assert s2.session_id == "s1"
        assert s2.last_seq == 5
        assert s2.messages == {}

    def test_round_trip_rebuilds_parts_as_objects(self):
        """Regression: dataclasses.asdict() serializes parts values to
        raw dicts. from_dict must rebuild ProjectedPart objects or
        parts_in_order() (which reads .seq) crashes with
        AttributeError on the snapshot round-trip path."""
        from agentnodes.interfaces.api.session.projector import (
            ProjectedMessage,
            ProjectedPart,
        )
        s = ProjectedSession(session_id="s", last_seq=3)
        s.messages["m1"] = ProjectedMessage(
            id="m1", session_id="s", role="assistant", content="",
            parts={"p1": ProjectedPart(id="p1", type="text",
                                       data={"text": "x"}, seq=1)},
        )
        s2 = ProjectedSession.from_dict(s.to_dict())
        part = s2.messages["m1"].parts["p1"]
        assert isinstance(part, ProjectedPart)
        assert s2.messages["m1"].parts_in_order()[0].seq == 1
        assert part.data == {"text": "x"}

    def test_from_dict_tolerates_legacy_snapshot(self):
        """Legacy snapshots lack the open_thinking_part_id bookkeeping
        field — from_dict must not require its presence."""
        d = {
            "session_id": "s", "last_seq": 1,
            "messages": {
                "m1": {"id": "m1", "session_id": "s", "role": "user",
                       "content": "x", "parts": {}},
            },
        }
        s2 = ProjectedSession.from_dict(d)
        assert s2.messages["m1"].open_thinking_part_id is None

    def test_round_trip_preserves_message_fields(self):
        from agentnodes.interfaces.api.session.projector import ProjectedMessage
        s = ProjectedSession(session_id="s", last_seq=2)
        s.messages["m1"] = ProjectedMessage(
            id="m1", session_id="s", role="user",
            content="hi", message_type="user",
            created_at=123.0, seq=1, metadata={"k": "v"}, parts={},
        )
        s2 = ProjectedSession.from_dict(s.to_dict())
        assert "m1" in s2.messages
        assert s2.messages["m1"].role == "user"
        assert s2.messages["m1"].metadata == {"k": "v"}


class TestFlushWritesSnapshot:
    async def test_snapshot_written_when_seq_reaches_interval(
        self, projector: Projector, tmp_path,
    ):
        # Drive 200 events so flush()'s seq % 200 == 0 fires.
        state = await _build_state_with_n_messages(projector, "s1", 200)
        projector.flush(state, snapshot_interval=200)
        conn = sqlite3.connect(str(projector.db_path))
        try:
            row = conn.execute(
                "SELECT seq, snapshot_json FROM snapshots WHERE session_id='s1'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == 200
        snap = ProjectedSession.from_dict(json.loads(row[1]))
        assert snap.last_seq == 200
        assert len(snap.messages) == 200

    async def test_snapshot_not_written_below_interval(
        self, projector: Projector, tmp_path,
    ):
        state = await _build_state_with_n_messages(projector, "s1", 100)
        projector.flush(state, snapshot_interval=200)
        # Ensure the table exists for the assertion; the table won't be
        # created by flush() when the interval doesn't fire. We mimic
        # what a real warm-up would do: load_events/project load the
        # table on first use via _load_snapshot.
        conn = sqlite3.connect(str(projector.db_path))
        try:
            projector._ensure_snapshots_table(conn)
            row = conn.execute(
                "SELECT 1 FROM snapshots WHERE session_id='s1'"
            ).fetchone()
        finally:
            conn.close()
        assert row is None

    async def test_snapshot_written_every_interval(
        self, projector: Projector, tmp_path,
    ):
        state = await _build_state_with_n_messages(projector, "s1", 400)
        projector.flush(state, snapshot_interval=200)
        conn = sqlite3.connect(str(projector.db_path))
        try:
            rows = conn.execute(
                "SELECT seq FROM snapshots WHERE session_id='s1'"
            ).fetchall()
        finally:
            conn.close()
        assert [r[0] for r in rows] == [400]


class TestProjectSeedsFromSnapshot:
    async def test_project_uses_snapshot_when_present(
        self, projector: Projector, tmp_path,
    ):
        # Seed a snapshot directly at seq 100 with 100 messages.
        prior = ProjectedSession(session_id="s1", last_seq=100)
        for i in range(1, 101):
            from agentnodes.interfaces.api.session.projector import ProjectedMessage
            prior.messages[f"m{i}"] = ProjectedMessage(
                id=f"m{i}", session_id="s1", role="user",
                content=str(i), seq=i, parts={},
            )
        # Persist snapshot directly (bypasses flush side effects).
        conn = sqlite3.connect(str(projector.db_path))
        try:
            projector._ensure_snapshots_table(conn)
            conn.execute(
                "INSERT INTO snapshots (session_id, seq, snapshot_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    "s1", 100,
                    json.dumps(prior.to_dict(), ensure_ascii=False),
                    0.0,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        # Now project from scratch — should hit snapshot + apply delta
        # from seq=101 onwards (none in this test). No DB events means
        # the snapshot's state is returned as-is.
        result = projector.project("s1")
        assert result.last_seq == 100
        assert len(result.messages) == 100

    async def test_project_replays_delta_after_snapshot(
        self, projector: Projector, tmp_path,
    ):
        # Seed snapshot at seq=100 with 100 messages.
        prior = ProjectedSession(session_id="s1", last_seq=100)
        for i in range(1, 101):
            from agentnodes.interfaces.api.session.projector import ProjectedMessage
            prior.messages[f"m{i}"] = ProjectedMessage(
                id=f"m{i}", session_id="s1", role="user",
                content=str(i), seq=i, parts={},
            )
        conn = sqlite3.connect(str(projector.db_path))
        try:
            projector._ensure_snapshots_table(conn)
            conn.execute(
                "INSERT INTO snapshots (session_id, seq, snapshot_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    "s1", 100,
                    json.dumps(prior.to_dict(), ensure_ascii=False),
                    0.0,
                ),
            )
            conn.commit()
            # Also seed the events themselves so load_events can replay
            # the delta (it would normally read event_log, but we have
            # only snapshots in this test).  We pre-seed a dummy event
            # to verify the replay path actually applies a delta event.
            conn.execute(
                "INSERT INTO event_log VALUES (?,?,?,?,?,?,?,?)",
                ("e101", "s1", 101, "message_received",
                 json.dumps({"text": "delta", "role": "user",
                             "message_id": "m-s1-101"}),
                 1.0, None, "main"),
            )
            conn.commit()
        finally:
            conn.close()

        result = projector.project("s1")
        # Snapshot had 100 messages; replay applied event at seq=101,
        # which adds one more message.
        assert result.last_seq == 101
        assert len(result.messages) == 101


class TestSnapshotMultiSession:
    async def test_sessions_isolated(self, projector: Projector, tmp_path):
        s1 = await _build_state_with_n_messages(projector, "s1", 200)
        s2 = await _build_state_with_n_messages(projector, "s2", 200)
        projector.flush(s1, snapshot_interval=200)
        projector.flush(s2, snapshot_interval=200)
        conn = sqlite3.connect(str(projector.db_path))
        try:
            rows = conn.execute(
                "SELECT session_id, seq FROM snapshots ORDER BY session_id"
            ).fetchall()
        finally:
            conn.close()
        assert rows == [("s1", 200), ("s2", 200)]


class TestSnapshotMonotonic:
    async def test_lower_seq_does_not_overwrite_higher(
        self, projector: Projector, tmp_path,
    ):
        # Pre-seed a snapshot at seq=200.
        conn = sqlite3.connect(str(projector.db_path))
        try:
            projector._ensure_snapshots_table(conn)
            existing = ProjectedSession(session_id="s1", last_seq=200)
            conn.execute(
                "INSERT INTO snapshots (session_id, seq, snapshot_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("s1", 200,
                 json.dumps(existing.to_dict(), ensure_ascii=False),
                 0.0),
            )
            conn.commit()
        finally:
            conn.close()

        # Now flush a state at seq=100 — must NOT regress the snapshot.
        smaller = ProjectedSession(session_id="s1", last_seq=100)
        projector.flush(smaller, snapshot_interval=200)

        conn = sqlite3.connect(str(projector.db_path))
        try:
            row = conn.execute(
                "SELECT seq FROM snapshots WHERE session_id='s1'"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == 200  # untouched
