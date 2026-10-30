#!/usr/bin/env python
"""List pytest files for one shard.

Two modes:
- round-robin (default): stable order, mod-arity by sorted file name.
  Used for first runs / no timing data; predictable but not time-balanced.
- time-aware: LPT bin-packing on a `.shard-timings.json` of {relpath: seconds}.
  Falls back to round-robin + stderr warning when timings are missing.

Both modes skip `test_session_triggers.py` when it exists (known heavy/flake
magnet, runs in its own nightly job — see docs/ci-sharding-strategy.md). It
was retired with the legacy session stack in B4 Step 4-5, so the skip set is
now usually empty.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_TIMINGS = Path(".shard-timings.json")
# Kept as a set (not hard-coded to empty) so a future heavy suite can be
# excluded from sharding without touching the logic below.
DEFAULT_SKIP: set[str] = set()


def _collect_files(root: Path) -> list[Path]:
    root = root.resolve()
    return sorted(
        p for p in root.glob("test_*.py") if p.name not in DEFAULT_SKIP
    )


def _shard_round_robin(files: list[Path], shard: int, shards: int) -> list[Path]:
    return [p for i, p in enumerate(files) if i % shards == shard]


def _shard_time_aware(
    files: list[Path],
    shard: int,
    shards: int,
    timings: dict[str, float],
    anchor: Path,
) -> list[Path]:
    """LPT bin-packing: sort by duration desc, drop into the lightest bucket.

    Missing entries are treated as 1.0s (the median lightweight test) so a
    brand-new test file still gets a sensible slot instead of zero cost.
    """
    def _rel(p: Path) -> str:
        # Normalize to forward-slash POSIX-style relative to anchor; matches
        # the format produced by collect_shard_timings*.py
        return str(p.relative_to(anchor)).replace("\\", "/")

    def _cost(p: Path) -> float:
        return timings.get(_rel(p), 1.0)

    items = sorted(files, key=_cost, reverse=True)
    buckets: list[list[Path]] = [[] for _ in range(shards)]
    loads = [0.0] * shards
    fallback = 0
    for p in items:
        cost = timings.get(_rel(p))
        if cost is None:
            cost = 1.0
            fallback += 1
        idx = loads.index(min(loads))
        buckets[idx].append(p)
        loads[idx] += cost
    if fallback:
        print(
            f"warning: {fallback} files have no timing data; using 1.0s default",
            file=sys.stderr,
        )
    return sorted(buckets[shard])


def _print_summary(
    files: list[Path], shard: int, shards: int, timings: dict[str, float], anchor: Path
) -> None:
    def _cost(p: Path) -> float:
        rel = str(p.relative_to(anchor)).replace("\\", "/")
        return timings.get(rel, 1.0)

    total_time = sum(_cost(p) for p in files)
    print(
        f"shard {shard}/{shards}: {len(files)} files, est ~{total_time:.1f}s",
        file=sys.stderr,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=4)
    ap.add_argument("--root", default="tests")
    ap.add_argument(
        "--mode",
        choices=["round-robin", "time-aware"],
        default="round-robin",
    )
    ap.add_argument(
        "--timings",
        type=Path,
        default=DEFAULT_TIMINGS,
        help="Path to .shard-timings.json (only used in time-aware mode).",
    )
    ap.add_argument(
        "--summary",
        action="store_true",
        help="Print a one-line summary of this shard to stderr.",
    )
    args = ap.parse_args()

    files = _collect_files(Path(args.root))
    if not files:
        return 0

    # Anchor for relative-to: matches the keys in .shard-timings.json
    # (collect_shard_timings*.py writes "tests/<file>.py" relative to cwd).
    anchor = Path.cwd()

    timings: dict[str, float] = {}
    if args.mode == "time-aware":
        if args.timings.exists():
            try:
                raw = json.loads(args.timings.read_text(encoding="utf-8"))
                # accept either {file: sec} or pytest-split's {file: [durs]}
                timings = {
                    k.replace("\\", "/"): (
                        sum(v) / len(v) if isinstance(v, list) else float(v)
                    )
                    for k, v in raw.items()
                }
            except (OSError, ValueError) as exc:
                print(f"warning: failed to read {args.timings}: {exc}", file=sys.stderr)
        if not timings:
            print(
                f"warning: no timings in {args.timings}; falling back to round-robin",
                file=sys.stderr,
            )
            selected = _shard_round_robin(files, args.shard, args.shards)
        else:
            selected = _shard_time_aware(files, args.shard, args.shards, timings, anchor)
    else:
        selected = _shard_round_robin(files, args.shard, args.shards)

    if args.summary:
        _print_summary(
            selected,
            args.shard,
            args.shards,
            timings,
            anchor,
        )

    print("\n".join(str(p) for p in selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
