"""Phase 2 data-integrity regression tests.

Covers:
    - Concurrent emit(): seq never reused (no silent event loss)
    - Projector flush preserves metadata_json on REPLACE
    - get_messages returns the most-recent N (not the oldest)
    - /compact path no longer crashes on missing self.config
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._fixtures.legacy_event_bus_v2 import EventBusV2
from strategy_research.kernel.events.event_v2 import EventV2
from tests._fixtures.legacy_events import EventBus


def _setup_db(db_path: Path, n_sessions: int = 1) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        conn.execute("""
            CREATE TABLE event_log (
                id TEXT PRIMARY KEY,
                aggregate_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                time_created REAL NOT NULL,
                UNIQUE (aggregate_id, seq)
            )
        """)
        for i in range(n_sessions):
            conn.execute("INSERT INTO sessions (id) VALUES (?)", (f"s{i}",))
        conn.commit()
    finally:
        conn.close()


class TestConcurrentEmit(unittest.TestCase):
    def test_no_seq_collision_under_concurrency(self) -> None:
        """Two threads emitting concurrently must not reuse seq values.

        Regression: _next_seq read MAX(seq)+1 outside the lock, so two
        parallel emit() calls got the same seq; the loser's INSERT hit
        UNIQUE(aggregate_id, seq) and the event was silently dropped.
        """
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "test.db"
            _setup_db(db)
            legacy = EventBus()
            bus = EventBusV2(legacy, str(db), flush_to_messages=False)

            barrier = threading.Barrier(2)

            def emit(i: int) -> int:
                barrier.wait()  # maximize overlap
                ev = EventV2(
                    id=f"e{i}",
                    aggregate_id="s0",
                    seq=i,  # replay semantics: publish uses event's seq
                    type="message_received",
                    data={"i": i},
                    time_created=1000.0 + i,
                )
                bus.publish(ev)
                return i

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                list(ex.map(emit, [1, 2]))

            conn = sqlite3.connect(str(db))
            try:
                seqs = [r[0] for r in conn.execute(
                    "SELECT seq FROM event_log WHERE aggregate_id='s0' ORDER BY seq"
                )]
            finally:
                conn.close()
            # Both events persisted with distinct seqs
            self.assertEqual(len(seqs), 2)
            self.assertEqual(seqs, [1, 2])

    def test_emit_seq_unique_under_concurrency(self) -> None:
        """Direct emit() (which auto-assigns seq) stays unique too."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "test.db"
            _setup_db(db)
            legacy = EventBus()
            bus = EventBusV2(legacy, str(db), flush_to_messages=False)

            barrier = threading.Barrier(2)

            def emit(i: int) -> None:
                barrier.wait()
                bus.emit("s0", "message_received", {"i": i})

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                list(ex.map(emit, [1, 2]))

            conn = sqlite3.connect(str(db))
            try:
                seqs = [r[0] for r in conn.execute(
                    "SELECT seq FROM event_log WHERE aggregate_id='s0' ORDER BY seq"
                )]
            finally:
                conn.close()
            self.assertEqual(len(seqs), 2)
            self.assertEqual(seqs, [1, 2])


class TestFlushPreservesMetadata(unittest.TestCase):
    def _db_with_messages(self, tmp: Path) -> Path:
        db = Path(tmp) / "test.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
            conn.execute("""
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    metadata_json TEXT,
                    message_type TEXT,
                    seq INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE message_parts (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    seq INTEGER,
                    time_created REAL
                )
            """)
            conn.execute("INSERT INTO sessions (id) VALUES ('s0')")
            conn.execute("""
                INSERT INTO messages (id, session_id, role, content, created_at,
                                      metadata_json, message_type, seq)
                VALUES ('m1', 's0', 'assistant', 'hello', 1000.0,
                        '{"model": "gpt-x", "run_id": "r1"}', 'assistant', 1)
            """)
            conn.commit()
        finally:
            conn.close()
        return db

    def test_metadata_survives_flush(self) -> None:
        """INSERT OR REPLACE used to NULL metadata_json; ON CONFLICT must
        preserve the existing metadata when the projection has none."""
        import tempfile

        from strategy_research.interfaces.api.session.projector import Projector

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = self._db_with_messages(Path(tmp))
            state = MagicMock()
            state.session_id = "s0"
            state.last_seq = 2  # P0-1 B2: flush() reads this for snapshot trigger
            state.to_message_rows.return_value = [{
                "id": "m1", "session_id": "s0", "role": "assistant",
                "content": "hello v2", "message_type": "assistant",
                "created_at": 1001.0, "seq": 2,
            }]
            state.to_part_rows.return_value = []

            Projector(str(db)).flush(state)

            conn = sqlite3.connect(str(db))
            try:
                row = conn.execute(
                    "SELECT metadata_json, content, seq FROM messages WHERE id='m1'"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row[0], '{"model": "gpt-x", "run_id": "r1"}')
            self.assertEqual(row[1], "hello v2")
            self.assertEqual(row[2], 2)


class TestGetMessagesMostRecent(unittest.TestCase):
    def _db_with_messages(self, tmp: Path, n: int) -> Path:

        db = Path(tmp) / "test.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
            conn.execute("""
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    metadata_json TEXT,
                    message_type TEXT,
                    seq INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE message_parts (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    seq INTEGER,
                    time_created REAL
                )
            """)
            conn.execute("INSERT INTO sessions (id) VALUES ('s0')")
            for i in range(n):
                conn.execute(
                    "INSERT INTO messages (id, session_id, role, content, "
                    "created_at, message_type, seq) VALUES (?, 's0', 'user', ?, ?, 'user', ?)",
                    (f"m{i:04d}", f"msg-{i}", 1000.0 + i, i + 1),
                )
            conn.commit()
        finally:
            conn.close()
        return db

    def test_returns_most_recent_n(self) -> None:
        """limit=100 on a 120-message session must return seq 21..120
        (not the oldest 1..100)."""
        import tempfile

        from strategy_research.interfaces.api.session.store import SessionStore

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = self._db_with_messages(Path(tmp), 120)
            store = SessionStore(db_path=str(db))
            store._use_event_log_read = False

            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            with patch(
                "strategy_research.interfaces.api.routers.web_session._get_db",
                return_value=conn,
            ):
                msgs = store.get_messages("s0", limit=100)
            conn.close()
            self.assertEqual(len(msgs), 100)
            seqs = [m.seq for m in msgs]
            self.assertEqual(seqs[0], 21)      # most recent 100
            self.assertEqual(seqs[-1], 120)
            self.assertEqual(seqs, sorted(seqs))  # chronological order


class TestCompactNoSelfConfig(unittest.TestCase):
    def test_compact_history_no_attribute_error(self) -> None:
        """compact_history referenced self.config (never set) → every
        /compact failed with AttributeError. Empty history must return
        cleanly without touching config."""
        from strategy_research.interfaces.api.session.service import SessionService

        store = MagicMock()
        store.get_messages.return_value = []
        bus = MagicMock()
        service = SessionService(store=store, event_bus=bus)

        import asyncio

        with patch.object(SessionService, "_convert_messages_to_history",
                          return_value=[]):
            result = asyncio.run(service.compact_history("s0"))
        self.assertEqual(result["layers"], [])
        self.assertNotIn("config", vars(service))


if __name__ == "__main__":
    unittest.main()
