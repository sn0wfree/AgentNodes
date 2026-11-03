"""Strategy acceptance module — config-driven keep/discard decision (P6 Step 0).

Architecture
------------
The pipeline produces ``AcceptanceDecision`` as a single source of truth for
"should this strategy be kept?". The decision is composed of two layers in
v1.0.0:

  1. **Hard threshold** (``rules.HardThresholdRule``)
     - Pure-Python rule against metrics.json numeric fields
     - Default thresholds from ``AcceptanceConfig.hard``:
       calmar_min, sharpe_min, max_dd_min, trades_min
     - Deterministic, no LLM cost, catches obvious failures

  2. **LLM evaluator** (``llm_eval.LLMEvaluator``)
     - Optional: triggered when ``AcceptanceConfig.llm.enabled=True``
     - Asks the configured LLM to score the run qualitatively
     - Returns ``{"passed": bool, "score": float (0-1), "reason": str}``

The two layers combine in ``decide()`` per the policy:

  - hard_passed = False  → reject (LLM is moot; metrics fail objective thresholds)
  - hard_passed = True & llm enabled & llm_passed = False → reject
  - hard_passed = True & (llm disabled OR llm_passed = True) → accept

Future rules (v1.1+): percentile-of-history, Bayesian drift, ensemble voting.
Drop them into ``rules.py`` and add them to ``decide()``.

Why a separate module?
----------------------
* Single function ``decide(metrics, llm_verdict, cfg) -> AcceptanceDecision``
  is the ONLY thing CLI / autoresearch / swarm / MCP / hooks need to know.
* Tuning thresholds = edit ``config.yaml`` (no code change).
* Adding rules = subclass + register in ``decide()`` (single dispatch site).
* Replaying history = ``quantnodes-research accept --metrics-file …``.

Layered config (priority high → low):
    1. CLI flags (``--calmar-min 0.7 …``)
    2. Workspace acceptance.yaml   (``<ws>/acceptance.yaml``)
    3. User ``~/.quantnodes-research/acceptance.yaml``
    4. Built-in defaults in ``AcceptanceConfig``

Public API
----------
    AcceptanceConfig     - dataclass with all thresholds + flags
    AcceptanceDecision   - dataclass with verdict + breakdown
    decide               - the single entry point
    load_config          - 4-layer merge
    DEFAULT_CONFIG       - built-in defaults
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .llm_eval import LLMEvaluator
from .rules import HardThresholdRule, RuleResult

__all__ = [
    "AcceptanceConfig",
    "AcceptanceDecision",
    "DEFAULT_CONFIG",
    "decide",
    "load_config",
    "HardThresholdRule",
    "RuleResult",
    "LLMEvaluator",
]


# ── Config dataclass ─────────────────────────────────────────────────


@dataclass(frozen=True)
class AcceptanceConfig:
    """Configuration for the dual-layer acceptance decision.

    All fields can be overridden via YAML or CLI flags (see ``load_config``).
    """

    # Hard thresholds (pure-Python rule)
    hard_calmar_min: float = 0.5
    hard_sharpe_min: float = 0.3
    hard_max_dd_min: float = -0.15      # max_dd must be >= this (e.g. -0.20 fails)
    hard_trades_min: int = 30           # minimum non-zero trade count
    hard_ann_return_min: float = 0.0    # 0.0 disables this check

    # LLM layer
    llm_enabled: bool = True
    llm_weight: float = 1.0            # how much weight LLM gets in overall verdict
                                        # 1.0 = LLM verdict required to override hard pass
                                        # 0.0 = LLM verdict is advisory only
    llm_score_threshold: float = 0.5    # LLM "passed" if score >= threshold
    llm_timeout_s: float = 30.0
    llm_model: str | None = None        # None = use LLMConfig default

    # Behaviour
    require_all_hard: bool = True       # all hard checks must pass
                                        # False = at least one must pass (lenient)
    stagnation_patience: int = 10       # consecutive rejects before autoresearch stops

    def with_overrides(self, **kwargs: Any) -> "AcceptanceConfig":
        """Return a new config with the given fields overridden."""
        valid = {f.name for f in _config_fields()}
        clean = {k: v for k, v in kwargs.items() if k in valid and v is not None}
        if not clean:
            return self
        import dataclasses as _dc
        return _dc.replace(self, **clean)


def _config_fields():
    """Helper for field introspection (avoids import-of-self)."""
    import dataclasses as _dc
    return _dc.fields(AcceptanceConfig)


# Built-in defaults; users can copy and modify via ``load_config``
DEFAULT_CONFIG = AcceptanceConfig()


# ── Decision dataclass ────────────────────────────────────────────────


@dataclass(frozen=True)
class AcceptanceDecision:
    """Single source of truth for a keep/discard verdict.

    Attributes:
        accept: Final yes/no.
        reason: Human-readable explanation (for logs / TSV / UI).
        hard_passed: Result of the hard threshold layer.
        llm_passed:  Result of the LLM layer (None = LLM disabled or skipped).
        hard_detail: Per-metric pass/fail breakdown from the hard layer.
        llm_detail:  Raw LLM verdict dict (None = not invoked).
        stagnation_triggered: True if this was an auto-stop due to repeated rejects.
    """

    accept: bool
    reason: str
    hard_passed: bool
    llm_passed: bool | None = None
    hard_detail: dict[str, bool] = field(default_factory=dict)
    llm_detail: dict[str, Any] | None = None
    stagnation_triggered: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise for JSON / TSV / DuckDB."""
        return {
            "accept": self.accept,
            "reason": self.reason,
            "hard_passed": self.hard_passed,
            "llm_passed": self.llm_passed,
            "hard_detail": dict(self.hard_detail),
            "llm_detail": dict(self.llm_detail) if self.llm_detail else None,
            "stagnation_triggered": self.stagnation_triggered,
        }


# ── Core decision function ────────────────────────────────────────────


def decide(
    metrics: Mapping[str, Any],
    *,
    llm_verdict: dict[str, Any] | None = None,
    cfg: AcceptanceConfig | None = None,
    stagnation_count: int = 0,
) -> AcceptanceDecision:
    """Compute a keep/discard decision from metrics + optional LLM verdict.

    Args:
        metrics: Strategy run metrics (calmar, sharpe, max_dd, ann_return,
                 trades, win_rate, …). Missing keys coerce to 0.0 — which
                 fails every hard gate except ``ann_return`` when disabled —
                 except ``max_dd``: an absent/unparsable drawdown fails
                 closed (−inf) instead of reading as "no drawdown".
                 ``max_drawdown``/``mdd`` are accepted as aliases of
                 ``max_dd`` (engine-path key names).
        llm_verdict: Pre-computed LLM verdict dict (skip LLM call). Schema:
            ``{"passed": bool, "score": float, "reason": str, ...}``.
            Pass ``None`` to skip LLM layer entirely.
        cfg: AcceptanceConfig; defaults to built-in.
        stagnation_count: How many consecutive rejects preceded this one.
            When ``>= cfg.stagnation_patience``, ``stagnation_triggered=True``
            is returned (driving autoresearch to stop) and the verdict is
            reject — stagnation breaks the loop, it never blesses the
            strategy.

    Returns:
        ``AcceptanceDecision`` with full breakdown. The pipeline can log
        every component to results.tsv / summary.json for audit.
    """
    cfg = cfg or DEFAULT_CONFIG
    metrics_dict = _coerce_metrics(metrics)

    # Layer 1: Hard threshold
    rule = HardThresholdRule()
    result = rule.check(metrics_dict, cfg)
    hard_passed = result.passed
    hard_detail = dict(result.detail)

    # Stagnation override (always wins — but as a STOP, not a pass).
    # Historical behaviour forced accept=True here "to break the loop",
    # which blessed an unqualified strategy with a keep verdict. The
    # loop-breaking signal is ``stagnation_triggered`` (both the study
    # runner and the CLI autoresearch loop check it directly); the
    # acceptance verdict itself stays honest: N consecutive rejects is
    # evidence the strategy is NOT good enough, never a quality pass.
    if stagnation_count >= cfg.stagnation_patience and stagnation_count > 0:
        return AcceptanceDecision(
            accept=False,
            reason=(
                f"stagnation: {stagnation_count} consecutive rejects >= "
                f"patience={cfg.stagnation_patience}; autoresearch stops "
                f"WITHOUT acceptance (stagnation is a stop signal, not a "
                f"quality pass)"
            ),
            hard_passed=hard_passed,
            llm_passed=None,
            hard_detail=hard_detail,
            llm_detail=None,
            stagnation_triggered=True,
        )

    # Layer 2: LLM (optional)
    llm_passed: bool | None = None
    llm_detail: dict[str, Any] | None = None
    if llm_verdict is not None:
        llm_passed = bool(llm_verdict.get("passed", False))
        llm_detail = dict(llm_verdict)

    # Combine layers
    return _combine(hard_passed, llm_passed, hard_detail, llm_detail, cfg)


def _combine(
    hard_passed: bool,
    llm_passed: bool | None,
    hard_detail: dict[str, bool],
    llm_detail: dict[str, Any] | None,
    cfg: AcceptanceConfig,
) -> AcceptanceDecision:
    """Apply the combine policy: hard AND (LLM or skip)."""
    if not hard_passed:
        failed = [k for k, v in hard_detail.items() if not v]
        return AcceptanceDecision(
            accept=False,
            reason=f"hard threshold failed: {', '.join(failed) or '<no detail>'}",
            hard_passed=False,
            llm_passed=llm_passed,
            hard_detail=hard_detail,
            llm_detail=llm_detail,
        )

    if llm_passed is None:
        return AcceptanceDecision(
            accept=True,
            reason="hard threshold passed; LLM layer not invoked",
            hard_passed=True,
            llm_passed=None,
            hard_detail=hard_detail,
            llm_detail=llm_detail,
        )

    if llm_passed:
        score = (llm_detail or {}).get("score", 1.0)
        reason = (llm_detail or {}).get("reason", "")
        return AcceptanceDecision(
            accept=True,
            reason=f"hard + LLM passed (score={score:.2f}): {reason}",
            hard_passed=True,
            llm_passed=True,
            hard_detail=hard_detail,
            llm_detail=llm_detail,
        )

    score = (llm_detail or {}).get("score", 0.0)
    reason = (llm_detail or {}).get("reason", "")
    return AcceptanceDecision(
        accept=False,
        reason=f"hard passed but LLM rejected (score={score:.2f}): {reason}",
        hard_passed=True,
        llm_passed=False,
        hard_detail=hard_detail,
        llm_detail=llm_detail,
    )


# ── Config loading ──────────────────────────────────────────────────


def load_config(
    *,
    cli_overrides: Mapping[str, Any] | None = None,
    workspace_config: Path | None = None,
    user_config: Path | None = None,
) -> AcceptanceConfig:
    """Build an AcceptanceConfig from layered sources.

    Priority (high → low):
        1. ``cli_overrides``     — kwargs from ``--calmar-min 0.7 …``
        2. ``workspace_config``  — ``<ws>/acceptance.yaml``
        3. ``user_config``       — ``~/.quantnodes-research/acceptance.yaml``
        4. ``DEFAULT_CONFIG``    — built-in defaults

    A missing user/workspace config is silent (returns empty dict). A
    malformed YAML raises ``yaml.YAMLError`` (transparent from PyYAML).
    Unknown keys are silently ignored (forward-compat).
    """
    from strategy_research.foundation.config_loader import load_layered_config, scalar_key_filter

    user_path = user_config if user_config is not None else (
        Path.home() / ".quantnodes-research" / "acceptance.yaml"
    )
    defaults = {f.name: getattr(DEFAULT_CONFIG, f.name) for f in _config_fields()}
    merged = load_layered_config(
        defaults=defaults,
        cli_overrides=cli_overrides,
        workspace_path=workspace_config,
        user_path=user_path,
        key_filter=scalar_key_filter,
    )
    return DEFAULT_CONFIG.with_overrides(**merged)


# Key aliases: producers disagree on spelling — the script path
# (templates/prepare.py) prints ``max_dd``/``turnover``, while the engine
# path (core.utils.metrics.extended_metrics) emits ``max_drawdown`` and
# ``ann_turnover``. Without these aliases an engine-path metrics dict
# silently coerced max_dd to the missing-value default below, and the
# drawdown gate never fired.
_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "max_dd": ("max_drawdown", "mdd"),
    "turnover": ("ann_turnover",),
}

# Missing-value defaults per coerced key. 0.0 is safe (fails the gate)
# for every hard-gated metric except max_dd: drawdown is negative, so a
# 0.0 default reads as "no drawdown" and PASSES hard_max_dd_min. An
# absent/unparsable drawdown therefore fails closed.
_MISSING_DEFAULTS: dict[str, float] = {
    "calmar": 0.0,
    "sharpe": 0.0,
    "max_dd": float("-inf"),
    "ann_return": 0.0,
    "ann_vol": 0.0,
    "sortino": 0.0,
    "turnover": 0.0,
    "win_rate": 0.0,
}


def _coerce_metrics(metrics: Mapping[str, Any]) -> dict[str, float]:
    """Cast metrics values to float/int with per-key missing defaults.

    Falls back to the alias keys in ``_KEY_ALIASES`` before applying the
    default, so engine- and script-path metrics dicts are treated alike.
    """
    out: dict[str, float] = {}
    for key in ("calmar", "sharpe", "max_dd", "ann_return", "ann_vol",
                "sortino", "turnover", "win_rate"):
        v = metrics.get(key)
        if v is None:
            for alias in _KEY_ALIASES.get(key, ()):
                v = metrics.get(alias)
                if v is not None:
                    break
        try:
            out[key] = float(v) if v is not None else _MISSING_DEFAULTS[key]
        except (TypeError, ValueError):
            out[key] = _MISSING_DEFAULTS[key]
    out["trades"] = int(metrics.get("trades", 0) or 0)
    return out
