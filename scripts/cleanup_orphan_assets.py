#!/usr/bin/env python3
"""孤儿资产清理：删除 DuckDB 中不属于对应策略 config.codes 的资产行。

背景（docs/archive/undated/run-backtest-data-gate.md）: 历史 get_market_data 用旧 codes
拉取的数据行残留在 price_data 中（如 blue_chip_momentum 名下 21 只资产
含 6 只残留、其中 5 只仅 1 行），会污染因子分数与权重。代码层已按
config.codes 过滤（config_runner），本脚本做一次性存量治理。

安全措施:
- 执行前备份 data.duckdb → data.duckdb.bak-orphan-cleanup-<ts>
- 只处理「有 config.yaml 且 data.codes 非空」的策略; 其余跳过
- 只删除「该策略名下、不在其 config codes」的行（strategy 分区隔离）

用法:
    python scripts/cleanup_orphan_assets.py --workspace /home/ll/Public/qn-research
    python scripts/cleanup_orphan_assets.py --workspace . --dry-run   # 只看统计
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _load_codes(workspace: Path, strategy: str) -> list[str] | None:
    """返回策略 config.yaml 的 data.codes; 无 config/无 codes 返回 None。"""
    cfg_path = workspace / "strategies" / strategy / "config.yaml"
    if not cfg_path.exists():
        return None
    try:
        import yaml

        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        codes = (cfg.get("data") or {}).get("codes") or []
        return [str(c) for c in codes] if codes else None
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️  读取 {cfg_path} 失败: {exc}（跳过该策略）")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("."),
                        help="workspace 根目录（含 data.duckdb 与 strategies/）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只统计不删除")
    args = parser.parse_args()

    ws = args.workspace.resolve()
    db_path = ws / "data.duckdb"
    if not db_path.exists():
        print(f"❌ 未找到 {db_path}")
        return 1

    try:
        import duckdb
    except ImportError:
        print("❌ duckdb 未安装: pip install duckdb")
        return 1

    strategies = sorted(
        p.name for p in (ws / "strategies").iterdir()
        if p.is_dir()
    ) if (ws / "strategies").exists() else []
    if not strategies:
        print("⚠️  strategies/ 目录为空或不存在")
        return 1

    print(f"workspace: {ws}")
    print(f"策略数: {len(strategies)}")

    # 备份
    if not args.dry_run:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = db_path.with_name(f"data.duckdb.bak-orphan-cleanup-{ts}")
        shutil.copy2(db_path, backup)
        print(f"✅ 备份: {backup}")

    conn = duckdb.connect(str(db_path))
    total_deleted = 0
    stats: list[tuple[str, int, int]] = []  # (strategy, deleted, remaining_assets)

    try:
        before = conn.execute(
            "SELECT strategy_name, COUNT(*) FROM price_data GROUP BY strategy_name"
        ).fetchall()
        before_map = {r[0]: int(r[1]) for r in before}

        for strategy in strategies:
            codes = _load_codes(ws, strategy)
            if codes is None:
                print(f"  - {strategy}: 无 config 或无 data.codes → 跳过")
                continue
            placeholders = ", ".join(["?" for _ in codes])
            # 该策略名下的资产全集
            existing = conn.execute(
                "SELECT DISTINCT asset_code FROM price_data WHERE strategy_name = ?",
                [strategy],
            ).fetchall()
            orphans = [r[0] for r in existing if r[0] not in codes]
            if not orphans:
                print(f"  - {strategy}: 无孤儿资产")
                continue
            if args.dry_run:
                n = conn.execute(
                    f"SELECT COUNT(*) FROM price_data WHERE strategy_name = ? "
                    f"AND asset_code NOT IN ({placeholders})",
                    [strategy] + codes,
                ).fetchone()[0]
                print(f"  - {strategy}: 将删除 {n} 行孤儿资产 {orphans}")
                total_deleted += int(n)
            else:
                n_before = conn.execute(
                    "SELECT COUNT(*) FROM price_data WHERE strategy_name = ?",
                    [strategy],
                ).fetchone()[0]
                conn.execute(
                    f"DELETE FROM price_data WHERE strategy_name = ? "
                    f"AND asset_code NOT IN ({placeholders})",
                    [strategy] + codes,
                )
                n_after = conn.execute(
                    "SELECT COUNT(*) FROM price_data WHERE strategy_name = ?",
                    [strategy],
                ).fetchone()[0]
                deleted_rows = n_before - n_after
                total_deleted += deleted_rows
                print(f"  - {strategy}: 删除孤儿资产 {orphans}（{deleted_rows} 行）")
            after = conn.execute(
                "SELECT COUNT(DISTINCT asset_code) FROM price_data WHERE strategy_name = ?",
                [strategy],
            ).fetchone()[0]
            stats.append((strategy, len(orphans), int(after)))

        if not args.dry_run:
            conn.execute("CHECKPOINT")

        print("-" * 50)
        print(f"合计删除孤儿行: {total_deleted}（{'dry-run' if args.dry_run else '已执行'}）")
        for s, deleted, remaining in stats:
            print(f"  {s}: 删除 {deleted} 只孤儿资产, 剩余 {remaining} 只")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
