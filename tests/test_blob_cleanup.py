"""P0-1 C3 — blob_refs + TTL cleanup tests.

Covers: schema creation, ref_count increments, list_stale_blobs filters
by last_access + TTL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.storage.blob_schema import (
    ensure_blob_refs_schema,
    list_stale_blobs,
    record_blob_offload,
)


@pytest.fixture
def conn():
    import sqlite3
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


class TestSchema:
    def test_ensure_creates_table_and_index(self, conn):
        ensure_blob_refs_schema(conn)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "blob_refs" in tables
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_blob_refs_last_access" in idx

    def test_ensure_idempotent(self, conn):
        ensure_blob_refs_schema(conn)
        ensure_blob_refs_schema(conn)  # no error


class TestRecordOffload:
    def test_first_record_creates_row(self, conn):
        ensure_blob_refs_schema(conn)
        record_blob_offload(conn, "trace-blobs/abc.txt", now=100.0)
        row = conn.execute(
            "SELECT blob_path, ref_count, first_seen, last_access "
            "FROM blob_refs WHERE blob_path='trace-blobs/abc.txt'"
        ).fetchone()
        assert row == ("trace-blobs/abc.txt", 1, 100.0, 100.0)

    def test_subsequent_records_increment(self, conn):
        ensure_blob_refs_schema(conn)
        record_blob_offload(conn, "trace-blobs/abc.txt", now=100.0)
        record_blob_offload(conn, "trace-blobs/abc.txt", now=200.0)
        row = conn.execute(
            "SELECT ref_count, first_seen, last_access "
            "FROM blob_refs WHERE blob_path='trace-blobs/abc.txt'"
        ).fetchone()
        assert row == (2, 100.0, 200.0)

    def test_different_paths_tracked_separately(self, conn):
        ensure_blob_refs_schema(conn)
        record_blob_offload(conn, "trace-blobs/a.txt", now=100.0)
        record_blob_offload(conn, "trace-blobs/b.txt", now=100.0)
        rows = conn.execute(
            "SELECT blob_path, ref_count FROM blob_refs ORDER BY blob_path"
        ).fetchall()
        assert rows == [
            ("trace-blobs/a.txt", 1),
            ("trace-blobs/b.txt", 1),
        ]


class TestListStaleBlobs:
    def test_empty_when_no_rows(self, conn):
        ensure_blob_refs_schema(conn)
        assert list_stale_blobs(conn, ttl_days=365, now=10_000.0) == []

    def test_table_missing_returns_empty(self, conn):
        # No ensure_blob_refs_schema call — table doesn't exist yet.
        assert list_stale_blobs(conn, ttl_days=365) == []

    def test_recent_blob_not_stale(self, conn):
        ensure_blob_refs_schema(conn)
        # last_access = 100, now = 100 + 365d = 31_536_000, threshold = 0
        # → not stale (last_access == threshold is not <)
        record_blob_offload(conn, "trace-blobs/a.txt", now=100.0)
        # Push "now" to right after the TTL window closes.
        now = 100.0 + 365 * 86400 + 1.0
        result = list_stale_blobs(conn, ttl_days=365, now=now)
        assert result == [
            {
                "blob_path": "trace-blobs/a.txt",
                "ref_count": 1,
                "first_seen": 100.0,
                "last_access": 100.0,
            }
        ]

    def test_within_ttl_not_stale(self, conn):
        ensure_blob_refs_schema(conn)
        record_blob_offload(conn, "trace-blobs/a.txt", now=100.0)
        now = 100.0 + 100 * 86400  # 100 days later
        result = list_stale_blobs(conn, ttl_days=365, now=now)
        assert result == []

    def test_respects_limit(self, conn):
        ensure_blob_refs_schema(conn)
        for i in range(5):
            record_blob_offload(
                conn, f"trace-blobs/{i}.txt", now=100.0 + i,
            )
        now = 100.0 + 365 * 86400 + 10.0
        result = list_stale_blobs(conn, ttl_days=365, now=now, limit=2)
        assert len(result) == 2
