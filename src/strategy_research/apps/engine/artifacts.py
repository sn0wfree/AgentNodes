"""Artifacts — 写回测产物到 run_dir (OHLCV/equity/trades CSV)。

包含 schema 定义和校验，确保产物完整性。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .models import TradeRecord

logger = logging.getLogger(__name__)


# ============================================================
# Artifact Schema 定义
# ============================================================

ARTIFACTS_SPEC: dict[str, dict[str, Any]] = {
    "equity_curve.csv": {
        "required": True,
        "columns": ["capital", "unrealized", "equity", "positions"],
        "min_rows": 1,
    },
    "trades.csv": {
        "required": True,
        "columns": [
            "symbol", "direction", "entry_price", "exit_price",
            "entry_time", "exit_time", "size", "leverage",
            "pnl", "pnl_pct", "exit_reason", "holding_bars", "commission",
        ],
        "min_rows": 0,  # 0 trades is valid
    },
    "metrics.json": {
        "required": True,
        "fields": ["final_value", "total_return", "sharpe", "max_dd"],
        "min_rows": 1,
    },
}


def validate_artifacts(run_dir: Path) -> list[str]:
    """Validate backtest artifacts against schema.

    Returns a list of warning strings. Empty list = all OK.
    """
    warnings: list[str] = []

    for filename, spec in ARTIFACTS_SPEC.items():
        filepath = run_dir / filename
        if not filepath.exists():
            if spec.get("required", False):
                warnings.append(f"missing_required: {filename}")
            continue

        try:
            if filename.endswith(".csv"):
                _validate_csv(filepath, spec, filename, warnings)
            elif filename.endswith(".json"):
                _validate_json(filepath, spec, filename, warnings)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{filename}: validation error: {exc}")

    return warnings


def _validate_csv(
    filepath: Path,
    spec: dict,
    filename: str,
    warnings: list[str],
) -> None:
    """Validate a single CSV artifact against its spec."""
    df = pd.read_csv(filepath)
    expected_cols = spec.get("columns", [])
    missing_cols = [c for c in expected_cols if c not in df.columns]
    if missing_cols:
        warnings.append(f"{filename}: missing columns {missing_cols}")
    min_rows = spec.get("min_rows", 0)
    if len(df) < min_rows:
        warnings.append(f"{filename}: {len(df)} rows < min_rows {min_rows}")
    # Check for inf values in numeric columns
    for col in df.select_dtypes(include=["float64", "float32"]).columns:
        if df[col].isin([float("inf"), float("-inf")]).any():
            warnings.append(f"{filename}: inf values in column '{col}'")


def _validate_json(
    filepath: Path,
    spec: dict,
    filename: str,
    warnings: list[str],
) -> None:
    """Validate a single JSON artifact against its spec."""
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    expected_fields = spec.get("fields", [])
    missing = [k for k in expected_fields if k not in data]
    if missing:
        warnings.append(f"{filename}: missing fields {missing}")
    # Check for NaN/Inf in metric values
    for k, v in data.items():
        if isinstance(v, float):
            if v != v:  # NaN
                warnings.append(f"{filename}: NaN in field '{k}'")
            elif v == float("inf") or v == float("-inf"):
                warnings.append(f"{filename}: inf in field '{k}'")


def write_equity_curve(
    run_dir: Path,
    equity_snapshots: list,
) -> None:
    """写 equity_curve.csv。"""
    if not equity_snapshots:
        return

    data = {
        "timestamp": [s.timestamp for s in equity_snapshots],
        "capital": [s.capital for s in equity_snapshots],
        "unrealized": [s.unrealized for s in equity_snapshots],
        "equity": [s.equity for s in equity_snapshots],
        "positions": [s.positions for s in equity_snapshots],
    }
    df = pd.DataFrame(data).set_index("timestamp")
    df.to_csv(run_dir / "equity_curve.csv", header=True)


def write_trades_csv(
    run_dir: Path,
    trades: List[TradeRecord],
) -> None:
    """写 trades.csv。"""
    if not trades:
        return

    data = []
    for t in trades:
        data.append({
            "symbol": t.symbol,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "size": t.size,
            "leverage": t.leverage,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "exit_reason": t.exit_reason,
            "holding_bars": t.holding_bars,
            "commission": t.commission,
        })
    df = pd.DataFrame(data)
    df.to_csv(run_dir / "trades.csv", index=False)


def write_ohlcv_snapshots(
    run_dir: Path,
    data_map: Dict[str, pd.DataFrame],
    codes: List[str],
) -> None:
    """写每个标的的 OHLCV 到子目录。"""
    ohlcv_dir = run_dir / "ohlcv"
    ohlcv_dir.mkdir(exist_ok=True)
    for code in codes:
        if code in data_map:
            df = data_map[code]
            safe_name = code.replace("/", "_").replace(".", "_")
            df.to_csv(ohlcv_dir / f"{safe_name}.csv", header=True)


def write_metrics_json(
    run_dir: Path,
    metrics: Dict[str, Any],
) -> None:
    """写 metrics.json。"""
    # JSON-safe: convert NaN/Inf to None
    def _json_safe(obj):
        if isinstance(obj, float):
            if obj != obj:  # NaN
                return None
            if obj == float("inf") or obj == float("-inf"):
                return None
        return obj

    safe_metrics = {k: _json_safe(v) for k, v in metrics.items()}
    with open(run_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(safe_metrics, f, indent=2, ensure_ascii=False, default=str)


def write_all_artifacts(
    run_dir: Path,
    engine,
    data_map: Dict[str, pd.DataFrame],
    codes: List[str],
    metrics: Dict[str, Any],
) -> list[str]:
    """写所有回测产物并校验 schema。

    Returns:
        List of warning strings. Empty = all OK.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    write_equity_curve(run_dir, engine.equity_snapshots)
    write_trades_csv(run_dir, engine.trades)
    write_ohlcv_snapshots(run_dir, data_map, codes)
    write_metrics_json(run_dir, metrics)

    warnings = validate_artifacts(run_dir)
    if warnings:
        for w in warnings:
            logger.warning("artifact validation: %s", w)

    return warnings


__all__ = [
    "ARTIFACTS_SPEC",
    "validate_artifacts",
    "write_equity_curve",
    "write_trades_csv",
    "write_ohlcv_snapshots",
    "write_metrics_json",
    "write_all_artifacts",
]
