"""Parameter self-evolution — genetic algorithm optimizing agent LoopConfig.

After studies complete, this module analyzes which LoopConfig parameters led
to the best outcomes, then evolves better configs for future studies using
a simple genetic algorithm (tournament selection + uniform crossover +
boundary mutation).

See docs/agentquant-research-20260902.md §参数自我进化 for design rationale.
"""
from __future__ import annotations

import logging
import random
from typing import Any

logger = logging.getLogger(__name__)

# ── Tunable parameter bounds (GA search space) ─────────────────────
BOUNDS: dict[str, tuple[float, float]] = {
    "max_iterations":      (3, 80),
    "no_progress_window":  (2, 15),
    "wrap_up_ratio":       (0.5, 0.95),
    "tool_max_retries":    (1, 5),
}
_INT_PARAMS = frozenset({"max_iterations", "no_progress_window", "tool_max_retries"})
_SEED_CONFIGS: list[dict] = [
    {"max_iterations": 10, "no_progress_window": 3},   # react
    {"max_iterations": 50, "no_progress_window": 5},   # explorer
    {"max_iterations":  5, "no_progress_window": 2},   # validator
    {"max_iterations":  1, "no_progress_window": 3},   # minimal
]
_FITNESS_WEIGHTS = [0.5, 0.3, 0.1, 0.1]  # calmar, target_met, rounds, discard


def fitness(
    study: Any,
    study_store: Any,
    *,
    weights: list[float] | None = None,
) -> float:
    """Compute a fitness score for a completed study.

    fitness = w1*(best_calmar/2) + w2*targets_met - w3*rounds_ratio - w4*discard_ratio

    Higher is better.  Returns 0.0 for studies without usable metrics.
    """
    w = weights or _FITNESS_WEIGHTS
    # Error / cancelled studies have no useful fitness signal.
    status = getattr(study, "execution_status", None)
    if status is not None and getattr(status, "value", "") not in ("complete", "monitoring"):
        return 0.0
    metrics = getattr(study, "last_metrics", None) or {}
    calmar = float(metrics.get("calmar") or 0)
    targets_met = 1.0 if study.execution_status.value == "complete" else 0.0
    max_rounds = getattr(study, "max_rounds", None)
    rounds_used = getattr(study, "current_round", 0)
    rounds_ratio = rounds_used / max_rounds if max_rounds and max_rounds > 0 else 0.0
    # discard_ratio: read from state.json if available
    try:
        from .state_store import load as load_state
        path = __import__("pathlib").Path(study.workspace_path).resolve()
        state = load_state(path, study.study_id)
        discard_streak = getattr(state, "discard_streak", 0) if state else 0
    except Exception:
        discard_streak = 0
    discard_ratio = min(discard_streak / 5.0, 1.0)
    return round(
        w[0] * (calmar / 2.0)
        + w[1] * targets_met
        - w[2] * rounds_ratio
        - w[3] * discard_ratio,
        6,
    )


def record_observation(
    study_store: Any,
    study: Any,
    *,
    config: dict | None = None,
) -> float:
    """Record a fitness observation for a completed study.

    Returns the fitness score.  Called by runner after _mark_terminal.
    """
    fit = fitness(study, study_store)
    config = config or getattr(study, "loop_config", None) or {}
    study_id = study.study_id
    now_iso = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat()
    with study_store._lock:
        study_store._conn.execute(
            """
            INSERT INTO loop_config_evolution
                (scope, generation, config_json, fitness, study_id, outcome, created_at)
            VALUES (?, 0, ?, ?, ?, ?, ?)
            """,
            ("global", __import__("json").dumps(config), fit,
             study_id, study.execution_status.value, now_iso),
        )
        study_store._conn.commit()
    return fit


def maybe_evolve(
    study_store: Any,
    *,
    min_observations: int = 5,
    improvement_threshold: float = 1.1,
    min_new_samples: int = 3,
) -> dict | None:
    """Run the GA when enough observations have accumulated.

    Returns the new current config if the incumbent was replaced, else None.
    Only replaces when the new config's observed fitness significantly beats
    the current config (new_mean > current_mean * improvement_threshold AND
    at least min_new_samples observations for the new config).
    """
    current = _read_current_config(study_store)
    obs = _fetch_observations(study_store, limit=50)
    if len(obs) < min_observations:
        return None
    # Seed population: current config + seeds
    pop = list(_SEED_CONFIGS)
    if current:
        pop.insert(0, current)
    # Evolve
    candidates = _ga_step(pop, obs, n_generations=3)
    if not candidates:
        return None
    best_candidate = candidates[0]
    # Compare against incumbent
    current_mean = _mean_fitness_for_config(obs, current) if current else -1.0
    new_mean = _mean_fitness_for_config(obs, best_candidate)
    new_count = sum(1 for o in obs if _configs_match(o.get("config", {}), best_candidate))
    if new_count >= min_new_samples and new_mean > current_mean * improvement_threshold:
        _write_current_config(study_store, best_candidate)
        logger.info(
            "loop evolution: replaced config (fitness %.3f → %.3f)",
            current_mean, new_mean,
        )
        return best_candidate
    return None


# ── GA internals ──────────────────────────────────────────────────

def _ga_step(
    population: list[dict],
    observations: list[dict],
    *,
    n_generations: int = 3,
    tournament_size: int = 3,
    crossover_rate: float = 0.7,
    mutation_rate: float = 0.2,
) -> list[dict]:
    """Run GA for a few generations, return top configs by estimated fitness."""
    pop = [dict(c) for c in population]
    for _ in range(n_generations):
        scored = [(_estimate_fitness(c, observations), c) for c in pop]
        scored.sort(key=lambda x: x[0], reverse=True)
        # Elitism: keep top 2
        next_pop = [c for _, c in scored[:2]]
        while len(next_pop) < len(pop):
            # Tournament selection
            p1 = _tournament_select(scored, tournament_size)
            p2 = _tournament_select(scored, tournament_size)
            child = _crossover(p1, p2, crossover_rate)
            child = _mutate(child, mutation_rate)
            next_pop.append(child)
        pop = next_pop
    scored = [(_estimate_fitness(c, observations), c) for c in pop]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored]


def _estimate_fitness(config: dict, observations: list[dict]) -> float:
    """Estimate config fitness from historical observations."""
    fits = [o["fitness"] for o in observations if _configs_match(o.get("config", {}), config)]
    if fits:
        return sum(fits) / len(fits)
    return 0.0


def _tournament_select(scored: list[tuple[float, dict]], k: int) -> dict:
    """Pick the best from k random candidates."""
    candidates = random.sample(scored, min(k, len(scored)))
    return max(candidates, key=lambda x: x[0])[1]


def _crossover(p1: dict, p2: dict, rate: float) -> dict:
    """Uniform crossover: for each param, randomly pick from p1 or p2."""
    child = {}
    for key in BOUNDS:
        if key in p1 or key in p2:
            if random.random() < rate:
                child[key] = p1.get(key, p2.get(key))
            else:
                child[key] = p2.get(key, p1.get(key))
    return child


def _mutate(config: dict, rate: float) -> dict:
    """Boundary mutation: with probability rate, nudge a param toward a random value within bounds."""
    mutated = dict(config)
    for key, (lo, hi) in BOUNDS.items():
        if key in mutated and random.random() < rate:
            if key in _INT_PARAMS:
                mutated[key] = random.randint(int(lo), int(hi))
            else:
                mutated[key] = round(random.uniform(lo, hi), 4)
    return mutated


def _configs_match(a: dict, b: dict) -> bool:
    """Check if two configs have the same tunable parameters."""
    return all(a.get(k) == b.get(k) for k in BOUNDS)


def _mean_fitness_for_config(observations: list[dict], config: dict) -> float:
    """Mean fitness of observations matching this config."""
    fits = [o["fitness"] for o in observations if _configs_match(o.get("config", {}), config)]
    return sum(fits) / len(fits) if fits else 0.0


def _fetch_observations(study_store: Any, limit: int = 50) -> list[dict]:
    """Fetch recent evolution observations."""
    import json as _json
    with study_store._lock:
        rows = study_store._conn.execute(
            "SELECT config_json, fitness FROM loop_config_evolution "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"config": _json.loads(r["config_json"]) if r["config_json"] else {},
         "fitness": r["fitness"]}
        for r in rows
    ]


def _read_current_config(study_store: Any) -> dict | None:
    """Read the current best config from the KV store."""
    import json as _json
    with study_store._lock:
        row = study_store._conn.execute(
            "SELECT config_json FROM loop_config_kv WHERE scope = 'global'",
        ).fetchone()
    if row and row["config_json"]:
        return _json.loads(row["config_json"])
    return None


def _write_current_config(study_store: Any, config: dict) -> None:
    """Write/replace the current best config in the KV store."""
    import json as _json
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with study_store._lock:
        study_store._conn.execute(
            "INSERT OR REPLACE INTO loop_config_kv (scope, config_json, updated_at) "
            "VALUES ('global', ?, ?)",
            (_json.dumps(config), now),
        )
        study_store._conn.commit()
