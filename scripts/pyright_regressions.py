#!/usr/bin/env python
"""Pyright regressions-gate (docs/archive/undated/pyright-gradual-design.md §3).

Compares a pyright --outputjson report against the committed baseline;
exits 1 only when the error count INCREASES. This is the same
anti-self-exemption philosophy as the eval baseline: regressions are
gated, improvements must be explicitly captured via --update-baseline.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_REPORT = Path("pyright-report.json")
DEFAULT_BASELINE = Path("docs/pyright-baseline.json")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument(
        "--update-baseline",
        action="store_true",
        help="Write the current report's error count as the new baseline.",
    )
    args = ap.parse_args()

    if not args.report.exists():
        print(f"error: report not found: {args.report}", file=sys.stderr)
        return 2
    try:
        text = args.report.read_text(encoding="utf-8-sig")
        data = json.loads(text[text.find("{"):])
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot parse report: {exc}", file=sys.stderr)
        return 2

    summary = data.get("summary") or {}
    errors = int(summary.get("errorCount") or 0)
    warnings = int(summary.get("warningCount") or 0)
    files = int(summary.get("filesAnalyzed") or 0)

    if args.update_baseline:
        payload = {
            "errorCount": errors,
            "warningCount": warnings,
            "filesAnalyzed": files,
            "note": "regressions-gate baseline (docs/archive/undated/pyright-gradual-design.md §3)",
        }
        args.baseline.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        print(f"baseline updated: {args.baseline} (errors={errors}, warnings={warnings}, files={files})")
        return 0

    if not args.baseline.exists():
        args.baseline.write_text(
            json.dumps({"errorCount": errors, "warningCount": warnings,
                        "filesAnalyzed": files,
                        "note": "auto-created on first run"}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"no baseline; established errors={errors} (warnings={warnings}, files={files})")
        return 0

    try:
        base = json.loads(args.baseline.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot parse baseline: {exc}", file=sys.stderr)
        return 2
    base_errors = int(base.get("errorCount") or 0)
    delta = errors - base_errors
    print(f"pyright: errors={errors} (baseline {base_errors}, Δ{delta:+d}), "
          f"warnings={warnings}, files={files}")
    if delta > 0:
        print(
            f"REGRESSION: pyright errors increased by {delta} (new type debt). "
            "Fix the new errors or update the baseline via --update-baseline "
            "if intentional.",
            file=sys.stderr,
        )
        return 1
    if delta < 0:
        print(f"note: type debt decreased by {-delta} — consider refreshing the baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
