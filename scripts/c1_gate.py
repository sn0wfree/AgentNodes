#!/usr/bin/env python
"""Compare a pytest run against the frozen baseline (C1 gate).

Purpose (docs/stage-c1-team-contract-20261028.md §6.2)
------------------------------------------------------
The gate is *set inclusion*, never a failure count:

    my failures  ⊆  baseline ∩ my files

Trade one regression for one accidental fix and the count stays equal --
which is exactly the failure mode a count-based gate cannot see. This
tool compares node ids.

Usage
-----
    # run your slice, capture the log, then check it
    set PYTHONPATH=<your worktree>\\src
    D:\\tools\\python312\\python.exe -m pytest tests/test_workflow_*.py \
        -q --tb=no --no-header > my.log 2>&1
    D:\\tools\\python312\\python.exe scripts/c1_gate.py my.log

Exit codes
----------
    0  clean  -- no new failures
    1  regression -- at least one failure outside the baseline
    2  usage error (missing log / baseline)

New failures are printed with their file, so the owner is obvious.
Fixed baseline entries are reported too (informational only -- they are
what "随 C1 自然消失" looks like, and a large number deserves a look,
not a celebration).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_BASELINE = Path("tests/baselines/pr-lane-failures.json")
_SUMMARY_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+.*)?$")


def parse_log(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _SUMMARY_RE.match(line.strip())
        if not m:
            continue
        kind, node_id = m.group(1), m.group(2)
        if out.get(node_id) != "FAILED":
            out[node_id] = kind
    return out


def load_baseline(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(
            f"error: baseline not found: {path}\n"
            "       generate it first with scripts/c1_baseline.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", help="pytest output file, or - for stdin")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument(
        "--files", nargs="*", default=None,
        help="only consider failures in these files (your ownership slice)",
    )
    ap.add_argument("--quiet", action="store_true", help="only print the verdict")
    args = ap.parse_args(argv)

    baseline = load_baseline(Path(args.baseline))
    known = set(baseline["known_failures"])

    if args.log == "-":
        text = sys.stdin.read()
    else:
        p = Path(args.log)
        if not p.exists():
            print(f"error: log not found: {p}", file=sys.stderr)
            return 2
        text = p.read_text(encoding="utf-8", errors="replace")

    current = parse_log(text)
    if args.files:
        wanted = tuple(args.files)
        # Scope BOTH sides to the slice. Filtering only `current` would
        # report every other file's baseline entry as "now passes", which
        # is pure noise: the worker never ran those tests.
        known = {
            node for node in known
            if any(node.startswith(w) or f"/{w}" in node for w in wanted)
        }
        current = {
            node: kind for node, kind in current.items()
            if any(node.startswith(w) or f"/{w}" in node for w in wanted)
        }

    new_failures = sorted(set(current) - known)
    fixed = sorted(known - set(current))

    if not args.quiet:
        print(f"baseline: {args.baseline}")
        print(f"  commit  : {baseline.get('commit', '?')[:12]}")
        print(f"  entries : {len(known)}")
        print(f"current run failures (in scope): {len(current)}")
        print()

    if new_failures:
        print(f"REGRESSIONS ({len(new_failures)}) -- not in baseline:")
        for node in new_failures:
            print(f"  {current[node]:6s} {node}")
        print()
        print("These are yours to explain. Either fix them, or if they are")
        print("genuinely pre-existing, add them to the baseline in a")
        print("separate commit with a one-line reason.")
        return 1

    if not args.quiet:
        print("clean: no failures outside the baseline.")
        if fixed:
            # Informational: baseline entries that did not fail this run.
            # Two very different causes, so say which one applies:
            #   - the worker ran a narrow slice (most entries never ran)
            #   - a migration genuinely fixed them (stale baseline)
            print(f"\nbaseline entries not failing in this run ({len(fixed)}):")
            for node in fixed[:25]:
                print(f"  {node}")
            if len(fixed) > 25:
                print(f"  ... and {len(fixed) - 25} more")
            print("\nMost of these probably did not run -- this tool only sees")
            print("the tests you invoked. If you ran the full PR lane and this")
            print("list is still large, refresh the baseline in a separate")
            print("commit so the next worker starts from truth.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
