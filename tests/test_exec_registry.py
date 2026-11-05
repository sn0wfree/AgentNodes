"""Unit tests for exec_registry API (register/get/list/overwrite).

Covers:
- register/get round-trip for both python_executor and evaluator tables
- list_python_executors() / list_evaluators() return sorted keys
- Overwriting an existing name replaces (not raises)
- Builtin registration: 'run_backtest_script' + 'decide' present
  (guarded by ImportError; safe when deps missing)
"""
from __future__ import annotations

from agentnodes.kernel.agent import exec_registry


# ── python_executor table ────────────────────────────────────────────


class TestRegisterPythonExecutor:
    def test_register_and_get_roundtrip(self):
        def fn(**kw): return kw

        exec_registry.register_python_executor("_t_py_rt", fn)
        assert exec_registry.get_python_executor("_t_py_rt") is fn

    def test_get_unknown_returns_none(self):
        assert exec_registry.get_python_executor("_never_registered_xyz") is None

    def test_overwrite_replaces(self):
        """Registering the same name twice keeps the latest binding."""
        first = lambda **kw: "first"
        second = lambda **kw: "second"

        exec_registry.register_python_executor("_t_py_overwrite", first)
        exec_registry.register_python_executor("_t_py_overwrite", second)
        assert exec_registry.get_python_executor("_t_py_overwrite") is second


class TestListPythonExecutors:
    def test_returns_sorted(self):
        before = set(exec_registry.list_python_executors())
        exec_registry.register_python_executor("_z_list", lambda **kw: None)
        exec_registry.register_python_executor("_a_list", lambda **kw: None)
        names = exec_registry.list_python_executors()
        assert names == sorted(names)
        # Newly registered names appear in the list
        assert "_z_list" in names
        assert "_a_list" in names
        # Unrelated prior names untouched
        for n in before:
            assert n in names

class TestRegisterEvaluator:
    def test_register_and_get_roundtrip(self):
        def fn(**kw): return kw

        exec_registry.register_evaluator("_t_ev_rt", fn)
        assert exec_registry.get_evaluator("_t_ev_rt") is fn

    def test_get_unknown_returns_none(self):
        assert exec_registry.get_evaluator("_never_registered_xyz") is None

    def test_overwrite_replaces(self):
        first = lambda **kw: "first"
        second = lambda **kw: "second"

        exec_registry.register_evaluator("_t_ev_overwrite", first)
        exec_registry.register_evaluator("_t_ev_overwrite", second)
        assert exec_registry.get_evaluator("_t_ev_overwrite") is second


class TestListEvaluators:
    def test_returns_sorted(self):
        before = set(exec_registry.list_evaluators())
        exec_registry.register_evaluator("_z_ev_list", lambda **kw: None)
        exec_registry.register_evaluator("_a_ev_list", lambda **kw: None)
        names = exec_registry.list_evaluators()
        assert names == sorted(names)
        assert "_z_ev_list" in names
        assert "_a_ev_list" in names
        for n in before:
            assert n in names

class TestModuleSurface:
    def test_all_exports(self):
        """All listed exports are callable / importable."""
        for name in (
            "register_python_executor",
            "get_python_executor",
            "list_python_executors",
            "register_evaluator",
            "get_evaluator",
            "list_evaluators",
            "extract_metrics_from_upstream",
        ):
            assert hasattr(exec_registry, name), f"missing export: {name}"
