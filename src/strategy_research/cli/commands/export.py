"""Extracted from cli.py — export command.

Contains:
- cmd_export
"""

from __future__ import annotations

from pathlib import Path


def cmd_export(args) -> int:
    """导出策略到多种平台格式。"""
    from strategy_research.apps.export import export_strategy

    workspace = Path(args.path).resolve()
    strategy_name = args.strategy
    strategy_path = workspace / "strategies" / strategy_name / "strategy.py"

    if not strategy_path.exists():
        print(f"策略文件不存在: {strategy_path}")
        return 1

    output_dir = Path(args.output) if args.output else workspace / "exports"
    formats = args.format

    results = export_strategy(strategy_path, output_dir, formats)

    print(f"=== 导出结果: {strategy_name} ===")
    for fmt, result in results.items():
        status = "✓" if result["status"] == "ok" else "✗"
        if result["status"] == "ok":
            print(f"  {status} {fmt}: {result['path']} ({result['lines']} 行)")
        else:
            print(f"  {status} {fmt}: {result['error']}")

    return 0
