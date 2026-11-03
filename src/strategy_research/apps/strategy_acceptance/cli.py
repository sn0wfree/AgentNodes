"""CLI handler for strategy acceptance debugging (P6 Step 0).

Subcommand:
    quantnodes-research accept --metrics-file <path> [--llm-verdict <json>]
        Print the keep/discard decision for a run's metrics.json.

This is a debug/replay tool, NOT a production gate. Use it to:
    - Inspect why a run was rejected
    - Compare hard-only vs hard+LLM verdicts
    - Tune thresholds before deploying
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import (
    decide,
    load_config,
)
from .llm_eval import LLMEvaluator


def cmd_accept(args: argparse.Namespace) -> int:
    """Print the keep/discard decision for a metrics file."""
    metrics_path = Path(args.metrics_file).expanduser().resolve()
    if not metrics_path.exists():
        print(f"✗ metrics file not found: {metrics_path}", file=sys.stderr)
        return 1
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"✗ could not parse metrics file: {exc}", file=sys.stderr)
        return 1

    cli_overrides: dict[str, Any] = {}
    for fld in ("hard_calmar_min", "hard_sharpe_min", "hard_max_dd_min",
                "hard_trades_min", "hard_ann_return_min", "llm_enabled",
                "llm_weight", "llm_score_threshold", "stagnation_patience"):
        val = getattr(args, fld, None)
        if val is not None:
            cli_overrides[fld] = val
    workspace_config = (
        Path(args.workspace_config).expanduser().resolve()
        if args.workspace_config else None
    )
    cfg = load_config(
        cli_overrides=cli_overrides or None,
        workspace_config=workspace_config,
    )

    llm_verdict: dict[str, Any] | None = None
    if args.llm_verdict:
        try:
            llm_verdict = json.loads(args.llm_verdict)
        except json.JSONDecodeError as exc:
            print(f"✗ --llm-verdict is not valid JSON: {exc}", file=sys.stderr)
            return 1

    stagnation_count = max(0, int(getattr(args, "stagnation_count", 0) or 0))

    decision = decide(
        metrics,
        llm_verdict=llm_verdict,
        cfg=cfg,
        stagnation_count=stagnation_count,
    )

    out = decision.to_dict()
    print(json.dumps(out, indent=2, ensure_ascii=False))

    if args.invoke_llm:
        try:
            from strategy_research.foundation.llm import LLMConfig, OpenAICompatClient
            llm_cfg = LLMConfig.load()
            client = OpenAICompatClient(llm_cfg)
        except Exception as exc:                                # noqa: BLE001
            print(f"✗ LLM client init failed: {exc}", file=sys.stderr)
            return 2
        try:
            llm_verdict_actual = LLMEvaluator(client=client).evaluate(metrics, cfg=cfg)
        except Exception as exc:                                # noqa: BLE001
            print(f"✗ LLM evaluate failed: {exc}", file=sys.stderr)
            return 2
        decision_llm = decide(metrics, llm_verdict=llm_verdict_actual, cfg=cfg,
                              stagnation_count=stagnation_count)
        print("\n# With live LLM verdict:")
        print(json.dumps(decision_llm.to_dict(), indent=2, ensure_ascii=False))

    return 0 if decision.accept else 3


def add_accept_subparsers(subparsers: Any) -> None:
    """Attach the ``accept`` subcommand to a parent parser."""
    parser = subparsers.add_parser(
        "accept",
        help="离线重放策略验收决策 (debug 工具)",
    )
    parser.add_argument(
        "--metrics-file", required=True,
        help="metrics.json 路径 (含 calmar/sharpe/max_dd/…)",
    )
    parser.add_argument(
        "--workspace-config",
        help="workspace 级 acceptance.yaml 路径 (优先级高于 user config)",
    )
    parser.add_argument(
        "--llm-verdict",
        help='预计算 LLM verdict JSON, 例如 \'{"passed": true, "score": 0.7, "reason": "…"}\'',
    )
    parser.add_argument(
        "--invoke-llm", action="store_true",
        help="额外调用 LLM 生成 verdict 并打印对比 (需 OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--stagnation-count", type=int, default=0,
        help="已连续 reject 的轮数 (用于触发 stagnation 强制 accept)",
    )
    # Threshold overrides
    parser.add_argument("--hard-calmar-min", type=float, help="覆盖 hard calmar_min")
    parser.add_argument("--hard-sharpe-min", type=float, help="覆盖 hard sharpe_min")
    parser.add_argument("--hard-max-dd-min", type=float, help="覆盖 hard max_dd_min")
    parser.add_argument("--hard-trades-min", type=int, help="覆盖 hard trades_min")
    parser.add_argument("--hard-ann-return-min", type=float, help="覆盖 hard ann_return_min")
    parser.add_argument("--llm-enabled", type=lambda s: s.lower() in ("1", "true", "yes"),
                        help="覆盖 llm_enabled (true/false)")
    parser.add_argument("--llm-score-threshold", type=float, help="覆盖 llm_score_threshold")
    parser.add_argument("--stagnation-patience", type=int, help="覆盖 stagnation_patience")
    parser.set_defaults(_handler=cmd_accept)
