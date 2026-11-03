"""Backtest runner — 主入口 + AST guard + engine routing。

流程：
  1. safe_run_dir() — 安全校验
  2. config validation (Pydantic)
  3. AST guard — 验证 signal_engine.py
  4. 从 DuckDB 加载 OHLCV 数据
  5. 创建市场引擎
  6. 运行回测
  7. 写 run_card + artifacts
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Type

from .base import BaseEngine
from .signals import SignalEngine

logger = logging.getLogger(__name__)


# ============================================================
# AST Guard — 验证 signal_engine.py 安全性
# ============================================================

_UNSAFE_BUILTINS = frozenset({"exec", "eval", "compile", "__import__", "breakpoint"})
_UNSAFE_MODULES = frozenset({
    "os", "subprocess", "shutil", "socket", "requests", "urllib", "http",
    "ctypes", "pickle", "shelve", "sqlite3", "importlib", "code", "codeop",
    "compileall", "py_compile", "pdb", "profile", "cProfile", "timeit",
    "distutils", "setuptools", "pip", "ensurepip",
})


def _validate_signal_engine_source(file_path: Path) -> None:
    """AST 安全检查：拒绝可执行的顶层语句。"""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except SyntaxError as exc:
        raise ValueError(f"Invalid signal_engine.py syntax: {exc}") from exc

    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # docstrings OK

        if isinstance(node, ast.ImportFrom) and node.module == "signal_engine":
            raise ValueError(
                "Circular import: 'from signal_engine import ...' imports itself"
            )

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # Check for unsafe module imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".")[0]
                    if mod in _UNSAFE_MODULES:
                        raise ValueError(f"Unsafe module import: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module.split(".")[0]
                if mod in _UNSAFE_MODULES:
                    raise ValueError(f"Unsafe module import: {node.module}")
            continue

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _validate_function_def(node)
            continue

        if isinstance(node, ast.ClassDef):
            _validate_class_body(node)
            continue

        if _is_safe_constant_assignment(node):
            continue

        raise ValueError(
            f"Executable top-level statement {type(node).__name__} is not allowed"
        )


def _validate_function_def(node) -> None:
    """验证函数定义安全性。"""
    # No decorators
    if node.decorator_list:
        raise ValueError(f"Decorators not allowed on function '{node.name}'")

    # No unsafe defaults
    for arg in node.args.defaults:
        if not _is_literal_node(arg):
            raise ValueError(
                f"Non-literal default value in function '{node.name}'"
            )

    # No unsafe annotations
    for arg in node.args.args + node.args.kwonlyargs:
        if arg.annotation and not _is_safe_node(arg.annotation):
            raise ValueError(
                f"Unsafe annotation in function '{node.name}'"
            )


def _validate_class_body(node) -> None:
    """验证类定义安全性。"""
    # No decorators
    if node.decorator_list:
        raise ValueError(f"Decorators not allowed on class '{node.name}'")

    # No keywords (metaclass etc.)
    if node.keywords:
        raise ValueError(f"Class keywords not allowed on '{node.name}'")

    # Check body
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _validate_function_def(item)
        elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant):
            continue  # docstring
        elif _is_safe_constant_assignment(item):
            continue
        elif isinstance(item, ast.Pass):
            continue
        else:
            raise ValueError(
                f"Executable statement in class '{node.name}' body: {type(item).__name__}"
            )


def _is_literal_node(node) -> bool:
    """检查 AST 节点是否为字面量。"""
    if isinstance(node, (ast.Constant, ast.Str, ast.Num)):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal_node(elt) for elt in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            _is_literal_node(k) and _is_literal_node(v)
            for k, v in zip(node.keys, node.values)
        )
    return False


def _is_safe_constant_assignment(node) -> bool:
    """检查是否为安全的顶层常量赋值。"""
    if not isinstance(node, ast.Assign):
        return False
    return all(_is_literal_node(target) for target in node.targets)


def _is_safe_node(node) -> bool:
    """检查节点是否为安全类型。"""
    if isinstance(node, (ast.Name, ast.Attribute, ast.Constant)):
        return True
    if isinstance(node, ast.Subscript):
        return _is_safe_node(node.value) and _is_safe_node(node.slice)
    if isinstance(node, ast.Tuple):
        return all(_is_safe_node(elt) for elt in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_safe_node(node.left) and _is_safe_node(node.right)
    return False


def _validate_signal_engine_class(engine_cls: Type) -> None:
    """运行时预检：SignalEngine 可零参实例化且有 generate() 方法。"""
    sig = inspect.signature(engine_cls.__init__)
    required = [
        p.name for p in sig.parameters.values()
        if p.name != "self"
        and p.default is inspect.Parameter.empty
        and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    if required:
        raise ValueError(
            f"SignalEngine.__init__() has required arguments {required}. "
            "All parameters must have default values."
        )

    if not callable(getattr(engine_cls, "generate", None)):
        raise ValueError(
            "SignalEngine must have a callable 'generate' method"
        )


def _load_signal_engine(file_path: Path) -> Type[SignalEngine]:
    """加载并验证 signal_engine.py。"""
    _validate_signal_engine_source(file_path)

    spec = importlib.util.spec_from_file_location("signal_engine", str(file_path))
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load {file_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["signal_engine"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("signal_engine", None)

    engine_cls = getattr(module, "SignalEngine", None)
    if engine_cls is None:
        raise ValueError("No SignalEngine class found in file")

    _validate_signal_engine_class(engine_cls)
    return engine_cls


# ============================================================
# Engine routing
# ============================================================

def _create_market_engine(
    config: Dict[str, Any],
    codes: list[str],
) -> BaseEngine:
    """根据 codes 和 config 创建市场引擎。"""
    from ..utils.market_detection import detect_market

    markets = {detect_market(c) for c in codes} if codes else set()
    engine_type = config.get("engine", "auto")

    # Cross-market → CompositeEngine
    if len(markets) > 1 or engine_type == "composite":
        from .composite import CompositeEngine
        return CompositeEngine(config, codes)

    # Futures
    if "futures" in markets or engine_type == "futures":
        from ..utils.market_detection import detect_source  # noqa: F811
        source = detect_source(codes[0]) if codes else "tushare"
        if source in ("tushare", "akshare"):
            from .china_futures import ChinaFuturesEngine
            return ChinaFuturesEngine(config)
        from .global_futures import GlobalFuturesEngine
        return GlobalFuturesEngine(config)

    # Forex
    if "forex" in markets or engine_type == "forex":
        from .forex import ForexEngine
        return ForexEngine(config)

    # India
    if "india_equity" in markets:
        from .india_equity import IndiaEquityEngine
        return IndiaEquityEngine(config)

    # Crypto
    if "crypto" in markets or engine_type == "crypto":
        from .crypto import CryptoEngine
        return CryptoEngine(config)

    # A-share
    if "a_share" in markets:
        from .china_a import ChinaAEngine
        return ChinaAEngine(config)

    # US/HK equity
    if markets & {"us_equity", "hk_equity"}:
        from .global_equity import GlobalEquityEngine
        market = "hk" if "hk_equity" in markets else "us"
        return GlobalEquityEngine(config, market=market)

    # Default: ChinaA
    from .china_a import ChinaAEngine
    return ChinaAEngine(config)


# ============================================================
# Main runner
# ============================================================

def run_engine_backtest(
    workspace_path: Path,
    strategy_name: str,
    config: Dict[str, Any],
    signal_engine_path: Optional[Path] = None,
    signal_engine_cls: Optional[Type[SignalEngine]] = None,
    bars_per_year: int = 252,
    optimizer: Optional[str] = None,
    progress_every: Optional[int] = None,
) -> Dict[str, Any]:
    """运行完整回测 pipeline。

    Args:
        workspace_path: 工作区路径
        strategy_name: 策略名称
        config: 回测配置
        signal_engine_path: signal_engine.py 路径 (二选一)
        signal_engine_cls: SignalEngine 类 (二选一)
        bars_per_year: 年化 bar 数
        optimizer: 优化器名称 (equal_volatility/risk_parity/mean_variance/max_diversification/turnover_aware)
        progress_every: 进度行频率（透传 engine.run_backtest；None=零输出，
            0=自动频率）。后台化场景（CLI 子进程 + run.log）用它驱动停滞判定。

    Returns:
        metrics dict
    """
    from strategy_research.apps.db import load_ohlcv_data

    codes = config.get("codes", [])
    start_date = config.get("start_date")
    end_date = config.get("end_date")

    # 1. Load signal engine
    if signal_engine_cls is None:
        if signal_engine_path is None:
            raise ValueError("Either signal_engine_path or signal_engine_cls required")
        signal_engine_cls = _load_signal_engine(signal_engine_path)
    signal_engine = signal_engine_cls()

    # 2. Load data from DuckDB
    data_map = load_ohlcv_data(workspace_path, strategy_name, codes, start_date, end_date)
    if not data_map:
        raise ValueError(f"No data found for strategy '{strategy_name}'")

    # 3. Generate signals
    signal_map = signal_engine.generate(data_map)
    valid_codes = [c for c in codes if c in data_map and c in signal_map]

    # 4. Create engine
    engine = _create_market_engine(config, valid_codes)

    # 5. Build optimizer callable
    opt_func = None
    if optimizer and optimizer != "none":
        from .optimizers import optimize_weights

        # 从 config 读取优化器参数
        opt_lookback = int(config.get("optimizer_lookback", 0))
        opt_sign_preservation = bool(config.get("optimizer_sign_preservation", True))

        def _opt_func(ret_df, pos_df, dates):
            return optimize_weights(
                ret_df, pos_df, dates, method=optimizer,
                lookback=opt_lookback,
                sign_preservation=opt_sign_preservation,
            )
        opt_func = _opt_func

    # 6. Run
    metrics = engine.run_backtest(
        data_map, signal_map, valid_codes, bars_per_year,
        optimizer=opt_func, progress_every=progress_every,
    )

    return metrics


__all__ = [
    "_validate_signal_engine_source",
    "_validate_signal_engine_class",
    "_load_signal_engine",
    "_create_market_engine",
    "run_engine_backtest",
]
