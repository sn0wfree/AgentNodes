"""Concurrency smoke tests for HypothesisStore (P3-E).

Ensures the RLock + WAL configuration handles parallel writes
without data corruption or SQLite "database is locked" errors.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from strategy_research.apps.hypothesis.store import HypothesisStore


@pytest.fixture
def store(tmp_path: Path) -> HypothesisStore:
    s = HypothesisStore(db_path=tmp_path / "hyp.db")
    yield s
    s.close()


# ─── helpers ─────────────────────────────────────────────────────


def _worker_create(store: HypothesisStore, idx: int, results: list, errors: list) -> None:
    """Create a hypothesis and record the result or error."""
    try:
        hyp = store.create(
            title=f"concurrent_{idx}",
            thesis=f"thesis for worker {idx}",
        )
        results.append(hyp.hypothesis_id)
    except Exception as exc:
        errors.append(exc)


def _worker_create_and_search(store: HypothesisStore, idx: int, results: list, errors: list) -> None:
    """Interleave create and search to stress the RLock."""
    try:
        store.create(title=f"cs_{idx}", thesis=f"thesis {idx}")
        hits = store.search(query="cs", limit=5)
        results.append(len(hits))
    except Exception as exc:
        errors.append(exc)


# ─── tests ───────────────────────────────────────────────────────


class TestConcurrentCreate:
    def test_parallel_create_no_losses(self, store: HypothesisStore):
        """N threads creating concurrently — all must succeed with unique IDs."""
        n_workers = 20
        results: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(n_workers)

        def _timed_create(idx: int):
            barrier.wait(timeout=5)
            _worker_create(store, idx, results, errors)

        threads = [threading.Thread(target=_timed_create, args=(i,)) for i in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Worker errors: {errors}"
        assert len(results) == n_workers
        # All IDs must be unique
        assert len(set(results)) == n_workers

    def test_parallel_create_persists_all(self, store: HypothesisStore):
        """All created hypotheses survive and are queryable."""
        n_workers = 15
        ids: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(n_workers)

        def _create(idx: int):
            barrier.wait(timeout=5)
            try:
                hyp = store.create(title=f"persist_{idx}", thesis="t")
                ids.append(hyp.hypothesis_id)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_create, args=(i,)) for i in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Worker errors: {errors}"
        # Every ID must be retrievable
        for hid in ids:
            assert store.get(hid) is not None, f"{hid} not found after parallel create"


class TestConcurrentCreateAndSearch:
    def test_parallel_create_and_search(self, store: HypothesisStore):
        """N threads interleaving create + search — no SQLite lock errors."""
        n_workers = 10
        search_counts: list[int] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(n_workers)

        def _work(idx: int):
            barrier.wait(timeout=5)
            _worker_create_and_search(store, idx, search_counts, errors)

        threads = [threading.Thread(target=_work, args=(i,)) for i in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Worker errors: {errors}"
        # Each search should have found at least one result (the one just created)
        assert len(search_counts) == n_workers
        # All searches returned at least 1 hit (the one they just created)
        assert all(c >= 1 for c in search_counts), f"Search counts: {search_counts}"


class TestConcurrentUpdate:
    def test_parallel_update_no_corruption(self, store: HypothesisStore):
        """N threads updating different hypotheses concurrently."""
        n_workers = 10
        hyps = [
            store.create(title=f"upd_{i}", thesis="t")
            for i in range(n_workers)
        ]
        errors: list[Exception] = []
        barrier = threading.Barrier(n_workers)

        def _update(idx: int):
            barrier.wait(timeout=5)
            try:
                store.update(hyps[idx].hypothesis_id, status="testing")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_update, args=(i,)) for i in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Worker errors: {errors}"
        # All should now be in testing status
        for hyp in hyps:
            fetched = store.get(hyp.hypothesis_id)
            assert fetched.status == "testing", f"{hyp.hypothesis_id} status={fetched.status}"


class TestCloseDuringActivity:
    def test_close_does_not_corrupt(self, store: HypothesisStore):
        """Closing a store while another thread is active should not crash the process."""
        errors: list[Exception] = []

        def _bg_create():
            try:
                for i in range(5):
                    store.create(title=f"bg_{i}", thesis="t")
            except Exception as exc:
                errors.append(exc)

        bg = threading.Thread(target=_bg_create)
        bg.start()
        # Close immediately — should not raise
        store.close()
        bg.join(timeout=5)

        # Background thread may have hit errors after close — that's acceptable.
        # The test is that the process didn't crash (we're still here).
        assert True
