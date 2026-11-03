"""Data shape transformations: long ↔ wide, multi-asset ↔ single-asset.

Tool set #2 (after ``data_clean``).

# TODO(toolsets): expose high-frequency helpers as LLM tools
#   (LongToWideTool / WideToLongTool) under
#   ``core/agent/builtin_tools/data/`` when agent use-cases emerge.

Conventions
-----------
- ``LONG_OHLCV`` : ``DataFrame`` with columns
  ``[date, asset, open, high, low, close, volume]`` (subset allowed).
- ``LONG_CLOSE`` : ``DataFrame`` with columns ``[date, asset, close]``.
- ``WIDE_OHLCV`` : ``dict[str, DataFrame]`` mapping
  ``{asset_code: DataFrame(date_index, [open, high, low, close, volume])}``.
- ``WIDE_CLOSE`` : ``DataFrame`` with ``date_index`` and asset-code columns
  holding close values only.
- ``WIDE_FACTOR``: same shape as ``WIDE_CLOSE`` but values are factor scores.

All helpers raise ``ValueError`` on shape mismatch (fail-fast).  No silent
fallback, no empty defaults.
"""
from __future__ import annotations

import re
from typing import Literal, cast

import pandas as pd

ValueColsArg = list[str] | Literal["ohlcv", "close"]


# ============================================================
# Detection helpers (private)
# ============================================================

_OHLCV_COLS = ("open", "high", "low", "close", "volume")
_CLOSE_COLS = ("close",)
_ASSET_CODE_RE = re.compile(r"^\d{6}\.[A-Z]{2}$")


def _is_long_format(df: pd.DataFrame) -> bool:
    return {"date", "asset"}.issubset(df.columns)


def _is_ohlcv_columns(df: pd.DataFrame) -> bool:
    cols = set(df.columns)
    return {"open", "high", "low", "close"}.issubset(cols)


# ============================================================
# LONG → WIDE
# ============================================================


def long_to_single_asset_wide(
    df: pd.DataFrame,
    asset: str,
    value_cols: ValueColsArg = "ohlcv",
    date_col: str = "date",
    asset_col: str = "asset",
) -> pd.DataFrame:
    """Extract one asset from long format and return wide (T, N) DataFrame.

    Args:
        df: long format DataFrame containing ``date_col`` and ``asset_col``.
        asset: asset code to extract (must appear in ``asset_col``).
        value_cols: which value columns to keep.  Either
            ``"ohlcv"`` (open, high, low, close, volume) or ``"close"`` or
            an explicit list of column names.  Missing columns are skipped
            (only those that exist in ``df`` are returned).
        date_col: name of the date column.
        asset_col: name of the asset column.

    Returns:
        ``DataFrame`` indexed by ``date_col`` with the requested value
        columns.  Duplicates on date are dropped (keep last).

    Raises:
        ValueError: if ``df`` is not long format or ``asset`` is missing.
    """
    if date_col not in df.columns or asset_col not in df.columns:
        raise ValueError(
            f"long format requires columns [{date_col}, {asset_col}, ...]; "
            f"got {list(df.columns)}"
        )
    if asset not in df[asset_col].unique():
        available = sorted(df[asset_col].unique().tolist())[:10]
        raise ValueError(
            f"asset {asset!r} not found in data; available (first 10): {available}"
        )

    if value_cols == "ohlcv":
        wanted = list(_OHLCV_COLS)
    elif value_cols == "close":
        wanted = list(_CLOSE_COLS)
    else:
        wanted = list(value_cols)

    present = [c for c in wanted if c in df.columns]
    if not present:
        raise ValueError(
            f"none of requested value_cols {wanted} found in df columns {list(df.columns)}"
        )

    sub = df[df[asset_col] == asset].copy()
    sub = sub.drop_duplicates(subset=[date_col], keep="last")
    sub = sub.set_index(date_col)[present]
    sub = sub.sort_index()
    return cast(pd.DataFrame, sub)


def long_to_wide_close(
    df: pd.DataFrame,
    date_col: str = "date",
    asset_col: str = "asset",
    value_col: str = "close",
) -> pd.DataFrame:
    """Pivot long DataFrame to wide (T, N) panel with one column per asset.

    Returns:
        ``DataFrame`` indexed by ``date_col``, columns = unique asset codes,
        values = ``value_col``.  Equivalent to ``df.pivot(...)``.

    Raises:
        ValueError: if ``df`` is not long format or required columns missing.
    """
    if date_col not in df.columns or asset_col not in df.columns:
        raise ValueError(
            f"long format requires columns [{date_col}, {asset_col}, ...]; "
            f"got {list(df.columns)}"
        )
    if value_col not in df.columns:
        raise ValueError(
            f"value_col {value_col!r} not in df columns {list(df.columns)}"
        )
    panel = df.pivot(index=date_col, columns=asset_col, values=value_col)
    panel.index = pd.to_datetime(panel.index)
    return panel


def long_to_wide_ohlcv_per_asset(
    df: pd.DataFrame,
    date_col: str = "date",
    asset_col: str = "asset",
) -> dict[str, pd.DataFrame]:
    """Convert long OHLCV DataFrame to ``{asset: wide(T, [ohlcv])}`` dict.

    Each per-asset DataFrame has a ``date_index`` and the subset of
    ``[open, high, low, close, volume]`` columns present in ``df``.

    Raises:
        ValueError: if ``df`` is not long format.
    """
    if date_col not in df.columns or asset_col not in df.columns:
        raise ValueError(
            f"long format requires columns [{date_col}, {asset_col}, ...]; "
            f"got {list(df.columns)}"
        )

    ohlcv_present = [c for c in _OHLCV_COLS if c in df.columns]
    if not ohlcv_present:
        raise ValueError(
            f"no ohlcv columns found in df (need at least one of "
            f"{list(_OHLCV_COLS)}); got {list(df.columns)}"
        )

    out: dict[str, pd.DataFrame] = {}
    for asset, sub in df.groupby(asset_col, sort=False):
        sub = sub.drop_duplicates(subset=[date_col], keep="last")
        sub = sub.set_index(date_col)[ohlcv_present]
        sub = sub.sort_index()
        sub.index = pd.to_datetime(sub.index)
        out[str(asset)] = sub
    return out


# ============================================================
# WIDE → LONG
# ============================================================


def _unique_date_col_name(panel: pd.DataFrame) -> str:
    """Return a column name to use for the date axis after ``reset_index()``.

    Follows the pandas convention: if the index has a name, that name is
    used as the new column.  Otherwise the column is named ``'index'`` —
    and if that already collides with an existing column, pandas appends
    a numeric suffix (``index_1``, ``index_2`` …) until the name is unique.
    """
    base = panel.index.name if panel.index.name is not None else "index"
    candidate = base
    suffix = 1
    while candidate in panel.columns:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return cast(str, candidate)


def wide_close_to_long(panel: pd.DataFrame) -> pd.DataFrame:
    """Melt wide (T, N) close-only panel to long [date, asset, close].

    Args:
        panel: DataFrame with date index and asset-code columns.

    Returns:
        ``DataFrame`` with columns ``[date, asset, close]``.

    Raises:
        ValueError: if ``panel`` index has no name and looks like already-long
            (i.e. columns include 'date' or 'asset').
    """
    if not isinstance(panel, pd.DataFrame):
        raise ValueError(f"expected DataFrame, got {type(panel).__name__}")
    if {"date", "asset"}.issubset(panel.columns):
        raise ValueError(
            "input already looks like long format (has 'date' and 'asset' columns); "
            "wide_close_to_long expects a wide panel with date as index"
        )

    date_col = _unique_date_col_name(panel)
    reset = panel.reset_index()
    # reset_index may have produced a different name than `date_col` if
    # pandas internally disambiguated.  Re-normalise to the exact name we
    # computed.
    actual_col = next(c for c in reset.columns if c not in panel.columns)
    if actual_col != date_col:
        reset = reset.rename(columns={actual_col: date_col})
    out = reset.melt(id_vars=date_col, var_name="asset", value_name="close")
    out = out.rename(columns={date_col: "date"})
    return cast(pd.DataFrame, out[["date", "asset", "close"]])


def wide_factor_to_long(panel: pd.DataFrame, factor_name: str) -> pd.DataFrame:
    """Melt wide factor panel to long [date, asset, <factor_name>].

    Same as ``wide_close_to_long`` but the value column is named after the
    factor instead of ``close``.
    """
    if not isinstance(panel, pd.DataFrame):
        raise ValueError(f"expected DataFrame, got {type(panel).__name__}")
    if not factor_name:
        raise ValueError("factor_name must be a non-empty string")

    if "date" in panel.columns and "asset" in panel.columns:
        raise ValueError(
            "input already looks like long format (has 'date' and 'asset' columns); "
            "wide_factor_to_long expects a wide panel with date as index"
        )

    date_col = _unique_date_col_name(panel)
    reset = panel.reset_index()
    actual_col = next(c for c in reset.columns if c not in panel.columns)
    if actual_col != date_col:
        reset = reset.rename(columns={actual_col: date_col})
    out = reset.melt(id_vars=date_col, var_name="asset", value_name=factor_name)
    out = out.rename(columns={date_col: "date"})
    return cast(pd.DataFrame, out[["date", "asset", factor_name]])


def wide_to_long_ohlcv(panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Inverse of ``long_to_wide_ohlcv_per_asset``.

    Each value DataFrame is expected to be wide (T, [ohlcv]) with date index.
    Concatenates them into a single long DataFrame with columns
    ``[date, asset, open, high, low, close, volume]`` (only columns present
    in any panel are kept).
    """
    if not panels:
        raise ValueError("panels dict is empty")
    if not isinstance(panels, dict):
        raise ValueError(f"expected dict, got {type(panels).__name__}")

    pieces = []
    for asset, sub in panels.items():
        if not isinstance(sub, pd.DataFrame):
            raise ValueError(
                f"panel for asset {asset!r} is not a DataFrame: {type(sub).__name__}"
            )
        if sub.empty:
            continue
        df = sub.reset_index()
        index_col = sub.index.name or "date"
        if index_col != "date":
            df = df.rename(columns={index_col: "date"})
        df["asset"] = asset
        pieces.append(df)

    if not pieces:
        raise ValueError("all panels are empty; nothing to concatenate")
    out = pd.concat(pieces, ignore_index=True, sort=False)
    out = out[["date", "asset"] + [c for c in out.columns if c not in ("date", "asset")]]
    return cast(pd.DataFrame, out)


# ============================================================
# Format detection
# ============================================================


def is_wide_close_format(df: pd.DataFrame) -> bool:
    """Heuristic: True if ``df`` looks like a multi-asset wide close panel.

    A wide close panel has a date-like index (or DatetimeIndex) and asset
    codes as columns.  We detect asset codes via the ``600001.SH`` /
    ``000001.SZ`` regex.

    Used by callers that want to refuse wrong-shape input (e.g. factor DSL
    that expects per-asset wide ohlcv).
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return False
    cols = list(df.columns)
    if not cols:
        return False
    # Has explicit 'close' / 'open' columns → not wide close format
    if _is_ohlcv_columns(df):
        return False
    if "close" in cols:
        return False
    # Count asset-code-shaped columns
    code_like = sum(1 for c in cols if isinstance(c, str) and _ASSET_CODE_RE.match(c))
    return code_like >= max(1, len(cols) // 2)


__all__ = [
    "long_to_single_asset_wide",
    "long_to_wide_close",
    "long_to_wide_ohlcv_per_asset",
    "wide_close_to_long",
    "wide_factor_to_long",
    "wide_to_long_ohlcv",
    "is_wide_close_format",
]
