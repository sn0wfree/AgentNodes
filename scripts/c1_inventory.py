#!/usr/bin/env python
"""Freeze and verify the collected-test inventory (C1 anti-silent-loss gate).

Why this exists (docs/stage-c1-acceptance-20261028.md §2.2)
-----------------------------------------------------------
During a migration, ~100 baseline failures are *supposed* to disappear.
That makes deleting a test file indistinguishable from fixing it, unless
the set of test ids is itself under version control.

This tool answers one question: **did the set of runnable tests shrink?**

- A deleted test file, a deleted test function, a new ``pytest.skip`` at
  module level, or a widened marker exclusion all show up as REMOVED.
- Adding tests shows up as ADDED (informational -- that is how new
  coverage looks).

Removal is not automatically wrong. Deleting a suite that only tested the
old executor is the *point* of C1. But it must be a deliberate, reviewed
deletion: refresh the frozen inventory in the same commit that deletes
the tests, so the diff shows both.

Usage
-----
    # one-off: freeze the current inventory (coordinator only)
    D:\\tools\\python312\\python.exe -m pytest tests --collect-only -q \\
        -m "not llm and not e2e and not slow" > collect.log 2>&1
    D:\\tools\\python312\\python.exe scripts/c1_inventory.py freeze collect.log

    # every worker, before requesting merge
    D:\\tools\\python312\\python.exe -m pytest tests --collect-only -q \\
        -m "not llm and not e2e and not slow" > collect.log 2>&1
    D:\\tools\\python312\\python.exe scripts/c1_inventory.py verify collect.log

Exit codes
----------
    0  no tests disappeared
    1  tests disappeared (list printed with their file)
    2  usage error

Caveat: this compares *collection*, not execution. A test that used to
run and now xfails/skips at runtime is invisible here -- that is what
scripts/c1_gate.py covers. The two gates are complementary: one watches
the set, the other watches the outcomes.
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

DEFAULT_OUT = Path("tests/baselines/pr-lane-nodeids.json")

# Collected node ids look like:
#   tests/test_x.py::TestY::test_z
#   tests/test_x.py::test_z[a-1]
#   tests/test_x.py::test_z[STOP kill all]     <- params may contain spaces
# The trailing "N/M tests collected" line must not match, and neither
# must the deselected/warning lines. Anchoring on "tests/" + "::" is what
# separates a node id from prose.
_NODE_RE = re.compile(r"^(tests[/\\]\S.*?::.+)$")


def parse_collect_log(text: str) -> list[str]:
    """Return sorted, de-duplicated collected node ids."""
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        # --collect-only -q prints one node id per line; -vv variants add
        # "::" detail, which we keep as-is.
        m = _NODE_RE.match(line)
        if m:
            seen.add(m.group(1).replace("\\", "/"))
    return sorted(seen)


def current_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"error: log not found: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def cmd_freeze(log: Path, out: Path) -> int:
    nodes = parse_collect_log(_read(log))
    if not nodes:
        print(
            "error: parsed zero node ids -- check that the log came from\n"
            "       pytest --collect-only -q (not a normal run).",
            file=sys.stderr,
        )
        return 2
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": current_commit(),
        "count": len(nodes),
        "nodeids": nodes,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"froze {len(nodes)} node ids -> {out}")
    return 0


def cmd_verify(log: Path, baseline: Path, allow_shrink: bool) -> int:
    if not baseline.exists():
        raise SystemExit(
            f"error: frozen inventory not found: {baseline}\n"
            "       freeze it first (scripts/c1_inventory.py freeze <collect.log>)"
        )
    frozen = set(json.loads(baseline.read_text(encoding="utf-8"))["nodeids"])
    current = set(parse_collect_log(_read(log)))

    removed = sorted(frozen - current)
    added = sorted(current - frozen)

    print(f"frozen : {len(frozen)}")
    print(f"current: {len(current)}")
    print(f"delta  : {len(added)} added, {len(removed)} removed")
    print()

    if removed:
        by_file = Counter(n.split("::")[0] for n in removed)
        print(f"REMOVED ({len(removed)}) -- these no longer collect:")
        for name, n in by_file.most_common():
            print(f"  {n:4d}  {name}")
        print()
        print("If this is the intended retirement of old-executor tests, refreeze")
        print("the inventory in the SAME commit that deletes them, so the diff")
        print("shows both halves. If not, you have silently dropped coverage.")
        if not allow_shrink:
            return 1

    if added and len(added) <= 40:
        print(f"ADDED ({len(added)}) -- new coverage:")
        for node in added:
            print(f"  {node}")
    elif added:
        by_file = Counter(n.split("::")[0] for n in added)
        print(f"ADDED ({len(added)}) across {len(by_file)} files, top:")
        for name, n in by_file.most_common(10):
            print(f"  {n:4d}  {name}")

    if not removed:
        print("clean: no collected test disappeared.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("freeze", help="write the inventory from a collect log")
    f.add_argument("log")
    f.add_argument("--out", default=str(DEFAULT_OUT))

    v = sub.add_parser("verify", help="compare a collect log to the inventory")
    v.add_argument("log")
    v.add_argument("--baseline", default=str(DEFAULT_OUT))
    v.add_argument(
        "--allow-shrink", action="store_true",
        help="report removals but exit 0 (for the refreeze commit itself)",
    )

    args = ap.parse_args(argv)
    if args.cmd == "freeze":
        return cmd_freeze(Path(args.log), Path(args.out))
    return cmd_verify(Path(args.log), Path(args.baseline), args.allow_shrink)


if __name__ == "__main__":
    raise SystemExit(main())
