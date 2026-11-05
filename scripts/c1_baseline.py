#!/usr/bin/env python
"""Extract the PR-lane failure baseline for the C1 team gate.

Purpose (docs/archive/2026-10/stage-c1-team-contract-20261028.md §6.1)
------------------------------------------------------
Each worker's green condition is "my failures are a subset of the
baseline that falls inside my own files". Counting failures is not
enough -- the comparison must be by node id, otherwise a worker can
trade one regression for one accidental fix and report zero delta.

Usage
-----
    # 1. produce a raw pytest log (quiet, no tracebacks)
    D:\\tools\\python312\\python.exe -m pytest tests -q --tb=no \
        -m "not llm and not e2e and not slow" --no-header > raw.log 2>&1

    # 2. turn it into the machine-readable baseline
    D:\\tools\\python312\\python.exe scripts/c1_baseline.py raw.log

The parser reads pytest's short test summary info lines:

    FAILED tests/test_x.py::test_y - AssertionError: ...
    ERROR tests/test_x.py::test_z - fixture 'w' not found

Both are recorded: an ERROR is a test that could not even run, which is
just as much a baseline entry as a FAILED one. Warnings/config noise is
ignored.

Output goes to tests/baselines/pr-lane-failures.json:

    {
      "generated_at": "...",
      "commit": "...",
      "command": "...",
      "counts": {"failed": 127, "error": 7, "total": 134},
      "known_failures": ["tests/test_a.py::test_b", ...],
      "by_file": {"tests/test_a.py": 3, ...}
    }
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OUT = Path("tests/baselines/pr-lane-failures.json")

# "FAILED tests/x.py::test_y - msg" / "ERROR tests/x.py::test_z - msg"
# The node id is everything up to the first whitespace or " - ".
_SUMMARY_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+.*)?$")


def parse_log(text: str) -> dict[str, str]:
    """Return {node_id: "FAILED"|"ERROR"} for every summary line."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _SUMMARY_RE.match(line.strip())
        if not m:
            continue
        kind, node_id = m.group(1), m.group(2)
        # A node appearing twice (reruns) keeps the worst status.
        if out.get(node_id) != "FAILED":
            out[node_id] = kind
    return out


def parse_totals(text: str) -> dict[str, int]:
    """Pull the last '=== N failed, M passed ... ===' line if present."""
    totals: dict[str, int] = {}
    for line in text.splitlines():
        if "passed" not in line and "failed" not in line:
            continue
        for num, word in re.findall(r"(\d+)\s+(passed|failed|error|errors|skipped)", line):
            key = word.rstrip("s") if word.startswith("error") else word
            totals[key] = int(num)
    return totals


def current_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def build_payload(text: str, command: str) -> dict:
    failures = parse_log(text)
    by_file = Counter(node.split("::")[0] for node in failures)
    failed = sum(1 for v in failures.values() if v == "FAILED")
    errored = sum(1 for v in failures.values() if v == "ERROR")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": current_commit(),
        "command": command,
        # `counts` is derived from the parsed node ids -- the authoritative
        # number. pytest's own tally goes in `pytest_totals` unchanged: it
        # counts parametrised cases differently, so letting it overwrite
        # `counts` would silently change what the gate compares against.
        "counts": {
            "failed": failed,
            "error": errored,
            "total": len(failures),
        },
        "pytest_totals": parse_totals(text),
        "known_failures": sorted(failures),
        "by_file": dict(by_file.most_common()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", help="raw pytest output file (or - for stdin)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--command", default="", help="the pytest command that produced the log")
    args = ap.parse_args(argv)

    if args.log == "-":
        text = sys.stdin.read()
    else:
        path = Path(args.log)
        if not path.exists():
            print(f"error: log not found: {path}", file=sys.stderr)
            return 2
        text = path.read_text(encoding="utf-8", errors="replace")

    payload = build_payload(text, args.command)
    if not payload["known_failures"]:
        print(
            "error: parsed zero failures -- did you drop --tb=no? "
            "without it the summary lines still appear, but check the log.",
            file=sys.stderr,
        )
        return 3

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    c = payload["counts"]
    print(f"wrote {out}")
    print(f"  failed={c['failed']} error={c['error']} total={len(payload['known_failures'])}")
    print("  top files:")
    for name, n in list(payload["by_file"].items())[:12]:
        print(f"    {n:4d}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
