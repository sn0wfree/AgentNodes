"""Tests for the SeqGenerator (Level 1)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from agentnodes.kernel.seq_generator import SeqGenerator, get_default_generator


class TestSeqGeneratorBasics:
    def test_first_call_returns_one(self):
        gen = SeqGenerator()
        assert gen.next("sess-1") == 1

    def test_subsequent_calls_increment(self):
        gen = SeqGenerator()
        gen.next("sess-1")
        gen.next("sess-1")
        assert gen.next("sess-1") == 3

    def test_sessions_are_independent(self):
        gen = SeqGenerator()
        assert gen.next("a") == 1
        assert gen.next("b") == 1  # different session
        assert gen.next("a") == 2  # continues from where 'a' left off
        assert gen.next("b") == 2
        assert gen.next("a") == 3

    def test_peek_does_not_increment(self):
        gen = SeqGenerator()
        gen.next("sess-1")
        gen.next("sess-1")
        assert gen.peek("sess-1") == 2
        assert gen.peek("sess-1") == 2  # still 2
        gen.next("sess-1")
        assert gen.peek("sess-1") == 3

    def test_peek_unknown_session_returns_zero(self):
        gen = SeqGenerator()
        assert gen.peek("never-used") == 0

    def test_empty_session_id_rejected(self):
        gen = SeqGenerator()
        with pytest.raises(ValueError):
            gen.next("")

    def test_reset_specific_session(self):
        gen = SeqGenerator()
        gen.next("a")
        gen.next("b")
        gen.reset("a")
        assert gen.peek("a") == 0
        assert gen.peek("b") == 1
        assert gen.next("a") == 1  # starts over

    def test_reset_all(self):
        gen = SeqGenerator()
        gen.next("a")
        gen.next("b")
        gen.reset()
        assert gen.peek("a") == 0
        assert gen.peek("b") == 0


class TestSeqGeneratorThreadSafety:
    def test_concurrent_increments_are_unique(self):
        """100 threads each call next('sess') 100 times → all 10000 values unique 1..10000."""
        gen = SeqGenerator()
        session_id = "concurrent-sess"
        n_threads = 50
        n_per_thread = 100
        results: list[int] = []

        def worker():
            for _ in range(n_per_thread):
                results.append(gen.next(session_id))

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker) for _ in range(n_threads)]
            for f in futures:
                f.result()

        assert len(results) == n_threads * n_per_thread
        # All values unique (no duplicate seqs assigned)
        assert len(set(results)) == len(results)
        # Values are 1..n_threads*n_per_thread (in some order)
        assert sorted(results) == list(range(1, n_threads * n_per_thread + 1))

    def test_concurrent_different_sessions_isolated(self):
        """Each session gets its own counter, even under concurrent access."""
        gen = SeqGenerator()
        n_sessions = 20
        n_per_session = 50

        def worker(sid: str):
            return [gen.next(sid) for _ in range(n_per_session)]

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {sid: pool.submit(worker, sid) for sid in [f"s{i}" for i in range(n_sessions)]}
            results = {sid: f.result() for sid, f in futures.items()}

        for sid, vals in results.items():
            # Each session: 1..n_per_session, no duplicates
            assert sorted(vals) == list(range(1, n_per_session + 1)), (
                f"session {sid} got {vals}"
            )


class TestGetDefaultGenerator:
    def test_returns_singleton(self):
        # Reset for test isolation
        import agentnodes.kernel.seq_generator as mod
        mod._default_generator = None

        g1 = get_default_generator()
        g2 = get_default_generator()
        assert g1 is g2

    def test_singleton_persists_state_across_calls(self):
        import agentnodes.kernel.seq_generator as mod
        mod._default_generator = None
        g1 = get_default_generator()
        seq1 = g1.next("shared-session")
        seq2 = get_default_generator().next("shared-session")
        assert seq2 == seq1 + 1
