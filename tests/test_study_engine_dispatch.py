"""Tests for study engine dispatch + plan-dag async wiring.

Originally lived in tests/test_study_dag_engine.py together with the
dag-engine golden-output tests (now archived to attic/). Only the dispatch
-tests + plan-dag async test remain — they verify the live dispatch
contract: the phases graph path is the only executor; legacy engine
values ('dag', 'langgraph') pass through unchanged and fall back to
phases with a warning (P5-2).
"""
from __future__ import annotations


class TestEngineDispatch:
    def test_env_flag_is_deprecated_and_ignored(self, monkeypatch):
        """SR_STUDY_DAG_ENGINE no longer remaps engines (final cleanup).

        Behavior assertion on _resolve_engine: the legacy switch logs a
        deprecation warning but the resolved value is unchanged.
        """
        from types import SimpleNamespace

        from strategy_research.apps.study.phase_engine import _resolve_engine

        monkeypatch.setenv("SR_STUDY_DAG_ENGINE", "1")
        study = SimpleNamespace(engine="phases")
        assert _resolve_engine(study, round_num=1) == "phases"

    def test_legacy_engine_values_pass_through_to_fallback(self):
        """engine='dag'/'langgraph' are deprecated: no remap, no executor.

        _resolve_engine returns the value unchanged; the dispatcher
        has no key for them so _resolve_branch_fn falls back to phases
        with a warning (pre-Step-2 if/else semantics).
        """
        from types import SimpleNamespace

        from strategy_research.apps.study import phase_engine

        assert "dag" not in phase_engine._ENGINE_BRANCHES
        assert "langgraph" not in phase_engine._ENGINE_BRANCHES
        for legacy in ("dag", "langgraph"):
            study = SimpleNamespace(engine=legacy)
            assert phase_engine._resolve_engine(study, round_num=1) == legacy
            # fallback resolution lands on the graph path
            from strategy_research.apps.study.graph_preset import (
                run_phases_via_graph,
            )
            assert phase_engine._resolve_branch_fn(legacy) is run_phases_via_graph


class TestPlanDagAsync:
    def test_plan_dag_uses_to_thread(self):
        """study_plan_dag must use asyncio.to_thread to avoid blocking
        the event loop during the synchronous LLM call."""
        import inspect

        from strategy_research.interfaces.api.routers.study import study_plan_dag
        src = inspect.getsource(study_plan_dag)
        assert "asyncio.to_thread" in src
        assert "planner.plan" in src
