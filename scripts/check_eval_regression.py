#!/usr/bin/env python
"""Gate nightly eval scorecards against a committed baseline.

Usage:
    check_eval_regression.py                      # compare (exit 1 on regression)
    check_eval_regression.py --update-baseline    # refresh baseline from scorecard

Design (docs/archive/undated/p0-guardrails-design.md §2):
- mean drop > --max-mean-drop (default 0.02 absolute)  -> FAIL
- pass_rate@0.7 drop > --max-pass-drop (default 0.05)  -> FAIL
- no baseline yet -> write one and pass (first nightly establishes it)
- regressions NEVER rewrite the baseline (no self-exemption)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_SCORECARD = Path("scorecard.json")
DEFAULT_BASELINE = Path("evals/nightly-baseline.json")


def load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        # utf-8-sig: tolerate a BOM (Windows editors / PowerShell writes);
        # reads plain UTF-8 fine too. Then skip any non-JSON preamble
        # (e.g. library deprecation warnings leaked to stdout by an
        # imported dependency — seen from fitz/pymupdf).
        text = path.read_text(encoding="utf-8-sig")
        brace = text.find("{")
        if brace > 0:
            text = text[brace:]
        data = json.loads(text)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict) or "mean" not in data:
        print(f"error: {path} is not a scorecard dict", file=sys.stderr)
        sys.exit(2)
    return data


def _trajectory_regressions(
    scorecard: dict, baseline: dict, max_steps_drift: float,
) -> list[str]:
    """T1-T3 trajectory drift checks (docs/archive/undated/phase0-trajectory-eval-design.md §3).

    Returns human-readable regression lines; empty list means no drift.
    """
    problems: list[str] = []
    traj_now = scorecard.get("trajectory") or {}
    traj_base = baseline.get("trajectory") or {}
    base_tasks = {t.get("id"): t for t in (baseline.get("tasks") or [])}
    now_tasks = {t.get("id"): t for t in (scorecard.get("tasks") or [])}

    steps_now = float(traj_now.get("steps_mean") or 0.0)
    steps_base = float(traj_base.get("steps_mean") or 0.0)
    if steps_base > 0:
        rel = abs(steps_now - steps_base) / steps_base
        if rel > max_steps_drift:
            problems.append(
                f"T1: steps_mean {steps_base} -> {steps_now} "
                f"({rel:.0%} > {max_steps_drift:.0%})"
            )

    try:
        from difflib import SequenceMatcher

        drifted = []
        for tid, tn in now_tasks.items():
            tb = base_tasks.get(tid)
            if not tb:
                continue
            a = list(tb.get("tool_sequence") or [])
            b = list(tn.get("tool_sequence") or [])
            if not a and not b:
                continue
            if SequenceMatcher(None, a, b).ratio() < 0.5:
                drifted.append(tid)
        threshold_count = max(3, int(0.2 * max(1, len(base_tasks))))
        if len(drifted) >= threshold_count:
            problems.append(
                f"T2: {len(drifted)} tasks with tool-sequence similarity <0.5 "
                f"(threshold {threshold_count}): {drifted[:5]}"
            )
    except Exception as exc:  # noqa: BLE001
        print(f"note: T2 sequence check skipped ({exc})", file=sys.stderr)

    term_now = traj_now.get("terminal") or {}
    term_base = traj_base.get("terminal") or {}
    for reason, cnt in term_now.items():
        if reason != "stop" and term_base.get(reason, 0) == 0 and cnt > 0:
            problems.append(
                f"T3: new terminal {reason!r} x{cnt} (absent from baseline)"
            )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--max-mean-drop", type=float, default=0.02)
    ap.add_argument("--max-pass-drop", type=float, default=0.05)
    ap.add_argument(
        "--max-steps-drift",
        type=float,
        default=0.30,
        help="T1: max relative drift of trajectory steps_mean (default 0.30).",
    )
    ap.add_argument(
        "--update-baseline",
        action="store_true",
        help="Write the scorecard as the new baseline (explicit refresh only).",
    )
    args = ap.parse_args()

    scorecard = load(args.scorecard)
    if scorecard is None:
        print(f"error: scorecard not found: {args.scorecard}", file=sys.stderr)
        return 2

    mean = float(scorecard.get("mean", 0.0))
    pass_rate = float(scorecard.get("pass_rate@0.7", 0.0))
    n = scorecard.get("n", "?")

    if args.update_baseline:
        args.baseline.write_text(
            json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"baseline updated: {args.baseline} (mean={mean}, pass@0.7={pass_rate}, n={n})")
        return 0

    baseline = load(args.baseline)
    if baseline is None:
        args.baseline.write_text(
            json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"no baseline at {args.baseline}; established from this run "
              f"(mean={mean}, pass@0.7={pass_rate}, n={n})")
        return 0

    base_mean = float(baseline.get("mean", 0.0))
    base_pass = float(baseline.get("pass_rate@0.7", 0.0))
    mean_delta = mean - base_mean
    pass_delta = pass_rate - base_pass

    print(f"scorecard: mean={mean} (baseline {base_mean}, Δ{mean_delta:+.4f}), "
          f"pass@0.7={pass_rate} (baseline {base_pass}, Δ{pass_delta:+.4f}), n={n}")

    failed = False
    if mean_delta < -args.max_mean_drop:
        print(f"REGRESSION: mean dropped {abs(mean_delta):.4f} > {args.max_mean_drop}",
              file=sys.stderr)
        failed = True
    if pass_delta < -args.max_pass_drop:
        print(f"REGRESSION: pass_rate@0.7 dropped {abs(pass_delta):.4f} > {args.max_pass_drop}",
              file=sys.stderr)
        failed = True

    for problem in _trajectory_regressions(scorecard, baseline, args.max_steps_drift):
        print(f"REGRESSION(trajectory): {problem}", file=sys.stderr)
        failed = True

    if failed:
        print("\nBaseline NOT auto-updated (no self-exemption). "
              "If the drop is intentional, refresh with --update-baseline "
              "or workflow_dispatch input update_baseline=true.",
              file=sys.stderr)
        return 1

    # Improvements do not silently become the new baseline either —
    # refresh stays explicit so the comparison window stays stable.
    if mean_delta > 0.005 or pass_delta > 0.01:
        print(f"note: score improved (mean {mean_delta:+.4f}); "
              "consider refreshing the baseline via --update-baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
