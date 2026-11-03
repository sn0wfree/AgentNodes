"""数据导入工具。

支持从多种数据源导入价格数据到 DuckDB:
- CSV/Parquet (本地文件, long format with OHLCV columns)
- Tushare (A 股/ETF/指数/港股)
- iFinD (宏观/港美股)
- FRED (美国宏观 56 系列)
- AKShare (免费全市场)
- 示例数据 (合成)

历史说明 (2026-08-05):
  save_price_data / import_csv / import_parquet / import_dataframe 已删除。
  原实现接受 close-only 面板，合成假 open/high/low/volume 写入 DuckDB，
  污染下游依赖真实 OHLCV 的代码。新实现 (import_csv_ohlcv / import_parquet_ohlcv)
  要求长格式必须含完整 OHLCV 列。
"""

from __future__ import annotations

import logging

from datetime import timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from strategy_research.foundation.observability.logging_setup import dual_log
from strategy_research.apps.db import (
    compute_data_fingerprint,
    get_last_import_date,
    get_price_data_info,
    save_ohlcv_data,
    save_ohlcv_to_db,
    update_data_fingerprint,
    update_import_meta,
)

logger = logging.getLogger(__name__)

# ============================================================
# API 数据源导入
# ============================================================

def import_from_source(
    workspace_path: Path,
    strategy_name: str,
    source: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    incremental: bool = True,
) -> bool:
    """从指定数据源导入数据。

    Args:
        workspace_path: 工作区路径
        strategy_name: 策略名称
        source: 数据源名称 (tushare/ifind/fred/akshare/auto)
        codes: 资产代码列表
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        incremental: 是否增量更新

    Returns:
        bool: 是否成功
    """
    from strategy_research.apps.data_source import NoAvailableSourceError, detect_market, resolve_loader

    # 自动检测市场
    if source == "auto":
        # 按市场分组
        market_groups = {}
        for code in codes:
            market = detect_market(code)
            if market not in market_groups:
                market_groups[market] = []
            market_groups[market].append(code)

        # 按市场分别获取
        all_success = True
        for market, market_codes in market_groups.items():
            try:
                loader = resolve_loader(market)
                success = _import_with_loader(
                    workspace_path, strategy_name, loader,
                    market_codes, start_date, end_date, incremental
                )
                if not success:
                    all_success = False
            except NoAvailableSourceError as e:
                logger.warning(f"❌ 市场 '{market}' 没有可用数据源: {e}")
                all_success = False

        return all_success
    else:
        # 指定数据源
        try:
            loader = resolve_loader(detect_market(codes[0]) if codes else "a_share")
            # 如果指定了 source，尝试使用指定的
            from strategy_research.apps.data_source import get_loader_or_fallback
            loader_cls = get_loader_or_fallback(source)
            loader = loader_cls()
            return _import_with_loader(
                workspace_path, strategy_name, loader,
                codes, start_date, end_date, incremental
            )
        except NoAvailableSourceError as e:
            logger.warning(f"❌ 数据源 '{source}' 不可用: {e}")
            return False


def _import_with_loader(
    workspace_path: Path,
    strategy_name: str,
    loader,
    codes: list[str],
    start_date: str,
    end_date: str,
    incremental: bool,
) -> bool:
    """使用指定 loader 导入数据"""
    # 增量更新: 调整 start_date
    if incremental:
        actual_codes = []
        for code in codes:
            last = get_last_import_date(workspace_path, strategy_name, code)
            if last:
                # 从最后日期的下一天开始
                next_day = (last + timedelta(days=1)).strftime("%Y-%m-%d")
                if next_day > end_date:
                    dual_log("import", f"  ⏭  {code} 已是最新 (最后更新: {last})")
                    continue
                actual_codes.append((code, next_day))
            else:
                actual_codes.append((code, start_date))

        if not actual_codes:
            dual_log("import", "✓ 所有数据已是最新")
            return True

        codes_to_fetch = [c for c, _ in actual_codes]
        fetch_start = min(s for _, s in actual_codes)
    else:
        codes_to_fetch = codes
        fetch_start = start_date

    dual_log("import", f"📡 从 {loader.name} 获取 {len(codes_to_fetch)} 个资产...")

    # 获取数据
    data_map = loader.fetch(codes_to_fetch, fetch_start, end_date)

    if not data_map:
        logger.warning(f"❌ {loader.name} 未返回数据")
        return False

    # 保存到 DuckDB
    success_count = 0
    for code, df in data_map.items():
        if df is None or df.empty:
            continue

        # 保留完整 OHLCV (修复：之前 df[["close"]] 把 OHLCV 全丢)
        ok = save_ohlcv_data(workspace_path, strategy_name, code, df)
        if ok:
            success_count += 1
            # 更新导入元数据
            update_import_meta(workspace_path, strategy_name, code, end_date)

    dual_log("import", f"✓ 导入完成: {success_count}/{len(codes_to_fetch)} 个资产")

    # 显示信息
    info = get_price_data_info(workspace_path, strategy_name)
    if info:
        dual_log("import", f"   总计: {info.get('n_assets', 0)} 个资产, "
              f"{info.get('n_dates', 0)} 个日期")

    return success_count > 0


# ============================================================
# Tushare 专用导入
# ============================================================

def import_tushare(
    workspace_path: Path,
    strategy_name: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    token: Optional[str] = None,
    incremental: bool = True,
) -> bool:
    """从 Tushare 导入价格数据。"""
    from strategy_research.apps.data_source.tushare_loader import TushareLoader

    loader = TushareLoader(token=token, workspace_path=workspace_path)
    if not loader.is_available():
        logger.warning("❌ Tushare 不可用: 请设置 TUSHARE_TOKEN")
        return False

    return _import_with_loader(
        workspace_path, strategy_name, loader,
        codes, start_date, end_date, incremental
    )


# ============================================================
# iFinD 专用导入
# ============================================================

def import_ifind(
    workspace_path: Path,
    strategy_name: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    token: Optional[str] = None,
    incremental: bool = True,
) -> bool:
    """从 iFinD 导入数据。"""
    from strategy_research.apps.data_source.ifind_loader import IFindLoader

    loader = IFindLoader(token=token, workspace_path=workspace_path)
    if not loader.is_available():
        logger.warning("❌ iFinD 不可用: 请设置 IFIND_MCP_TOKEN")
        return False

    return _import_with_loader(
        workspace_path, strategy_name, loader,
        codes, start_date, end_date, incremental
    )


# ============================================================
# FRED 专用导入
# ============================================================

def import_fred(
    workspace_path: Path,
    strategy_name: str,
    series_ids: list[str],
    start_date: str,
    end_date: str,
    api_key: Optional[str] = None,
    incremental: bool = True,
) -> bool:
    """从 FRED 导入宏观数据。"""
    from strategy_research.apps.data_source.fred_loader import FredLoader

    loader = FredLoader(api_key=api_key, workspace_path=workspace_path)
    if not loader.is_available():
        logger.warning("❌ FRED 不可用: 请设置 FRED_API_KEY")
        return False

    return _import_with_loader(
        workspace_path, strategy_name, loader,
        series_ids, start_date, end_date, incremental
    )


# ============================================================
# AKShare 专用导入
# ============================================================

def import_akshare(
    workspace_path: Path,
    strategy_name: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    incremental: bool = True,
) -> bool:
    """从 AKShare 导入数据 (免费)。"""
    from strategy_research.apps.data_source.akshare_loader import AKShareLoader

    loader = AKShareLoader()
    if not loader.is_available():
        logger.warning("❌ AKShare 不可用: 请安装 akshare (pip install akshare)")
        return False

    return _import_with_loader(
        workspace_path, strategy_name, loader,
        codes, start_date, end_date, incremental
    )


# ============================================================
# 本地文件导入 (OHLCV 长格式)
# ============================================================
#
# 历史: import_csv / import_parquet / import_dataframe 接受 close-only 面板,
# 通过 save_price_data 合成假 OHLCV 写入。2026-08-05 删除。
# 替换为 import_csv_ohlcv / import_parquet_ohlcv, 要求长格式必须含
# 完整 OHLCV 列 (open, high, low, close, [volume])。

_REQUIRED_OHLCV = ("open", "high", "low", "close")
_OPTIONAL_OHLCV = ("volume",)


def _validate_long_ohlcv(df: pd.DataFrame, date_col: str, asset_col: str) -> None:
    """Validate that ``df`` is long format with required OHLCV columns.

    Raises:
        ValueError: missing required columns.
    """
    missing = [c for c in (date_col, asset_col, *_REQUIRED_OHLCV) if c not in df.columns]
    if missing:
        raise ValueError(
            f"missing required columns: {missing}. "
            f"Expected long format with [{date_col}, {asset_col}, open, high, low, close, "
            f"(volume)]; got {list(df.columns)}"
        )


def _long_to_ohlcv_map(
    df: pd.DataFrame, date_col: str, asset_col: str
) -> dict[str, pd.DataFrame]:
    """Convert validated long OHLCV DataFrame to ``{asset: wide(ohlcv)}`` dict.

    The per-asset DataFrame is sorted by ``date_col`` and indexed by it,
    with the available subset of ``[open, high, low, close, volume]`` columns.
    """
    has_volume = "volume" in df.columns
    ohlcv_cols = list(_REQUIRED_OHLCV) + (["volume"] if has_volume else [])

    data_map: dict[str, pd.DataFrame] = {}
    for asset, sub in df.groupby(asset_col, sort=False):
        sub = sub.drop_duplicates(subset=[date_col], keep="last")
        sub = sub.set_index(date_col)[ohlcv_cols].sort_index()
        sub.index = pd.to_datetime(sub.index)
        if has_volume:
            sub["volume"] = sub["volume"].astype(float)
        data_map[str(asset)] = sub
    return data_map


def import_csv_ohlcv(
    workspace_path: Path,
    strategy_name: str,
    csv_path: str | Path,
    date_column: str = "date",
    asset_column: str = "asset",
) -> bool:
    """从长格式 CSV 导入完整 OHLCV 数据。

    CSV 必须为长格式, 列: ``date, asset, open, high, low, close, [volume]``。
    缺任一必需列会抛 ``ValueError``; 不会合成假数据。

    Args:
        workspace_path: 工作区根路径
        strategy_name: 策略名称 (写入 price_data.strategy_name)
        csv_path: CSV 文件路径
        date_column: 日期列名 (默认 ``date``)
        asset_column: 资产代码列名 (默认 ``asset``)

    Returns:
        True on success.

    Raises:
        FileNotFoundError: csv_path 不存在
        ValueError: 缺必需 OHLCV 列
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 文件不存在: {csv_path}")

    df = pd.read_csv(csv_path, parse_dates=[date_column])
    _validate_long_ohlcv(df, date_col=date_column, asset_col=asset_column)
    data_map = _long_to_ohlcv_map(df, date_col=date_column, asset_col=asset_column)

    n_rows = save_ohlcv_to_db(workspace_path, data_map, strategy_name)

    fingerprint = compute_data_fingerprint(df)
    update_data_fingerprint(
        workspace_path, strategy_name, "price_data",
        fingerprint, len(df),
    )
    info = get_price_data_info(workspace_path, strategy_name)
    dual_log(
        "import",
        f"✓ 导入 CSV (OHLCV): {info.get('n_assets', 0)} 个资产, "
        f"{info.get('n_dates', 0)} 个日期, {n_rows} 行",
    )
    return True


def import_parquet_ohlcv(
    workspace_path: Path,
    strategy_name: str,
    parquet_path: str | Path,
    date_column: str = "date",
    asset_column: str = "asset",
) -> bool:
    """从长格式 Parquet 导入完整 OHLCV 数据。

    与 ``import_csv_ohlcv`` 同样要求长格式 OHLCV 列。Parquet 文件中日期
    列存储为 ``datetime64[ns]`` (推荐) 或 ISO 字符串。

    Raises:
        FileNotFoundError: parquet_path 不存在
        ValueError: 缺必需 OHLCV 列
    """
    parquet_path = Path(parquet_path)
    if not parquet_path.exists():
        raise FileNotFoundError(f"Parquet 文件不存在: {parquet_path}")

    df = pd.read_parquet(parquet_path)
    if date_column in df.columns and not pd.api.types.is_datetime64_any_dtype(
        df[date_column]
    ):
        df[date_column] = pd.to_datetime(df[date_column])
    _validate_long_ohlcv(df, date_col=date_column, asset_col=asset_column)
    data_map = _long_to_ohlcv_map(df, date_col=date_column, asset_col=asset_column)

    n_rows = save_ohlcv_to_db(workspace_path, data_map, strategy_name)

    fingerprint = compute_data_fingerprint(df)
    update_data_fingerprint(
        workspace_path, strategy_name, "price_data",
        fingerprint, len(df),
    )
    info = get_price_data_info(workspace_path, strategy_name)
    dual_log(
        "import",
        f"✓ 导入 Parquet (OHLCV): {info.get('n_assets', 0)} 个资产, "
        f"{info.get('n_dates', 0)} 个日期, {n_rows} 行",
    )
    return True


def generate_sample_data(
    n_assets: int = 10,
    n_days: int = 504,
    start_date: str = "2020-01-01",
) -> pd.DataFrame:
    """生成示例价格数据 (宽面板，仅 close 列)。

    Returns:
        pd.DataFrame: (T, N) 面板, index=date, columns=assets, 值为 close
    """
    import numpy as np

    dates = pd.date_range(start_date, periods=n_days, freq="D")
    assets = [f"asset_{i:03d}" for i in range(n_assets)]

    np.random.seed(42)
    returns = np.random.randn(n_days, n_assets) * 0.02
    prices = np.exp(np.cumsum(returns, axis=0))

    return pd.DataFrame(prices, index=dates, columns=assets)


def generate_sample_ohlcv_data(
    n_assets: int = 10,
    n_days: int = 504,
    start_date: str = "2020-01-01",
) -> dict[str, "pd.DataFrame"]:
    """生成示例 OHLCV 数据 (dict[code → DataFrame])，模拟 loader.fetch() 返回格式。

    保持真实 OHLC 不变量：high >= max(open, close) >= min(open, close) >= low。

    用于测试 save_ohlcv_data 与 OHLCV 完整性检查。
    """
    import numpy as np

    dates = pd.date_range(start_date, periods=n_days, freq="D")
    assets = [f"asset_{i:03d}" for i in range(n_assets)]

    np.random.seed(42)
    returns = np.random.randn(n_days, n_assets) * 0.02
    close = np.exp(np.cumsum(returns, axis=0))

    # open: close 上下 0.2% 噪声
    open_ = close * (1 + np.random.randn(n_days, n_assets) * 0.002)
    # high: 比 max(open, close) 还高 0~1%
    base_high = np.maximum(open_, close)
    high = base_high * (1 + np.abs(np.random.randn(n_days, n_assets)) * 0.005)
    # low: 比 min(open, close) 还低 0~1%
    base_low = np.minimum(open_, close)
    low = base_low * (1 - np.abs(np.random.randn(n_days, n_assets)) * 0.005)
    volume = np.random.randint(100_000, 10_000_000, size=(n_days, n_assets)).astype(float)

    result = {}
    for i, asset in enumerate(assets):
        df = pd.DataFrame({
            "open": open_[:, i],
            "high": high[:, i],
            "low": low[:, i],
            "close": close[:, i],
            "volume": volume[:, i],
        }, index=dates)
        df.index.name = "date"
        result[asset] = df

    return result
