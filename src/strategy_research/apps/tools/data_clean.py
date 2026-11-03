"""数据清洗工具集

基于业界量化金融数据清洗标准，提供参数驱动的预设模式设计。

支持的预设模式：
- quick: 快速清洗（只去重）
- standard: 标准清洗（去重 + 填充缺失值）
- thorough: 彻底清洗（去重 + 填充 + 异常值检测 + 收益率）
- resample: 变频清洗（变频 + 去重 + 填充）
- custom: 自定义清洗（完全由 params 控制）

支持的清洗步骤（可任意组合）：
- dedup: 去重
- impute: 填充缺失值
- outlier: 异常值检测
- resample: 变频
- returns: 添加收益率
"""

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

# 预设模式定义
PRESETS = {
    "quick": {
        "description": "快速清洗：只去重",
        "steps": ["dedup"],
        "params": {
            "dedup_strategy": "first",
            "dedup_subset": ["asset", "date"],
        }
    },
    "standard": {
        "description": "标准清洗：去重 + 填充缺失值",
        "steps": ["dedup", "impute"],
        "params": {
            "dedup_strategy": "first",
            "dedup_subset": ["asset", "date"],
            "impute_method": "ffill",
            "impute_columns": ["open", "high", "low", "close"],
            "impute_limit": 3,
        }
    },
    "thorough": {
        "description": "彻底清洗：去重 + 填充 + 异常值检测 + 收益率",
        "steps": ["dedup", "impute", "outlier", "returns"],
        "params": {
            "dedup_strategy": "first",
            "dedup_subset": ["asset", "date"],
            "impute_method": "ffill",
            "impute_columns": ["open", "high", "low", "close", "volume"],
            "impute_limit": 5,
            "outlier_method": "iqr",
            "outlier_threshold": 1.5,
            "outlier_action": "flag",
        }
    },
    "resample": {
        "description": "变频清洗：变频 + 去重 + 填充",
        "steps": ["resample", "dedup", "impute"],
        "params": {
            "resample_freq": "W",
            "resample_method": "last",
            "dedup_strategy": "first",
            "dedup_subset": ["asset", "date"],
            "impute_method": "ffill",
            "impute_limit": 2,
        }
    },
    "custom": {
        "description": "自定义清洗：完全由 steps 和 params 控制",
        "steps": [],
        "params": {}
    }
}


@dataclass
class CleaningReport:
    """清洗报告"""
    initial_rows: int = 0
    final_rows: int = 0
    steps_applied: List[str] = field(default_factory=list)
    duplicates_removed: int = 0
    missing_filled: int = 0
    outliers_detected: int = 0
    resampled: bool = False
    original_freq: str = ""
    target_freq: str = ""
    params_applied: dict = field(default_factory=dict)
    message: str = ""


def clean_data(
    df: pd.DataFrame,
    preset: str = "standard",
    steps: Optional[List[str]] = None,
    params: Optional[dict] = None,
    dry_run: bool = True
) -> tuple[pd.DataFrame, CleaningReport]:
    """执行数据清洗

    Args:
        df: 输入数据
        preset: 预设模式 (quick/standard/thorough/resample/custom)
        steps: 清洗步骤列表 (preset=custom 时生效，或覆盖预设步骤)
        params: 自定义参数
        dry_run: 是否只生成报告不执行

    Returns:
        tuple: (清洗后的数据, 清洗报告)
    """
    # 确定步骤列表
    if steps is not None:
        clean_steps = steps
    elif preset in PRESETS:
        clean_steps = PRESETS[preset]["steps"].copy()
    else:
        clean_steps = ["dedup", "impute"]  # 默认步骤

    # 合并参数
    if preset == "custom":
        clean_params = params or {}
    else:
        preset_params = PRESETS.get(preset, PRESETS["standard"])["params"].copy()
        if params:
            preset_params.update(params)
        clean_params = preset_params

    # 创建报告
    report = CleaningReport(
        initial_rows=len(df),
        final_rows=len(df),
        steps_applied=[],
        params_applied=clean_params,
    )

    result_df = df.copy()

    # 按顺序执行清洗步骤
    for step in clean_steps:
        if step == "dedup":
            dedup_strategy = clean_params.get("dedup_strategy")
            if dedup_strategy and dedup_strategy != "none":
                result_df, removed = _dedup(result_df, clean_params)
                report.duplicates_removed = removed
                report.steps_applied.append("dedup")

        elif step == "impute":
            impute_method = clean_params.get("impute_method")
            if impute_method and impute_method != "none":
                result_df, filled = _impute(result_df, clean_params)
                report.missing_filled = filled
                report.steps_applied.append("impute")

        elif step == "outlier":
            outlier_method = clean_params.get("outlier_method")
            if outlier_method and outlier_method != "none":
                result_df, detected = _detect_outliers(result_df, clean_params)
                report.outliers_detected = detected
                report.steps_applied.append("outlier")

        elif step == "resample":
            resample_freq = clean_params.get("resample_freq")
            if resample_freq:
                result_df, orig_freq = _resample(result_df, clean_params)
                report.resampled = True
                report.original_freq = orig_freq
                report.target_freq = resample_freq
                report.steps_applied.append("resample")

        elif step == "returns":
            result_df = _add_returns(result_df)
            report.steps_applied.append("returns")

    report.final_rows = len(result_df)
    report.message = _build_message(report)

    return result_df, report


def _dedup(df: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, int]:
    """去重"""
    strategy = params.get("dedup_strategy", "first")
    subset = params.get("dedup_subset", ["asset", "date"])

    before = len(df)

    valid_subset = [col for col in subset if col in df.columns]
    if not valid_subset:
        return df, 0

    if strategy == "first":
        df = df.drop_duplicates(subset=valid_subset, keep='first')
    elif strategy == "last":
        df = df.drop_duplicates(subset=valid_subset, keep='last')
    elif strategy == "max_volume":
        if 'volume' in df.columns:
            idx = df.groupby(valid_subset)['volume'].idxmax()
            df = df.loc[idx]

    return df, before - len(df)


def _impute(df: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, int]:
    """填充缺失值"""
    method = params.get("impute_method", "ffill")
    columns = params.get("impute_columns", ["open", "high", "low", "close"])
    limit = params.get("impute_limit")

    valid_columns = [col for col in columns if col in df.columns]
    if not valid_columns:
        return df, 0

    before = int(df[valid_columns].isna().sum().sum())

    if method == "ffill":
        df[valid_columns] = df[valid_columns].ffill(limit=limit)
    elif method == "bfill":
        df[valid_columns] = df[valid_columns].bfill(limit=limit)
    elif method == "interpolate":
        df[valid_columns] = df[valid_columns].interpolate(method='linear', limit=limit)
    elif method == "zero":
        df[valid_columns] = df[valid_columns].fillna(0)

    after = int(df[valid_columns].isna().sum().sum())
    return df, before - after


def _detect_outliers(df: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, int]:
    """检测异常值"""
    method = params.get("outlier_method", "iqr")
    threshold = params.get("outlier_threshold", 1.5)
    action = params.get("outlier_action", "flag")

    if 'close' not in df.columns:
        return df, 0

    if method == "iqr":
        Q1 = df['close'].quantile(0.25)
        Q3 = df['close'].quantile(0.75)
        IQR = Q3 - Q1
        lower = Q1 - threshold * IQR
        upper = Q3 + threshold * IQR
        outlier_mask = (df['close'] < lower) | (df['close'] > upper)
    elif method == "zscore":
        try:
            from scipy import stats
            z_scores = stats.zscore(df['close'].dropna())
            outlier_mask = pd.Series(False, index=df.index)
            outlier_mask[df['close'].dropna().index] = abs(z_scores) > threshold
        except ImportError:
            return df, 0
    else:
        return df, 0

    outlier_count = int(outlier_mask.sum())

    if action == "flag":
        df['is_outlier'] = outlier_mask
    elif action == "remove":
        df = df[~outlier_mask]
    elif action == "clip":
        if method == "iqr":
            df['close'] = df['close'].clip(lower=lower, upper=upper)

    return df, outlier_count


def _resample(df: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, str]:
    """变频

    Args:
        df: 输入数据
        params: 清洗参数

    Returns:
        tuple: (变频后的数据, 原始频率)
    """
    freq = params.get("resample_freq", "W")  # W=周, M=月, Q=季, Y=年
    method = params.get("resample_method", "last")  # last/open/high/low/close/volume_mean

    # 检测原始频率
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
        original_freq = _detect_frequency(df['date'])
    else:
        original_freq = "unknown"

    # 按 asset 分组变频
    if 'asset' in df.columns:
        resampled_parts = []
        for asset in df['asset'].unique():
            asset_df = df[df['asset'] == asset].copy()
            asset_df = asset_df.set_index('date')
            asset_df = asset_df.drop(columns=['asset'], errors='ignore')

            # 变频规则
            agg_rules = {}
            if method == "last":
                agg_rules = {col: 'last' for col in asset_df.columns}
            elif method == "open":
                agg_rules = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
            elif method == "high":
                agg_rules = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
            elif method == "low":
                agg_rules = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
            elif method == "close":
                agg_rules = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
            elif method == "volume_mean":
                agg_rules = {col: 'last' for col in asset_df.columns if col != 'volume'}
                agg_rules['volume'] = 'mean'

            # 应用聚合规则
            if agg_rules:
                resampled = asset_df.resample(freq).agg(agg_rules)
            else:
                resampled = asset_df.resample(freq).last()

            resampled = resampled.dropna(subset=['close'])
            resampled['asset'] = asset
            resampled_parts.append(resampled.reset_index())

        result_df = pd.concat(resampled_parts, ignore_index=True)
    else:
        df = df.set_index('date')
        agg_rules = {col: 'last' for col in df.columns}
        result_df = df.resample(freq).agg(agg_rules).dropna().reset_index()

    return result_df, original_freq


def _detect_frequency(dates: pd.Series) -> str:
    """检测日期频率"""
    if len(dates) < 2:
        return "unknown"

    dates_sorted = dates.sort_values()
    diffs = dates_sorted.diff().dropna()

    if len(diffs) == 0:
        return "unknown"

    median_diff = diffs.median()

    if median_diff <= pd.Timedelta(days=1):
        return "D"
    elif median_diff <= pd.Timedelta(days=7):
        return "W"
    elif median_diff <= pd.Timedelta(days=31):
        return "M"
    elif median_diff <= pd.Timedelta(days=92):
        return "Q"
    else:
        return "Y"


def _add_returns(df: pd.DataFrame) -> pd.DataFrame:
    """添加收益率"""
    if 'close' in df.columns:
        df['return'] = df['close'].pct_change()
    return df


def _build_message(report: CleaningReport) -> str:
    """构建清洗消息"""
    parts = []

    if report.steps_applied:
        parts.append(f"执行步骤: {', '.join(report.steps_applied)}")

    if report.duplicates_removed > 0:
        parts.append(f"删除 {report.duplicates_removed} 条重复数据")

    if report.missing_filled > 0:
        parts.append(f"填充 {report.missing_filled} 条缺失值")

    if report.outliers_detected > 0:
        parts.append(f"检测到 {report.outliers_detected} 条异常值")

    if report.resampled:
        parts.append(f"变频 {report.original_freq} → {report.target_freq}")

    if not parts:
        return "数据清洗完成，无需处理"

    return "清洗完成：" + "，".join(parts)
