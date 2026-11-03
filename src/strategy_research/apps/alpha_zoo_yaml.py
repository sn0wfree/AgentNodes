"""Alpha Zoo YAML 因子加载器。

支持从 YAML 配置文件加载和计算因子。
配置格式:
  - id, zoo, nickname, theme, columns_required, ...
  - steps: 中间步骤列表 (用于可读性)
  - final: 最终公式

AST 节点类型:
  - {column: close}      -> 引用数据列
  - {value: 20}          -> 常量
  - {ref: name}          -> 引用 steps 中的变量
  - {op: ts_mean, args: [...]}  -> 算子调用
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

# 兄弟模块尚未随本步搬迁（W1 后续 step / W2 alpha_zoo 主包），
# 暂用旧绝对路径；对应包搬迁时会全库前缀改写追平。
from strategy_research.apps.alpha_zoo_ops import ALPHA_ZOO_OPS
from strategy_research.core.compute_factor import OPERATORS

# ============================================================
# YAML 加载
# ============================================================

def load_alpha_yaml(yaml_path: Path) -> dict:
    """从 YAML 文件加载因子配置。

    Returns:
        dict: 因子配置 (id, zoo, steps, final, ...)
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not config:
        raise ValueError(f"空的 YAML 文件: {yaml_path}")

    # 验证必要字段
    if "id" not in config:
        raise ValueError(f"缺少 'id' 字段: {yaml_path}")
    if "final" not in config:
        raise ValueError(f"缺少 'final' 字段: {yaml_path}")

    return config


def load_alpha_yaml_from_string(yaml_str: str) -> dict:
    """从 YAML 字符串加载因子配置。"""
    config = yaml.safe_load(yaml_str)
    if not config:
        raise ValueError("空的 YAML 字符串")
    if "id" not in config:
        raise ValueError("缺少 'id' 字段")
    if "final" not in config:
        raise ValueError("缺少 'final' 字段")
    return config


# ============================================================
# AST 求值器
# ============================================================

def _get_operator(op_name: str):
    """获取算子函数，优先使用 Alpha Zoo 算子。"""
    # 算子别名
    aliases = {"ewm": "ewm_mean"}
    op_name = aliases.get(op_name, op_name)
    # 优先使用 Alpha Zoo 算子 (支持 DataFrame)
    if op_name in ALPHA_ZOO_OPS:
        return ALPHA_ZOO_OPS[op_name]
    # 回退到 compute_factor 算子 (支持 Series)
    if op_name in OPERATORS:
        return OPERATORS[op_name]
    return None


def _where_df(condition, true_val, false_val):
    """DataFrame 版本的 where 函数。"""
    if isinstance(condition, pd.DataFrame):
        return pd.DataFrame(
            np.where(condition, true_val, false_val),
            index=condition.index,
            columns=condition.columns
        )
    elif isinstance(condition, pd.Series):
        return pd.Series(
            np.where(condition, true_val, false_val),
            index=condition.index
        )
    else:
        return np.where(condition, true_val, false_val)


def evaluate_node(
    node: Any,
    env: dict[str, pd.DataFrame],
    data: dict[str, pd.DataFrame],
) -> Any:
    """递归求值 AST 节点。

    Args:
        node: AST 节点 (dict 或标量)
        env: 变量环境 (steps 的计算结果)
        data: 原始数据 (dict: 列名 -> DataFrame)

    Returns:
        DataFrame (列引用/算子结果) 或 标量 (常量)
    """
    # 标量直接返回
    if isinstance(node, (int, float)):
        return node
    if isinstance(node, str):
        return _resolve_identifier(node, env, data)

    if not isinstance(node, dict):
        raise ValueError(f"无效的 AST 节点: {node}")

    # 列引用: {column: close}
    if "column" in node:
        col = node["column"]
        if col in data:
            return data[col]
        raise ValueError(f"数据中不存在列: {col}")

    # 常量: {value: 20}
    if "value" in node:
        val = node["value"]
        # 特殊处理 Python 内置类型名称
        if isinstance(val, str):
            type_map = {"float": float, "int": int, "bool": bool, "complex": complex}
            if val in type_map:
                return type_map[val]
        return val

    # 步骤引用: {ref: name}
    if "ref" in node:
        ref = node["ref"]
        # 首先检查是否是数据列
        if ref in data:
            return data[ref]
        # 然后检查是否是环境变量
        if ref in env:
            return env[ref]
        # 特殊处理 panel 引用 -> 返回 panel dict 本身
        if ref == "panel":
            return data
        # 特殊处理 pd 引用 -> 返回 pandas 模块
        if ref == "pd":
            import pandas as _pd
            return _pd
        raise ValueError(f"未知的步骤引用: {ref}")

    # 算子调用: {op: ts_mean, args: [...]}
    if "op" in node:
        return _call_operator(node, env, data)

    raise ValueError(f"无效的 AST 节点: {node}")


def _call_operator(
    node: dict,
    env: dict[str, pd.DataFrame],
    data: dict[str, pd.DataFrame],
) -> Any:
    """Dispatch an ``{op: ..., args: [...]}`` node."""
    op_name = node["op"]
    args_nodes = node.get("args", [])

    op_func = _get_operator(op_name)
    if op_func is None:
        raise ValueError(f"未知算子: {op_name}")

    # 递归求值参数
    args = [_coerce_arg(evaluate_node(arg, env, data)) for arg in args_nodes]

    # 调用算子
    try:
        # 特殊处理 where 算子 (使用 DataFrame 版本)
        if op_name == "where":
            # 2-arg where: pandas .where(cond) -> where(cond, value, NaN)
            if len(args) == 2:
                cond, val = args
                return _where_df(cond, val, np.nan)
            return _where_df(*args)
        return op_func(*args)
    except Exception as e:
        raise ValueError(f"算子 {op_name} 执行失败: {e}")


def _resolve_identifier(
    name: str,
    env: dict[str, pd.DataFrame],
    data: dict[str, pd.DataFrame],
) -> Any:
    """Resolve a bare-string identifier to a column/environment value."""
    if name in data:
        return data[name]
    if name in env:
        return env[name]
    raise ValueError(f"未知标识符: {name}")


def _coerce_arg(arg_val: Any) -> Any:
    """Coerce a DataFrame/Series argument to a scalar when it's constant."""
    if isinstance(arg_val, pd.DataFrame):
        # 检查是否是常量 DataFrame
        if len(arg_val.columns) == 1 and len(arg_val.iloc[:, 0].unique()) == 1:
            val = arg_val.iloc[0, 0]
            if isinstance(val, (int, float)) and val == int(val):
                return int(val)
            return val
        return arg_val
    if isinstance(arg_val, pd.Series):
        # 检查是否是常量 Series
        if len(arg_val.unique()) == 1:
            val = arg_val.iloc[0]
            if isinstance(val, (int, float)) and val == int(val):
                return int(val)
            return val
        return arg_val
    return arg_val


# ============================================================
# 因子计算
# ============================================================

def compute_alpha_from_yaml(
    config: dict,
    panel: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """从 YAML 配置计算因子。

    Args:
        config: 因子配置 (从 load_alpha_yaml 加载)
        panel: 数据面板 {列名: DataFrame}

    Returns:
        DataFrame: 因子值
    """
    # 获取参考 DataFrame (用于确定 shape)
    ref_df = None
    for key in ["close", "open", "high", "low", "volume"]:
        if key in panel:
            ref_df = panel[key]
            break
    if ref_df is None:
        raise ValueError("panel 中必须包含 close/open/high/low/volume 之一")

    # 构建环境
    env: dict[str, pd.DataFrame] = {}

    # 执行 steps
    steps = config.get("steps", [])
    for step in steps:
        name = step.get("name")
        expr = step.get("expr")
        if not name or not expr:
            raise ValueError(f"步骤必须包含 name 和 expr: {step}")

        # 跳过 close 步骤 (它只是引用数据列)
        if name == "close":
            continue

        env[name] = evaluate_node(expr, env, panel)

    # 执行 final
    final = config.get("final")
    if not final:
        raise ValueError("缺少 'final' 字段")

    result = evaluate_node(final, env, panel)

    # 确保返回 DataFrame
    if isinstance(result, pd.Series):
        result = result.to_frame(name=config.get("id", "factor"))

    # 验证结果
    if not isinstance(result, pd.DataFrame):
        raise TypeError(f"因子必须返回 DataFrame, 得到 {type(result)}")

    if result.shape != ref_df.shape:
        raise ValueError(f"形状不匹配: {result.shape} != {ref_df.shape}")

    # 注意: 暂时不检查 inf，因为某些因子可能产生 inf 值
    # if np.any(np.isinf(result.values)):
    #     raise ValueError("因子值包含 inf")

    return result


def compute_alpha_from_yaml_file(
    yaml_path: Path,
    panel: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """从 YAML 文件计算因子。"""
    config = load_alpha_yaml(yaml_path)
    return compute_alpha_from_yaml(config, panel)


# ============================================================
# 工具函数
# ============================================================

def get_alpha_metadata(config: dict) -> dict:
    """从配置中提取元数据。"""
    return {
        "id": config.get("id"),
        "zoo": config.get("zoo"),
        "nickname": config.get("nickname", ""),
        "theme": config.get("theme", []),
        "formula_latex": config.get("formula_latex", ""),
        "columns_required": config.get("columns_required", []),
        "universe": config.get("universe", []),
        "frequency": config.get("frequency", []),
        "decay_horizon": config.get("decay_horizon", 0),
        "min_warmup_bars": config.get("min_warmup_bars", 0),
    }
