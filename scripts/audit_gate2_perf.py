#!/usr/bin/env python
"""Audit gate ② item 4: performance baseline (GraphEngine + mailbox).

Why a script and not a test: this records *numbers*, not pass/fail. A
regression gate would need thresholds that nobody has justified yet; what
audit gate ② asks for is a reproducible measurement to compare against
later. Run it, record the output in the audit report.

    D:\\tools\\python312\\python.exe scripts\\audit_gate2_perf.py
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.orchestration import (  # noqa: E402
    EntryPoint,
    SourceKind,
    WorkKind,
    WorkRequest,
)
from strategy_research.core.runtimes import GraphEngine  # noqa: E402


def _graph(n_nodes: int) -> GraphEngine:
    engine = GraphEngine()
    for i in range(n_nodes):
        engine.add_node(f"n{i}", lambda s, i=i: {**s, "hops": s.get("hops", 0) + 1})
    return engine


def bench_graph(n_nodes: int = 5, runs: int = 200) -> dict:
    times = []
    for _ in range(runs):
        engine = _graph(n_nodes)
        t0 = time.perf_counter()
        state = engine.run({})
        times.append((time.perf_counter() - t0) * 1000)
        assert state["hops"] == n_nodes
    return {
        "nodes": n_nodes,
        "runs": runs,
        "median_ms": round(statistics.median(times), 3),
        "p95_ms": round(sorted(times)[int(runs * 0.95)], 3),
        "max_ms": round(max(times), 3),
    }


def bench_mailbox(n: int = 2000) -> dict:
    ep = EntryPoint()
    t0 = time.perf_counter()
    for i in range(n):
        ep.submit(WorkRequest(
            kind=WorkKind.CHAT,
            source=SourceKind.API_CHAT,
            session_key=f"chat:s{i % 50}",
            payload={"text": f"m{i}"},
        ))
    submit_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    drained = 0
    for _key, mailbox in ep.iter_mailboxes():
        while mailbox.poll_due(None) is not None:
            drained += 1
    drain_s = time.perf_counter() - t0

    return {
        "messages": n,
        "drained": drained,
        "submit_per_s": round(n / submit_s) if submit_s else 0,
        "drain_per_s": round(drained / drain_s) if drain_s else 0,
        "drain_ms": round(drain_s * 1000, 2),
    }


def main() -> int:
    print("=== GraphEngine single-run latency ===")
    for nodes in (1, 5, 10):
        print(f"  {bench_graph(nodes)}")

    print()
    print("=== mailbox throughput ===")
    print(f"  {bench_mailbox()}")

    print()
    print("Recorded for audit gate ② item 4. No thresholds are asserted:")
    print("the point is a reproducible number to compare a future run against.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
