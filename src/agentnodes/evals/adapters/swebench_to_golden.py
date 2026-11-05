"""Convert public benchmark rows → golden_tasks.jsonl (W: eval sources).

Usage (when `datasets` is installed and network is available)::

    python evals/adapters/swebench_to_golden.py \\
        --dataset princeton-nlp/SWE-bench_Verified \\
        --limit 20 --out evals/golden_from_swebench.jsonl

Each row becomes a golden task that keeps the issue constraints but
retargets expected tools to this project's toolset names.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# Map abstract agent needs → our tool names.
TOOL_HINT = ["read", "list_files", "git_diff"]


def convert_issue(instance_id: str, problem: str, *, max_chars: int = 800) -> dict[str, Any]:
    """Rewrite a SWE-bench-like issue into our golden schema."""
    prompt = problem.strip()[:max_chars]
    return {
        "id": f"swebench-{instance_id}".replace("/", "_")[:80],
        "prompt": (
            "阅读工作区代码/说明，定位并解释下列问题的修复思路；"
            "必要时用 read/git_diff 查看；不要写入文件。\n\n"
            f"{prompt}"
        ),
        "expected_tools": list(TOOL_HINT),
        "forbidden_tools": ["write", "run_bg_command"],
        "toolset": "minimal",
        "must_contain": ["修复|原因|fix|bug|error"],
        "tags": ["source:swebench", "reason"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="princeton-nlp/SWE-bench_Verified")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--out", default="evals/golden_from_swebench.jsonl")
    args = ap.parse_args()

    try:
        from datasets import load_dataset  # type: ignore
    except ImportError:
        print("datasets not installed; pip install datasets to convert at runtime")
        return 2

    ds = load_dataset(args.dataset, split="test")
    out = Path(args.out)
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for row in ds:
            if n >= args.limit:
                break
            inst = str(row.get("instance_id") or f"n{n}")
            text = str(row.get("problem_statement") or row.get("issue") or "")
            if not text:
                continue
            f.write(json.dumps(convert_issue(inst, text), ensure_ascii=False) + "\n")
            n += 1
    print(f"wrote {n} tasks -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
