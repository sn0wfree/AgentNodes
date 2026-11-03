"""Unit tests for A4 MemoryRetrieverInjector (pre_run memory retrieval)."""
from __future__ import annotations

from types import SimpleNamespace

from strategy_research.kernel.agent.context_injector import (
    MemoryRetrieverInjector,
    build_default_injectors,
)


class _Entry:
    def __init__(self, title: str, body: str, memory_type: str = "project") -> None:
        self.title = title
        self.body = body
        self.memory_type = memory_type


class _Mem:
    def __init__(self, hits=None, raise_exc: bool = False) -> None:
        self.hits = hits or []
        self.raise_exc = raise_exc
        self.queries: list[str] = []

    def find_relevant(self, query: str, max_results: int = 5):
        self.queries.append(query)
        if self.raise_exc:
            raise RuntimeError("memory down")
        return list(self.hits)[:max_results]


def _loop(**kw):
    defaults = dict(enable_memory_injection=True, session_id=None)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestDisabledOrEmpty:
    def test_disabled_leaves_task(self):
        inj = MemoryRetrieverInjector(_Mem([_Entry("a", "b")]))
        task = "do research"
        out = inj.inject_pre_run(_loop(enable_memory_injection=False), task, [])
        assert out == task

    def test_empty_memory_no_block(self):
        inj = MemoryRetrieverInjector(_Mem([]))
        out = inj.inject_pre_run(_loop(), "alpha factor", [])
        assert out == "alpha factor"
        assert "<relevant-memory>" not in out

    def test_blank_task_passthrough(self):
        inj = MemoryRetrieverInjector(_Mem([_Entry("a", "b")]))
        assert inj.inject_pre_run(_loop(), "   ", []) == "   "

    def test_retrieval_error_never_blocks(self):
        inj = MemoryRetrieverInjector(_Mem(raise_exc=True))
        out = inj.inject_pre_run(_loop(), "task", [])
        assert out == "task"


class TestInjection:
    def test_block_prepended(self):
        mem = _Mem([_Entry("mom20", "momentum factor lesson", "project")])
        inj = MemoryRetrieverInjector(mem)
        out = inj.inject_pre_run(_loop(), "analyze momentum", [])
        assert out.startswith("<relevant-memory>")
        assert "mom20" in out
        assert out.endswith("analyze momentum")
        assert mem.queries == ["analyze momentum"]

    def test_multiple_entries(self):
        mem = _Mem([_Entry(f"e{i}", f"body{i}") for i in range(3)])
        inj = MemoryRetrieverInjector(mem, max_results=3)
        out = inj.inject_pre_run(_loop(), "t", [])
        assert out.count("- [project/") == 3

    def test_max_results_limits(self):
        mem = _Mem([_Entry(f"e{i}", "x") for i in range(10)])
        inj = MemoryRetrieverInjector(mem, max_results=2)
        out = inj.inject_pre_run(_loop(), "t", [])
        assert out.count("- [project/") == 2

    def test_max_chars_truncates(self):
        mem = _Mem([_Entry("n", "word " * 200)])
        inj = MemoryRetrieverInjector(mem, max_chars=120, body_chars=200)
        out = inj.inject_pre_run(_loop(), "t", [])
        assert "truncated" in out
        assert len(out) < 400

    def test_body_snippet_trimmed(self):
        mem = _Mem([_Entry("n", "A" * 500)])
        inj = MemoryRetrieverInjector(mem, body_chars=20)
        out = inj.inject_pre_run(_loop(), "t", [])
        assert "A" * 21 not in out or "…" in out

    def test_dict_entries_supported(self):
        mem = _Mem([{"title": "h", "body": "b", "memory_type": "feedback"}])
        inj = MemoryRetrieverInjector(mem)
        out = inj.inject_pre_run(_loop(), "t", [])
        assert "[feedback/h]" in out


class TestHypotheses:
    def test_include_hypotheses_adds_rows(self):
        class _Reg:
            def list_hypotheses(self):
                return [{"title": "momentum works", "statement": "20d mom"}]

        mem = _Mem([])
        inj = MemoryRetrieverInjector(mem, include_hypotheses=True)
        # Patch hypothesis registry load via _retrieve_hypotheses monkey
        original = inj._retrieve_hypotheses
        inj._retrieve_hypotheses = lambda loop, task: _Reg().list_hypotheses()
        out = inj.inject_pre_run(_loop(), "momentum factor", [])
        assert "momentum works" in out
        inj._retrieve_hypotheses = original

    def test_default_excludes_hypotheses(self):
        inj = MemoryRetrieverInjector(_Mem([]), include_hypotheses=False)
        inj._retrieve_hypotheses = lambda loop, task: [{"title": "SHOULD_NOT"}]
        out = inj.inject_pre_run(_loop(), "t", [])
        assert "SHOULD_NOT" not in out


class TestFactory:
    def test_default_chain_includes_memory(self):
        names = [i.name for i in build_default_injectors()]
        assert "memory_retriever" in names

    def test_order_after_goal(self):
        inj = {i.name: i for i in build_default_injectors()}
        assert inj["memory_retriever"].order > inj["goal_context"].order
        assert inj["memory_retriever"].order < inj["todos_snapshot"].order
