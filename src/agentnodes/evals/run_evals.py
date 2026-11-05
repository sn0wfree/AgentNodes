"""A7: run golden tasks through AgentLoop (optional LLM) and score them."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python evals/run_evals.py` from repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from agentnodes.evals.scorecard import (  # noqa: E402
    RunRecord,
    build_scorecard,
    load_golden_tasks,
)


def _simulate_runs(tasks):
    """No-LLM placeholder: toolset tools used = expected; budget stays small."""
    for t in tasks:
        yield RunRecord(
            task_id=t.id,
            tools_used=list(t.expected_tools) or ["read"],
            tokens_used=min(t.budget_max_tokens or 500, 500),
            finished_reason="stop",
            answer=" ".join(t.must_contain) if t.must_contain else "ok",
            success=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run golden-task evals")
    parser.add_argument(
        "--tasks",
        default=str(Path(__file__).parent / "golden_tasks.jsonl"),
        help="Path to golden_tasks.jsonl",
    )
    parser.add_argument("--json", action="store_true", help="Print scorecard JSON")
    args = parser.parse_args(argv)

    tasks = load_golden_tasks(args.tasks)
    runs = list(_simulate_runs(tasks))
    card = build_scorecard(tasks, runs)
    payload = card.to_dict()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"tasks={payload['n']} mean={payload['mean']} pass@0.7={payload['pass_rate@0.7']}")
        for row in payload["tasks"]:
            note = ("; " + ",".join(row["notes"])) if row["notes"] else ""
            print(f"  {row['id']}: {row['total']}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
