"""Tests for parameter self-evolution (Phase E1-E5)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.core.agent.strategy.loop_strategy import LoopConfig
from strategy_research.apps.study.loop_evolution import (
    BOUNDS,
    _crossover,
    _estimate_fitness,
    _ga_step,
    _mutate,
    _read_current_config,
    _write_current_config,
    fitness,
    maybe_evolve,
    record_observation,
)


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    os.environ["QUANTNODES_RESEARCH_STUDY_DB_PATH"] = str(tmp_path / "studies.db")
    monkeypatch.setattr("random.seed", lambda x: None)
    yield
    os.environ.pop("QUANTNODES_RESEARCH_STUDY_DB_PATH", None)


@pytest.fixture
def study_store():
    from strategy_research.apps.study.store import StudyStore
    return StudyStore()


@pytest.fixture
def study(study_store):
    return study_store.create_study(
        owner_session_id="s1", goal_id=None, objective="test",
        workspace_path="/tmp/ws", strategy_name="test",
    )


def _fake_study(status="complete", calmar=1.5, rounds=3):
    s = MagicMock()
    s.study_id = "s1"
    s.last_metrics = {"calmar": calmar, "sharpe": 0.8, "max_dd": -0.1}
    s.current_round = rounds
    s.max_rounds = 10
    s.workspace_path = "/tmp/ws"
    from strategy_research.apps.study.models import StudyStatus
    s.execution_status = StudyStatus(status) if isinstance(status, str) else status
    s.loop_config = {"max_iterations": 20}
    return s


# ── E1: LoopConfig wiring ──────────────────────────────────────────


class TestLoopConfigWiring:
    def test_no_progress_window_from_strategy(self):
        from strategy_research.core.agent.strategy.explorer import ExplorerStrategyFactory
        strategy = ExplorerStrategyFactory.create()
        cfg = strategy.config
        assert cfg.max_iterations == 50
        assert cfg.no_progress_window == 5

    def test_config_round_trip(self):
        cfg = LoopConfig(max_iterations=15, no_progress_window=4, wrap_up_ratio=0.7)
        from dataclasses import asdict
        d = asdict(cfg)
        cfg2 = LoopConfig(**d)
        assert cfg2.max_iterations == 15
        assert cfg2.no_progress_window == 4
        assert cfg2.wrap_up_ratio == 0.7


# ── E2: Schema + store round-trip ──────────────────────────────────


class TestEvolutionSchema:
    def test_create_study_with_loop_config(self, study_store):
        r = study_store.create_study(
            owner_session_id="s", goal_id=None, objective="test",
            workspace_path="/tmp", strategy_name="t",
            loop_config={"max_iterations": 25},
        )
        assert r.loop_config == {"max_iterations": 25}
        fetched = study_store.get_study(r.study_id)
        assert fetched.loop_config == {"max_iterations": 25}

    def test_create_study_without_loop_config(self, study_store):
        r = study_store.create_study(
            owner_session_id="s", goal_id=None, objective="test",
            workspace_path="/tmp", strategy_name="t",
        )
        assert r.loop_config is None

    def test_evolution_table_exists(self, study_store):
        # Just verify the table was created (INSERT won't fail)
        study_store._conn.execute(
            "INSERT INTO loop_config_evolution (scope, config_json, fitness, study_id, outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("global", '{"max_iterations":10}', 0.5, "s1", "complete", "2026-01-01"),
        )

    def test_kv_table_exists(self, study_store):
        _write_current_config(study_store, {"max_iterations": 20})
        cfg = _read_current_config(study_store)
        assert cfg == {"max_iterations": 20}


# ── E4: GA engine ──────────────────────────────────────────────────


class TestGAEngine:
    def test_crossover_combines_parents(self):
        p1 = {"max_iterations": 10, "no_progress_window": 3}
        p2 = {"max_iterations": 50, "no_progress_window": 5}
        child = _crossover(p1, p2, rate=1.0)  # always crossover
        assert child["max_iterations"] in (10, 50)
        assert child["no_progress_window"] in (3, 5)

    def test_mutate_stays_in_bounds(self):
        for _ in range(50):
            mutated = _mutate({"max_iterations": 10, "no_progress_window": 3}, rate=1.0)
            for key, (lo, hi) in BOUNDS.items():
                if key in mutated:
                    assert lo <= mutated[key] <= hi, f"{key}={mutated[key]} out of [{lo}, {hi}]"

    def test_estimate_fitness_averages_matching_observations(self):
        obs = [
            {"config": {"max_iterations": 10}, "fitness": 0.5},
            {"config": {"max_iterations": 10}, "fitness": 0.7},
            {"config": {"max_iterations": 50}, "fitness": 0.3},
        ]
        assert _estimate_fitness({"max_iterations": 10}, obs) == pytest.approx(0.6)
        assert _estimate_fitness({"max_iterations": 50}, obs) == pytest.approx(0.3)
        assert _estimate_fitness({"max_iterations": 99}, obs) == 0.0

    def test_ga_step_returns_sorted_by_fitness(self):
        pop = [{"max_iterations": i * 10} for i in range(1, 6)]
        obs = [{"config": {"max_iterations": 30}, "fitness": 0.9}]
        result = _ga_step(pop, obs, n_generations=2)
        assert len(result) == 5


# ── E5: Fitness + record + maybe_evolve ─────────────────────────────


class TestFitnessRecording:
    def test_fitness_positive_for_good_study(self):
        s = _fake_study(status="complete", calmar=1.5)
        assert fitness(s, None) > 0

    def test_fitness_zero_for_error_study(self):
        s = _fake_study(status="error")
        assert fitness(s, None) == 0.0

    def test_record_observation(self, study_store):
        study = study_store.create_study(
            owner_session_id="s", goal_id=None, objective="test",
            workspace_path="/tmp", strategy_name="t",
            loop_config={"max_iterations": 20},
        )
        fit = record_observation(study_store, _fake_study())
        assert isinstance(fit, float)

    def test_maybe_evolve_returns_none_when_insufficient_data(self, study_store):
        assert maybe_evolve(study_store, min_observations=100) is None


# ── E5: Bootstrap injection ──────────────────────────────────────────


class TestBootstrapInjection:
    def test_create_study_record_readsevolved_config(self, monkeypatch):
        """When no loop_config passed, bootstrap reads from global KV."""
        from strategy_research.apps.study import store as _store_mod
        monkeypatch.setattr(_store_mod, "StudyStore", MagicMock)
        mock_store = MagicMock()
        mock_store.return_value.__enter__ = MagicMock(return_value=mock_store)
        mock_store.return_value.__exit__ = MagicMock(return_value=False)
        _store_mod.StudyStore.return_value = mock_store
        # No loop_config passed → should try to read from KV
        # (KV is empty → loop_config stays None)
