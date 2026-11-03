"""Phase 1 research-discipline acceptance (docs/phase1-research-discipline.md).

Covers: B2 five-gate health scan (data-present-then-check + danger
signal), B3 conclusion labels + manifest validation, and the
single-source scoring iron rule (no inlined factor-quality formulas
outside factor_validate).
"""
from __future__ import annotations

import re
from pathlib import Path

from strategy_research.apps.study.health import health_check
from strategy_research.apps.study.round_manifest import (
    CONCLUSION_LABELS,
    build_manifest,
    validate_manifest,
)

REPO = Path(__file__).resolve().parents[1]


# ── B2 health_check ─────────────────────────────────────────────


def test_suspect_sharpe_outside_band():
    findings = health_check({"sharpe": 1.8})
    checks = {f.check: f for f in findings}
    assert "sharpe_ic_range" in checks
    assert checks["sharpe_ic_range"].status == "suspect"


def test_band_metric_inside_band_silent():
    assert health_check({"sharpe": 0.45, "ic_ir": 0.4}) == []


def test_mdd_near_zero_suspect_lookahead():
    findings = health_check({"max_dd": -0.01})
    checks = {f.check: f for f in findings}
    assert "mdd_range" in checks
    assert checks["mdd_range"].status == "suspect"


def test_danger_signal_triggers():
    findings = health_check({"ann_ret": 0.8, "max_dd": -0.02})
    checks = {f.check: f for f in findings}
    assert "danger_signal" in checks
    assert "investigate" in checks["danger_signal"].detail


def test_absent_metrics_skip_quietly():
    # keys missing entirely -> no findings (no noise on thin metrics)
    assert health_check({"calmar": 0.7}) == []


def test_turnover_and_exposure_warn():
    findings = health_check({"turnover": 3.5, "full_exposure": 0.5})
    checks = {f.check for f in findings}
    assert {"turnover", "full_exposure"} <= checks


# ── B3/B4 manifest contract ─────────────────────────────────────


def test_conclusion_labels_are_b3_set():
    assert CONCLUSION_LABELS == {
        "effective", "weakly", "ineffective", "regime-dependent", "inconclusive",
    }


def _valid_manifest() -> dict:
    return build_manifest(
        round_num=1,
        inherited_from=None,
        adopted_run=None,
        run_name="run_0001",
        hypothesis="h1",
        levers=["top_n"],
        predicted_affected=["calmar"],
        strategy_changes=None,
        metrics={"calmar": 0.7, "sharpe": 0.4, "max_dd": -0.2, "ann_return": 0.12},
        prev_metrics=None,
        baseline_metrics=None,
        verdict="keep",
        verdict_reason="targets met",
        gates=None,
        budget={"turns_used": 1},
    )


def test_valid_manifest_passes_validation():
    assert validate_manifest(_valid_manifest()) == []


def test_missing_fields_warn_without_raising():
    m = _valid_manifest()
    m["hypothesis"] = {"text": ""}
    m["verdict"] = {}
    problems = validate_manifest(m)
    assert any("hypothesis.text" in p for p in problems)
    assert any("verdict.decision" in p for p in problems)


def test_bad_conclusion_rejected():
    m = _valid_manifest()
    m["conclusion"] = "nailed_it"
    problems = validate_manifest(m)
    assert any("CONCLUSION_LABELS" in p for p in problems)


def test_default_conclusion_is_conservative():
    assert _valid_manifest().get("conclusion") == "inconclusive"


# ── single-source scoring iron rule ─────────────────────────────


def test_factor_quality_scoring_lives_only_in_factor_validate():
    """No other module may inline factor-quality scoring formulas.

    Whitelist: factor_validate itself; config_runner._compute_scores
    and templates/prepare._compute_factor_scores are portfolio blends
    (different semantics) and are annotated as such.
    """
    pattern = re.compile(r"def (compute_ic|score_stability|score_rank_ic|compute_overall_score)\b")
    offenders = []
    for p in (REPO / "src").rglob("*.py"):
        rel = str(p.relative_to(REPO))
        if "alpha_zoo" in rel or "__pycache__" in rel:
            continue
        if rel.endswith("factor_validate.py"):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            offenders.append(rel)
    assert offenders == [], f"factor-quality scoring duplicated in: {offenders}"
