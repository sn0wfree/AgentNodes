"""DuckDB 工具函数。"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# Mutex for concurrent DuckDB writes (parallel agent fan-out safety)
_DUCKDB_WRITE_LOCK = threading.Lock()

# ============================================================
# 连接管理
# ============================================================

def get_db_path(workspace_path: Path) -> Path:
    """获取 DuckDB 文件路径。"""
    return workspace_path / "data.duckdb"


def get_connection(workspace_path: Path, read_only: bool = False):
    """获取 DuckDB 连接；不可用时返回 None。

    Note: DuckDB is a single-writer database — concurrent processes
    writing the same workspace DB can raise "Could not set lock".
    Callers should serialize writes (or use read_only=True for reads).

    read_only=True on a workspace whose DB has never been created is a
    normal situation (fresh workspace, or a strategy that never imported
    data). DuckDB raises ``IOException: database does not exist`` there,
    but every caller treats ``None`` as "no data" — so we convert that
    specific case to None instead of letting it crash. Regression: the
    data-readiness gate promises C0 "database unavailable -> fail check"
    but used to raise instead, taking the whole pre-flight down.
    """
    try:
        import duckdb
    except ImportError:
        print("⚠️  duckdb 未安装。安装: pip install duckdb")
        return None

    db_path = get_db_path(workspace_path)
    if read_only and not db_path.exists():
        return None
    try:
        return duckdb.connect(str(db_path), read_only=read_only)
    except Exception as exc:  # noqa: BLE001
        if read_only:
            # Any read-only failure means "no usable database here"; the
            # callers' contract is an empty result, not an exception.
            print(f"⚠️  无法只读打开 {db_path.name}: {exc}")
            return None
        raise


# ============================================================
# 初始化 SQL
# ============================================================

DUCKDB_INIT_SQL = """
-- 价格数据 (统一存储)
CREATE TABLE IF NOT EXISTS price_data (
    strategy_name VARCHAR NOT NULL,
    asset_code VARCHAR NOT NULL,
    date DATE NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    PRIMARY KEY (strategy_name, asset_code, date)
);

-- ohlcv 视图: 映射 price_data 到因子分析工具期望的格式
CREATE VIEW IF NOT EXISTS ohlcv AS
SELECT
    date,
    asset_code AS asset,
    open,
    high,
    low,
    close,
    volume
FROM price_data;

-- 因子数据 (计算结果缓存)
CREATE TABLE IF NOT EXISTS factor_data (
    strategy_name VARCHAR NOT NULL,
    factor_name VARCHAR NOT NULL,
    date DATE NOT NULL,
    asset_code VARCHAR NOT NULL,
    factor_value DOUBLE,
    PRIMARY KEY (strategy_name, factor_name, date, asset_code)
);

-- 因子注册表
CREATE TABLE IF NOT EXISTS factor_registry (
    factor_name VARCHAR NOT NULL,
    factor_code VARCHAR NOT NULL,
    factor_type VARCHAR NOT NULL,
    category VARCHAR,
    source VARCHAR,
    lookback_window INTEGER,
    strategy_name VARCHAR NOT NULL,
    operator_count INTEGER,
    param_count INTEGER,
    feature_count INTEGER,
    complexity_score DOUBLE,
    info_channel VARCHAR,
    parent_hypothesis_id VARCHAR,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    added_by VARCHAR,
    PRIMARY KEY (strategy_name, factor_name)
);

-- 验证缓存
CREATE TABLE IF NOT EXISTS validation_cache (
    factor_name VARCHAR NOT NULL,
    factor_code VARCHAR NOT NULL,
    strategy_name VARCHAR NOT NULL,
    ic_mean DOUBLE,
    ic_std DOUBLE,
    ir DOUBLE,
    ic_decay_1d DOUBLE,
    ic_decay_5d DOUBLE,
    ic_decay_20d DOUBLE,
    rank_ic_mean DOUBLE,
    stability_score DOUBLE,
    diversification_score DOUBLE,
    turnover_score DOUBLE,
    monotonicity_score DOUBLE,
    coverage_score DOUBLE,
    overall_score DOUBLE,
    is_valid BOOLEAN,
    fail_reasons VARCHAR,
    validated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source VARCHAR,
    data_fingerprint VARCHAR,
    PRIMARY KEY (strategy_name, factor_name)
);

-- 回测结果
CREATE TABLE IF NOT EXISTS backtest_results (
    strategy_name VARCHAR NOT NULL,
    run VARCHAR NOT NULL,
    commit_hash VARCHAR,
    action VARCHAR,
    goal_metric DOUBLE,
    calmar DOUBLE,
    sharpe DOUBLE,
    max_dd DOUBLE,
    ann_return DOUBLE,
    ann_vol DOUBLE,
    sortino DOUBLE,
    turnover DOUBLE,
    factors_added INTEGER DEFAULT 0,
    factors_removed INTEGER DEFAULT 0,
    params_changed INTEGER DEFAULT 0,
    status VARCHAR,
    description VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (strategy_name, run)
);

-- 权重历史
CREATE TABLE IF NOT EXISTS weight_history (
    strategy_name VARCHAR NOT NULL,
    run VARCHAR NOT NULL,
    date DATE NOT NULL,
    asset_code VARCHAR NOT NULL,
    weight DOUBLE,
    PRIMARY KEY (strategy_name, run, date, asset_code)
);

-- NAV 历史
CREATE TABLE IF NOT EXISTS nav_history (
    strategy_name VARCHAR NOT NULL,
    run VARCHAR NOT NULL,
    date DATE NOT NULL,
    nav DOUBLE,
    PRIMARY KEY (strategy_name, run, date)
);

-- 数据指纹
CREATE TABLE IF NOT EXISTS data_fingerprint (
    table_name VARCHAR NOT NULL,
    strategy_name VARCHAR NOT NULL,
    fingerprint VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_count INTEGER,
    PRIMARY KEY (table_name, strategy_name)
);

-- 导入元数据 (增量更新追踪)
CREATE TABLE IF NOT EXISTS import_meta (
    strategy_name VARCHAR NOT NULL,
    asset_code VARCHAR NOT NULL,
    last_date DATE,
    last_source VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (strategy_name, asset_code)
);
"""


def init_db(workspace_path: Path) -> bool:
    """初始化 DuckDB 表结构。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        # Execute table creation first
        conn.execute(DUCKDB_INIT_SQL)
        _ensure_factor_registry_columns(conn)
        conn.close()
        return True
    except Exception as e:
        # Handle view creation conflicts gracefully
        if "write-write conflict" in str(e) or "already exists" in str(e):
            try:
                conn.close()
            except Exception:
                pass
            return True  # View already exists, that's fine
        print(f"❌ DuckDB 初始化失败: {e}")
        try:
            conn.close()
        except Exception:
            pass
        return False


def save_ohlcv_to_db(
    workspace_path: Path,
    data_map: dict[str, pd.DataFrame],
    strategy_name: str = "default",
) -> int:
    """Save OHLCV data from online sources into DuckDB.

    Thread-safe: uses a module-level mutex to prevent concurrent DuckDB
    writes from parallel agent fan-out (data_quality + factor_analyst).

    Reusable by config_runner, import_data tool, and CLI.

    Args:
        workspace_path: Workspace root path.
        data_map: Dict of {code: DataFrame} with OHLCV columns.
        strategy_name: Strategy name for data partitioning.

    Returns:
        Total rows saved.
    """
    with _DUCKDB_WRITE_LOCK:
        init_db(workspace_path)
        conn = get_connection(workspace_path)
        if conn is None:
            raise RuntimeError("failed to open DuckDB")

        total_rows = 0
        for code, df in data_map.items():
            if df is None or df.empty:
                continue

            # Move date-like index to column so normalization can find it.
            # Loaders (tencent, eastmoney, akshare, etc.) return DataFrames with
            # trade_date as the index, not as a regular column.
            idx_name = df.index.name
            if idx_name and idx_name.lower() in ("trade_date", "tradedate", "datetime", "date"):
                df = df.reset_index()

            # Normalize column names
            col_map = {}
            for col in df.columns:
                cl = col.lower()
                if cl in ("trade_date", "tradedate", "datetime"):
                    col_map[col] = "date"
                elif cl in ("code", "symbol", "ticker", "asset"):
                    col_map[col] = "asset_code"
                elif cl in ("open", "high", "low", "close", "volume"):
                    # OHLCV 列名统一小写——否则 "Close" 逃过归一化后，
                    # 下面的缺失列补齐会拿小写 "close" 取值而 KeyError
                    col_map[col] = cl
            if col_map:
                df = df.rename(columns=col_map)

            if "asset_code" not in df.columns:
                df["asset_code"] = code
            if "date" not in df.columns:
                continue

            for c in ("open", "high", "low", "close", "volume"):
                if c not in df.columns:
                    df[c] = df["close"] if c != "volume" else 0.0

            df["strategy_name"] = strategy_name
            df = df[["strategy_name", "asset_code", "date", "open", "high", "low", "close", "volume"]]

            conn.execute("""
                INSERT OR REPLACE INTO price_data
                (strategy_name, asset_code, date, open, high, low, close, volume)
                SELECT strategy_name, asset_code, date, open, high, low, close, volume
                FROM df
            """)
            total_rows += len(df)

        conn.close()
        return total_rows


# ============================================================
# 价格数据操作
# ============================================================

def load_price_data(
    workspace_path: Path,
    strategy_name: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """从 DuckDB 加载价格数据。

    Returns:
        pd.DataFrame: (T, N) 价格面板, index=date, columns=assets
    """
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return pd.DataFrame()

    try:
        query = """
            SELECT date, asset_code, close
            FROM price_data
            WHERE strategy_name = ?
        """
        params = [strategy_name]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date, asset_code"

        df = conn.execute(query, params).fetchdf()
        conn.close()

        if df.empty:
            return pd.DataFrame()

        from .tools.data_transforms import long_to_wide_close

        # pivot to panel.  DuckDB schema uses ``asset_code`` as the asset
        # column name (vs. our helper's default ``asset``), so we pass it
        # explicitly.
        panel = long_to_wide_close(
            df, asset_col="asset_code", value_col="close"
        )
        return panel

    except Exception as e:
        print(f"❌ 加载价格数据失败: {e}")
        conn.close()
        return pd.DataFrame()


def load_ohlcv_data(
    workspace_path: Path,
    strategy_name: str,
    codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, pd.DataFrame]:
    """从 DuckDB 加载完整 OHLCV 数据（bar-by-bar engine 用）。

    Returns:
        dict[str, pd.DataFrame]: {asset_code: DataFrame}，每个 DataFrame
        index=DatetimeIndex，columns=[open, high, low, close, volume]
    """
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return {}

    try:
        query = """
            SELECT date, asset_code, open, high, low, close, volume
            FROM price_data
            WHERE strategy_name = ?
        """
        params: list = [strategy_name]

        if codes:
            placeholders = ", ".join(["?" for _ in codes])
            query += f" AND asset_code IN ({placeholders})"
            params.extend(codes)

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY asset_code, date"

        df = conn.execute(query, params).fetchdf()
        conn.close()

        if df.empty:
            return {}

        result: dict[str, pd.DataFrame] = {}
        for asset_code, group in df.groupby("asset_code"):
            asset_df = group.set_index("date")[
                ["open", "high", "low", "close", "volume"]
            ].copy()
            asset_df.index = pd.to_datetime(asset_df.index)
            for col in ["open", "high", "low", "close", "volume"]:
                asset_df[col] = pd.to_numeric(asset_df[col], errors="coerce")
            result[asset_code] = asset_df

        return result

    except Exception as e:
        print(f"❌ 加载 OHLCV 数据失败: {e}")
        conn.close()
        return {}


# save_price_data removed 2026-08-05: it accepted a wide close-only panel and
# synthesised fake open/high/low/volume (= close, 0) to fit the schema.
# Any code that relied on real OHLCV was misled.
# Use save_ohlcv_to_db() or save_ohlcv_data() instead — both require real OHLCV.


def get_price_data_info(workspace_path: Path, strategy_name: str) -> dict:
    """获取价格数据信息。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return {}

    try:
        result = conn.execute("""
            SELECT COUNT(DISTINCT asset_code) as n_assets,
                   COUNT(DISTINCT date) as n_dates,
                   MIN(date) as start_date,
                   MAX(date) as end_date
            FROM price_data
            WHERE strategy_name = ?
        """, [strategy_name]).fetchone()
        conn.close()

        if result:
            return {
                "n_assets": result[0],
                "n_dates": result[1],
                "start_date": result[2],
                "end_date": result[3],
            }
        return {}
    except Exception as e:
        print(f"❌ 获取价格数据信息失败: {e}")
        conn.close()
        return {}


# ============================================================
# 单资产 OHLCV 保存 (修复 OHLCV 丢失 bug)
# ============================================================

def save_ohlcv_data(
    workspace_path: Path,
    strategy_name: str,
    asset_code: str,
    ohlcv_df: pd.DataFrame,
) -> bool:
    """保存单资产 OHLCV 数据到 DuckDB。

    save_ohlcv_data 接受单资产的 OHLCV DataFrame (T, OHLCV)，完整保留 open/high/low/volume。
    For multi-asset bulk write, use save_ohlcv_to_db() which takes a {asset: ohlcv} dict.

    Args:
        workspace_path: 工作区路径
        strategy_name: 策略名称
        asset_code: 资产代码
        ohlcv_df: 单资产 OHLCV DataFrame，index=date，columns 包含 open/high/low/close/volume
    """
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        df = ohlcv_df.reset_index()
        # 首列视为日期列
        date_col = df.columns[0]
        if date_col != "date":
            df = df.rename(columns={date_col: "date"})

        # 补齐缺失列 (volume 默认 0，OHL 默认 None)
        for col, default in [("open", None), ("high", None), ("low", None), ("volume", 0.0)]:
            if col not in df.columns:
                df[col] = default

        df["strategy_name"] = strategy_name
        df["asset_code"] = asset_code

        # 保证 close 存在 (loader 至少会返回 close)
        if "close" not in df.columns:
            print(f"⚠️  {asset_code}: 缺少 close 列，跳过")
            conn.close()
            return False

        conn.execute("""
            INSERT OR REPLACE INTO price_data
            (strategy_name, asset_code, date, open, high, low, close, volume)
            SELECT strategy_name, asset_code, date, open, high, low, close, volume
            FROM df
        """)

        conn.close()
        return True
    except Exception as e:
        print(f"❌ 保存 OHLCV 失败 ({asset_code}): {e}")
        conn.close()
        return False


# ============================================================
# 因子数据操作
# ============================================================

def save_factor_data(
    workspace_path: Path,
    strategy_name: str,
    factor_name: str,
    factor_values: pd.DataFrame,
) -> bool:
    """保存因子数据到 DuckDB。

    Args:
        factor_values: (T, N) 因子值面板, index=date, columns=assets
    """
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        df = factor_values.reset_index().melt(
            id_vars="date", var_name="asset_code", value_name="factor_value"
        )
        df["strategy_name"] = strategy_name
        df["factor_name"] = factor_name

        conn.execute("""
            INSERT OR REPLACE INTO factor_data
            (strategy_name, factor_name, date, asset_code, factor_value)
            SELECT strategy_name, factor_name, date, asset_code, factor_value
            FROM df
        """)

        conn.close()
        return True
    except Exception as e:
        print(f"❌ 保存因子数据失败: {e}")
        conn.close()
        return False


def load_factor_data(
    workspace_path: Path,
    strategy_name: str,
    factor_name: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """从 DuckDB 加载因子数据。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return pd.DataFrame()

    try:
        query = """
            SELECT date, asset_code, factor_value
            FROM factor_data
            WHERE strategy_name = ? AND factor_name = ?
        """
        params = [strategy_name, factor_name]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date, asset_code"

        df = conn.execute(query, params).fetchdf()
        conn.close()

        if df.empty:
            return pd.DataFrame()

        panel = df.pivot(index="date", columns="asset_code", values="factor_value")
        panel.index = pd.to_datetime(panel.index)
        return panel

    except Exception as e:
        print(f"❌ 加载因子数据失败: {e}")
        conn.close()
        return pd.DataFrame()


# ============================================================
# 权重历史操作
# ============================================================

def save_weight_history(
    workspace_path: Path,
    strategy_name: str,
    run: str,
    weights_history: list[tuple[pd.Timestamp, dict[str, float]]],
) -> bool:
    """保存权重历史到 DuckDB。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        rows = []
        for date, weights in weights_history:
            for asset_code, weight in weights.items():
                rows.append({
                    "strategy_name": strategy_name,
                    "run": run,
                    "date": date,
                    "asset_code": asset_code,
                    "weight": weight,
                })

        if not rows:
            conn.close()
            return True

        df = pd.DataFrame(rows)  # noqa: F841 — referenced by DuckDB SQL below (FROM df)
        conn.execute("""
            INSERT OR REPLACE INTO weight_history
            (strategy_name, run, date, asset_code, weight)
            SELECT strategy_name, run, date, asset_code, weight
            FROM df
        """)

        conn.close()
        return True
    except Exception as e:
        print(f"❌ 保存权重历史失败: {e}")
        conn.close()
        return False


def load_weight_history(
    workspace_path: Path,
    strategy_name: str,
    run: str,
) -> pd.DataFrame:
    """加载权重历史。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return pd.DataFrame()

    try:
        result = conn.execute("""
            SELECT date, asset_code, weight
            FROM weight_history
            WHERE strategy_name = ? AND run = ?
            ORDER BY date, asset_code
        """, [strategy_name, run]).fetchdf()
        conn.close()

        if result.empty:
            return pd.DataFrame()

        return result.pivot(index="date", columns="asset_code", values="weight")

    except Exception as e:
        print(f"❌ 加载权重历史失败: {e}")
        conn.close()
        return pd.DataFrame()


# ============================================================
# NAV 历史操作
# ============================================================

def save_nav_history(
    workspace_path: Path,
    strategy_name: str,
    run: str,
    nav: pd.Series,
) -> bool:
    """保存 NAV 历史到 DuckDB。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        df = pd.DataFrame({  # noqa: F841 — referenced by DuckDB SQL below (FROM df)
            "strategy_name": strategy_name,
            "run": run,
            "date": nav.index,
            "nav": nav.values,
        })

        conn.execute("""
            INSERT OR REPLACE INTO nav_history
            (strategy_name, run, date, nav)
            SELECT strategy_name, run, date, nav
            FROM df
        """)

        conn.close()
        return True
    except Exception as e:
        print(f"❌ 保存 NAV 历史失败: {e}")
        conn.close()
        return False


def load_nav_history(
    workspace_path: Path,
    strategy_name: str,
    run: str,
) -> pd.Series:
    """加载 NAV 历史。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return pd.Series(dtype=float)

    try:
        result = conn.execute("""
            SELECT date, nav
            FROM nav_history
            WHERE strategy_name = ? AND run = ?
            ORDER BY date
        """, [strategy_name, run]).fetchdf()
        conn.close()

        if result.empty:
            return pd.Series(dtype=float)

        return pd.Series(result["nav"].values, index=pd.to_datetime(result["date"]))

    except Exception as e:
        print(f"❌ 加载 NAV 历史失败: {e}")
        conn.close()
        return pd.Series(dtype=float)


# ============================================================
# 因子注册表操作
# ============================================================

# 复杂度约束门 + 血统登记的新列 (旧库通过 _ensure_factor_registry_columns 补齐)
_FACTOR_REGISTRY_NEW_COLUMNS: list[tuple[str, str]] = [
    ("operator_count", "INTEGER"),
    ("param_count", "INTEGER"),
    ("feature_count", "INTEGER"),
    ("complexity_score", "DOUBLE"),
    ("info_channel", "VARCHAR"),
    ("parent_hypothesis_id", "VARCHAR"),
]


def _factor_registry_columns(conn) -> set[str]:
    """读取 factor_registry 现有列名 (read-only 安全)。"""
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'factor_registry'"
    ).fetchall()
    return {row[0] for row in rows}


def _ensure_factor_registry_columns(conn) -> None:
    """为旧库的 factor_registry 补齐新列 (需要写连接)。"""
    existing = _factor_registry_columns(conn)
    for name, col_type in _FACTOR_REGISTRY_NEW_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE factor_registry ADD COLUMN {name} {col_type}")


def register_factor(
    workspace_path: Path,
    strategy_name: str,
    factor_name: str,
    factor_code: str,
    factor_type: str = "asset_ts",
    category: str = "",
    source: str = "",
    lookback_window: int = 20,
    info_channel: str = "",
    parent_hypothesis_id: str = "",
    complexity: Optional[dict] = None,
) -> bool:
    """注册因子到因子注册表。

    登记复杂度约束门与血统信息:
    - complexity 为 None 时从 factor_code 自动静态分析 (算子数/自由参数/特征数);
      表达式无法分词则注册失败 (fail-closed)
    - info_channel: 信息通道标签 (如 momentum / carry / volatility_structure)
    - parent_hypothesis_id: 来源假设 id (血统追溯, 关联 hypothesis registry)
    """
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    if complexity is None:
        try:
            from .compute_factor import analyze_complexity

            complexity = analyze_complexity(factor_code)
        except Exception as e:
            print(f"❌ 注册因子失败: 复杂度分析不通过 ({e})")
            try:
                conn.close()
            except Exception:
                pass
            return False

    try:
        _ensure_factor_registry_columns(conn)
        conn.execute("""
            INSERT OR REPLACE INTO factor_registry
            (factor_name, factor_code, factor_type, category, source, lookback_window,
             strategy_name, operator_count, param_count, feature_count,
             complexity_score, info_channel, parent_hypothesis_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            factor_name, factor_code, factor_type, category, source, lookback_window,
            strategy_name,
            complexity.get("operator_count"),
            complexity.get("param_count"),
            complexity.get("feature_count"),
            complexity.get("complexity_score"),
            info_channel,
            parent_hypothesis_id,
        ])
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 注册因子失败: {e}")
        conn.close()
        return False


# get_factors 返回的字段顺序 (新列缺失时补 None, 兼容未迁移的旧库)
_FACTOR_REGISTRY_FIELDS = [
    "factor_name", "factor_code", "factor_type", "category", "source",
    "lookback_window", "operator_count", "param_count", "feature_count",
    "complexity_score", "info_channel", "parent_hypothesis_id",
]


def get_factors(workspace_path: Path, strategy_name: str) -> list[dict]:
    """获取策略的所有因子。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return []

    try:
        existing = _factor_registry_columns(conn)
        select_cols = [c for c in _FACTOR_REGISTRY_FIELDS if c in existing]
        result = conn.execute(f"""
            SELECT {", ".join(select_cols)}
            FROM factor_registry
            WHERE strategy_name = ?
            ORDER BY added_at
        """, [strategy_name]).fetchall()
        conn.close()

        col_index = {c: i for i, c in enumerate(select_cols)}
        return [
            {field: (row[col_index[field]] if field in col_index else None)
             for field in _FACTOR_REGISTRY_FIELDS}
            for row in result
        ]
    except Exception as e:
        print(f"❌ 获取因子失败: {e}")
        conn.close()
        return []


def remove_factor(workspace_path: Path, strategy_name: str, factor_name: str) -> bool:
    """从因子注册表移除因子。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        conn.execute("""
            DELETE FROM factor_registry
            WHERE strategy_name = ? AND factor_name = ?
        """, [strategy_name, factor_name])
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 移除因子失败: {e}")
        conn.close()
        return False


# ============================================================
# 验证缓存操作
# ============================================================

def cache_validation(
    workspace_path: Path,
    strategy_name: str,
    factor_name: str,
    factor_code: str,
    ic_mean: float,
    ic_std: float,
    ir: float,
    rank_ic_mean: float = 0.0,
    ic_decay_1d: float = 0.0,
    ic_decay_5d: float = 0.0,
    ic_decay_20d: float = 0.0,
    scores: Optional[dict] = None,
    overall_score: float = 0.0,
    is_valid: bool = False,
    fail_reasons: str = "",
    source: str = "",
    data_fingerprint: str = "",
) -> bool:
    """缓存因子验证结果。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    scores = scores or {}
    try:
        conn.execute("""
            INSERT OR REPLACE INTO validation_cache
            (factor_name, factor_code, strategy_name, ic_mean, ic_std, ir,
             rank_ic_mean, ic_decay_1d, ic_decay_5d, ic_decay_20d,
             stability_score, diversification_score, turnover_score,
             monotonicity_score, coverage_score, overall_score,
             is_valid, fail_reasons, source, data_fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            factor_name, factor_code, strategy_name,
            ic_mean, ic_std, ir, rank_ic_mean,
            ic_decay_1d, ic_decay_5d, ic_decay_20d,
            scores.get("stability", 0.0),
            scores.get("diversification", 0.0),
            scores.get("turnover", 0.0),
            scores.get("monotonicity", 0.0),
            scores.get("coverage", 0.0),
            overall_score,
            is_valid, fail_reasons, source, data_fingerprint,
        ])
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 缓存验证结果失败: {e}")
        conn.close()
        return False


def get_validation_cache(workspace_path: Path, strategy_name: str) -> list[dict]:
    """获取验证缓存。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return []

    try:
        result = conn.execute("""
            SELECT factor_name, factor_code, ic_mean, ic_std, ir,
                   rank_ic_mean, overall_score, is_valid, fail_reasons
            FROM validation_cache
            WHERE strategy_name = ?
            ORDER BY validated_at DESC
        """, [strategy_name]).fetchall()
        conn.close()

        return [
            {
                "factor_name": row[0],
                "factor_code": row[1],
                "ic_mean": row[2],
                "ic_std": row[3],
                "ir": row[4],
                "rank_ic_mean": row[5],
                "overall_score": row[6],
                "is_valid": row[7],
                "fail_reasons": row[8],
            }
            for row in result
        ]
    except Exception as e:
        print(f"❌ 获取验证缓存失败: {e}")
        conn.close()
        return []


# ============================================================
# 回测结果操作
# ============================================================

def save_backtest_result(
    workspace_path: Path,
    strategy_name: str,
    run: str,
    commit_hash: str = "",
    action: str = "",
    goal_metric: float = 0.0,
    calmar: float = 0.0,
    sharpe: float = 0.0,
    max_dd: float = 0.0,
    ann_return: float = 0.0,
    ann_vol: float = 0.0,
    sortino: float = 0.0,
    turnover: float = 0.0,
    factors_added: int = 0,
    factors_removed: int = 0,
    params_changed: int = 0,
    status: str = "pending",
    description: str = "",
) -> bool:
    """保存回测结果到 DuckDB。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        conn.execute("""
            INSERT OR REPLACE INTO backtest_results
            (strategy_name, run, commit_hash, action, goal_metric,
             calmar, sharpe, max_dd, ann_return, ann_vol, sortino, turnover,
             factors_added, factors_removed, params_changed, status, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            strategy_name, run, commit_hash, action, goal_metric,
            calmar, sharpe, max_dd, ann_return, ann_vol, sortino, turnover,
            factors_added, factors_removed, params_changed, status, description,
        ])
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 保存回测结果失败: {e}")
        conn.close()
        return False


def get_backtest_results(
    workspace_path: Path,
    strategy_name: str,
    limit: int = 100,
) -> list[dict]:
    """获取回测结果。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return []

    try:
        result = conn.execute("""
            SELECT run, commit_hash, action, goal_metric, calmar, sharpe,
                   max_dd, ann_return, ann_vol, sortino, turnover, status, description
            FROM backtest_results
            WHERE strategy_name = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, [strategy_name, limit]).fetchall()
        conn.close()

        return [
            {
                "run": row[0],
                "commit": row[1],
                "action": row[2],
                "goal_metric": row[3],
                "calmar": row[4],
                "sharpe": row[5],
                "max_dd": row[6],
                "ann_return": row[7],
                "ann_vol": row[8],
                "sortino": row[9],
                "turnover": row[10],
                "status": row[11],
                "description": row[12],
            }
            for row in result
        ]
    except Exception as e:
        print(f"❌ 获取回测结果失败: {e}")
        conn.close()
        return []


def get_best_backtest(workspace_path: Path, strategy_name: str) -> Optional[dict]:
    """获取最佳回测结果。"""
    results = get_backtest_results(workspace_path, strategy_name)
    if not results:
        return None

    # 按 goal_metric 排序 (假设 maximize)
    valid = [r for r in results if r["status"] == "keep"]
    if not valid:
        return None

    return max(valid, key=lambda x: x.get("goal_metric", 0))


# ============================================================
# 数据指纹操作
# ============================================================

def compute_data_fingerprint(data: pd.DataFrame) -> str:
    """计算数据指纹。"""
    content = f"{list(data.columns)}_{len(data)}_{data.index.min()}_{data.index.max()}"
    return hashlib.md5(content.encode()).hexdigest()[:16]


def update_data_fingerprint(
    workspace_path: Path,
    strategy_name: str,
    table_name: str,
    fingerprint: str,
    row_count: int = 0,
) -> bool:
    """更新数据指纹。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        conn.execute("""
            INSERT OR REPLACE INTO data_fingerprint
            (table_name, strategy_name, fingerprint, row_count)
            VALUES (?, ?, ?, ?)
        """, [table_name, strategy_name, fingerprint, row_count])
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 更新数据指纹失败: {e}")
        conn.close()
        return False


def get_data_fingerprint(workspace_path: Path, strategy_name: str) -> Optional[dict]:
    """获取数据指纹。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return None

    try:
        result = conn.execute("""
            SELECT table_name, fingerprint, row_count, updated_at
            FROM data_fingerprint
            WHERE strategy_name = ?
            ORDER BY updated_at DESC
            LIMIT 1
        """, [strategy_name]).fetchone()
        conn.close()

        if result:
            return {
                "table_name": result[0],
                "fingerprint": result[1],
                "row_count": result[2],
                "updated_at": result[3],
            }
        return None
    except Exception as e:
        print(f"❌ 获取数据指纹失败: {e}")
        conn.close()
        return None


# ============================================================
# 导入元数据操作 (增量更新)
# ============================================================

def get_last_import_date(
    workspace_path: Path,
    strategy_name: str,
    asset_code: str,
) -> Optional[datetime]:
    """获取某资产的最后导入日期。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return None

    try:
        result = conn.execute("""
            SELECT last_date FROM import_meta
            WHERE strategy_name = ? AND asset_code = ?
        """, [strategy_name, asset_code]).fetchone()
        conn.close()

        if result and result[0]:
            return result[0]
        return None
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Failed to get last import date: %s", e)
        return None


def update_import_meta(
    workspace_path: Path,
    strategy_name: str,
    asset_code: str,
    last_date: str,
    source: str = "",
) -> bool:
    """更新导入元数据。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        conn.execute("""
            INSERT OR REPLACE INTO import_meta
            (strategy_name, asset_code, last_date, last_source, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, [strategy_name, asset_code, last_date, source])
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 更新导入元数据失败: {e}")
        conn.close()
        return False


def get_import_meta(
    workspace_path: Path,
    strategy_name: str,
) -> list[dict]:
    """获取所有资产的导入元数据。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return []

    try:
        result = conn.execute("""
            SELECT asset_code, last_date, last_source, updated_at
            FROM import_meta
            WHERE strategy_name = ?
            ORDER BY asset_code
        """, [strategy_name]).fetchall()
        conn.close()

        return [
            {
                "asset_code": row[0],
                "last_date": row[1],
                "last_source": row[2],
                "updated_at": row[3],
            }
            for row in result
        ]
    except Exception as e:
        print(f"❌ 获取导入元数据失败: {e}")
        conn.close()
        return []
