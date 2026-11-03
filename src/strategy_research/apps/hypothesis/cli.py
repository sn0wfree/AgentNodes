"""CLI handlers for the hypothesis subsystem (P3-b).

Subcommands:
  create   — create a new hypothesis
  list     — list hypotheses (with optional status filter)
  show     — show a single hypothesis by id
  update   — update hypothesis fields (status, thesis, etc.)
  search   — token-overlap search across all fields
  link     — link a run_card or run_dir to a hypothesis
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .registry import (
    HYPOTHESIS_STATUSES,
    HypothesisRegistry,
    default_hypotheses_path,
)


def _registry(args: argparse.Namespace) -> HypothesisRegistry:
    path = Path(args.path).expanduser() if getattr(args, "path", None) else None
    return HypothesisRegistry(path=path)


def cmd_hypothesis_create(args: argparse.Namespace) -> int:
    r = _registry(args)
    try:
        hyp = r.create(
            title=args.title,
            thesis=args.thesis,
            status=args.status or "exploring",
            universe=args.universe or "",
            signal_definition=args.signal or "",
            data_sources=args.data_source or [],
            skills=args.skill or [],
            invalidation_notes=args.invalidation_notes or "",
        )
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    print(f"✓ Hypothesis created: {hyp.hypothesis_id}")
    print(f"  title:   {hyp.title}")
    print(f"  status:  {hyp.status}")
    print(f"  universe:{hyp.universe}")
    return 0


def cmd_hypothesis_list(args: argparse.Namespace) -> int:
    r = _registry(args)
    items = r.list()
    if args.status:
        try:
            items = [h for h in items if h.status == args.status]
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to filter by status: %s", e)
    if not items:
        print("no hypotheses found")
        return 0
    print(f"=== Hypotheses ({len(items)}) ===")
    for h in items:
        run_count = len(h.run_cards)
        print(f"  {h.hypothesis_id}  [{h.status:>11s}]  {h.title[:50]}  (runs={run_count})")
    return 0


def cmd_hypothesis_show(args: argparse.Namespace) -> int:
    r = _registry(args)
    h = r.get(args.hypothesis_id)
    if h is None:
        print(f"hypothesis not found: {args.hypothesis_id}", file=sys.stderr)
        return 1
    print(json.dumps(h.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_hypothesis_update(args: argparse.Namespace) -> int:
    r = _registry(args)
    try:
        h = r.update(
            args.hypothesis_id,
            status=args.status,
            thesis=args.thesis,
            invalidation_notes=args.invalidation_notes,
        )
    except (KeyError, ValueError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    print(f"✓ Hypothesis updated: {h.hypothesis_id}")
    print(f"  status:  {h.status}")
    return 0


def cmd_hypothesis_search(args: argparse.Namespace) -> int:
    r = _registry(args)
    try:
        results = r.search(query=args.query, status=args.status, limit=args.limit)
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    if not results:
        print(f"no matches for query={args.query!r}")
        return 0
    print(f"=== {len(results)} match(es) for query={args.query!r} ===")
    for h in results:
        print(f"  {h.hypothesis_id}  [{h.status:>11s}]  {h.title[:50]}")
        if h.thesis:
            preview = h.thesis[:80]
            print(f"      thesis: {preview}{'...' if len(h.thesis) > 80 else ''}")
    return 0


def cmd_hypothesis_link(args: argparse.Namespace) -> int:
    r = _registry(args)
    metrics: dict[str, Any] = {}
    if args.metric:
        for pair in args.metric:
            if "=" not in pair:
                print(f"✗ invalid --metric format: {pair!r} (expected key=value)", file=sys.stderr)
                return 1
            k, v = pair.split("=", 1)
            try:
                metrics[k] = float(v)
            except ValueError:
                metrics[k] = v
    try:
        h = r.link_backtest(
            args.hypothesis_id,
            run_card_path=args.run_card or "",
            backtest_run_dir=args.run_dir or "",
            metrics=metrics,
            notes=args.notes or "",
        )
    except (KeyError, ValueError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    print(f"✓ Linked run_card={args.run_card!r} run_dir={args.run_dir!r} → {h.hypothesis_id}")
    print(f"  total run_cards: {len(h.run_cards)}")
    return 0


def add_hypothesis_subparsers(subparsers: Any) -> None:
    """Attach the hypothesis subcommands to a parent parser."""
    parser = subparsers.add_parser("hypothesis", help="研究假设管理")
    sub = parser.add_subparsers(dest="hypothesis_command", help="hypothesis 子命令")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--path",
        default=None,
        help=f"hypotheses 存储文件路径 (默认 {default_hypotheses_path()})",
    )

    # create
    p = sub.add_parser("create", parents=[common], help="创建新假设")
    p.add_argument("--title", required=True, help="标题")
    p.add_argument("--thesis", required=True, help="研究论点")
    p.add_argument("--status", choices=HYPOTHESIS_STATUSES, default="exploring")
    p.add_argument("--universe", default="", help="目标市场/资产")
    p.add_argument("--signal", default="", help="信号定义")
    p.add_argument("--data-source", action="append", help="数据源 (可多次)")
    p.add_argument("--skill", action="append", help="相关 skill")
    p.add_argument("--invalidation-notes", default="", help="无效化条件")

    # list
    p = sub.add_parser("list", parents=[common], help="列出假设")
    p.add_argument("--status", choices=HYPOTHESIS_STATUSES, help="按状态过滤")

    # show
    p = sub.add_parser("show", parents=[common], help="显示单个假设")
    p.add_argument("hypothesis_id", help="hypothesis id")

    # update
    p = sub.add_parser("update", parents=[common], help="更新假设")
    p.add_argument("hypothesis_id", help="hypothesis id")
    p.add_argument("--status", choices=HYPOTHESIS_STATUSES)
    p.add_argument("--thesis", help="新论点")
    p.add_argument("--invalidation-notes", help="新无效化条件")

    # search
    p = sub.add_parser("search", parents=[common], help="搜索假设")
    p.add_argument("--query", default="", help="查询词")
    p.add_argument("--status", choices=HYPOTHESIS_STATUSES, help="按状态过滤")
    p.add_argument("--limit", type=int, default=10, help="最多结果数")

    # link
    p = sub.add_parser("link", parents=[common], help="链接回测结果")
    p.add_argument("hypothesis_id", help="hypothesis id")
    p.add_argument("--run-card", help="run_card.json 路径")
    p.add_argument("--run-dir", help="回测 run 目录路径")
    p.add_argument("--metric", action="append", help="metric key=value (可多次)")
    p.add_argument("--notes", default="", help="备注")
