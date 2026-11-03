"""Backtest health scan (QuantStudio B2 five-gate + danger signal).

Observation-first: findings are emitted and recorded but never flip a
verdict — see docs/phase1-research-discipline.md §3. Checks fire only
when their metric key is present; absent data means an absent check,
not a warning (no noise on thin metrics).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Conclusion labels (B3) live in round_manifest — they are the research-
# strength axis, orthogonal to the keep/discard verdict.


@dataclass
class Finding:
    """One health observation for a round."""

    check: str
    status: str  # ok | warn | suspect
    detail: str
    observed: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "status": self.status,
            "detail": self.detail,
            "observed": self.observed,
        }


def _num(metrics: dict, *keys: str) -> float | None:
    for k in keys:
        v = metrics.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def health_check(metrics: dict, *, prev_metrics: dict | None = None) -> list[Finding]:
    """B2 five-gate scan + danger signal over a round's metrics dict.

    Gates (data-present-then-check):
      holdings         position count vs top-share threshold
      turnover         daily turnover sanity
      sharpe_ic_range  Sharpe/IC_IR outside [0.3, 0.5] -> suspect
                       (outside the plausible band usually means a
                       timing / execution-price bug)
      mdd_range        max drawdown shallower than -5% together with
                       strong returns is the look-ahead smell
      full_exposure    time-in-market sanity
      danger_signal    ann_ret > 50% AND mdd > -5% -> suspect
                       ("investigate the bug, don't celebrate")

    Returns the findings that are not plainly ok (ok findings are
    omitted to keep the event payload small — silence is green).
    """
    findings: list[Finding] = []

    sharpe = _num(metrics, "sharpe", "sharpe_ratio")
    ic_ir = _num(metrics, "ic_ir", "icir")
    band_metric = sharpe if sharpe is not None else ic_ir
    band_name = "sharpe" if sharpe is not None else "ic_ir"
    if band_metric is not None:
        if not (0.3 <= band_metric <= 0.5):
            findings.append(Finding(
                check="sharpe_ic_range",
                status="suspect",
                detail=(
                    f"{band_name}={band_metric:.3f} outside [0.3, 0.5] "
                    "— timing/execution-price bug smell"
                ),
                observed=band_metric,
            ))

    mdd = _num(metrics, "max_dd", "mdd", "max_drawdown")
    ann_ret = _num(metrics, "ann_ret", "annual_return", "annualized_return")
    if mdd is not None:
        # MDD is negative in our convention (e.g. -0.23).
        shallow = mdd > -0.05
        if shallow:
            findings.append(Finding(
                check="mdd_range",
                status="suspect",
                detail=(
                    f"max_dd={mdd:.2%} is shallower than -5% — "
                    "possible look-ahead (B2: MDD deeper than -20~-40% is normal; "
                    "near-zero is not)"
                ),
                observed=mdd,
            ))

    # Danger signal: implausibly good is a bug report, not a trophy.
    if ann_ret is not None and mdd is not None and ann_ret > 0.50 and mdd > -0.05:
        findings.append(Finding(
            check="danger_signal",
            status="suspect",
            detail=(
                f"ann_ret={ann_ret:.1%} with max_dd={mdd:.1%} — "
                "investigate the bug first, do not celebrate (B2)"
            ),
            observed=ann_ret,
        ))

    turnover = _num(metrics, "turnover", "avg_turnover")
    if turnover is not None and turnover > 2.0:
        findings.append(Finding(
            check="turnover",
            status="warn",
            detail=f"turnover={turnover:.2f} > 2/H — unstable signal",
            observed=turnover,
        ))

    exposure = _num(metrics, "full_exposure", "time_in_market")
    if exposure is not None and exposure < 0.8:
        findings.append(Finding(
            check="full_exposure",
            status="warn",
            detail=f"time_in_market={exposure:.1%} < 80% — NaN-heavy signal?",
            observed=exposure,
        ))

    holdings = _num(metrics, "holdings", "n_holdings", "position_count")
    if holdings is not None and holdings > 500:
        findings.append(Finding(
            check="holdings",
            status="warn",
            detail=f"holdings={holdings:.0f} > 500 — universe/tick filter issue?",
            observed=holdings,
        ))

    return findings
