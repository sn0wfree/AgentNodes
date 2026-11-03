"""Strategy exporter — convert strategy.py to Pine Script / TDX / vnpy formats."""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def export_strategy(
    strategy_path: Path,
    output_dir: Path,
    formats: list[str] | None = None,
) -> dict[str, Any]:
    """Export a strategy to multiple platform formats.

    Args:
        strategy_path: Path to strategy.py file.
        output_dir: Directory to write exported files.
        formats: List of formats to export ("pine", "tdx", "vnpy"). Default: all.

    Returns:
        Dict with export results per format.
    """
    if formats is None:
        formats = ["pine", "tdx", "vnpy"]

    output_dir.mkdir(parents=True, exist_ok=True)

    if not strategy_path.exists():
        return {fmt: {"status": "error", "error": f"Strategy file not found: {strategy_path}"} for fmt in formats}

    strategy_code = strategy_path.read_text(encoding="utf-8")
    strategy_info = _parse_strategy(strategy_code)

    results: dict[str, Any] = {}

    for fmt in formats:
        try:
            if fmt == "pine":
                code = _to_pine(strategy_info)
                out_path = output_dir / f"{strategy_path.stem}.pine"
            elif fmt == "tdx":
                code = _to_tdx(strategy_info)
                out_path = output_dir / f"{strategy_path.stem}.tdx"
            elif fmt == "vnpy":
                code = _to_vnpy(strategy_info)
                out_path = output_dir / f"{strategy_path.stem}.py"
            else:
                results[fmt] = {"status": "error", "error": f"Unknown format: {fmt}"}
                continue

            out_path.write_text(code, encoding="utf-8")
            results[fmt] = {"status": "ok", "path": str(out_path), "lines": len(code.splitlines())}

        except Exception as exc:  # noqa: BLE001
            results[fmt] = {"status": "error", "error": str(exc)}

    return results


def _parse_strategy(code: str) -> dict[str, Any]:
    """Parse strategy.py to extract params, factors, and logic."""
    info: dict[str, Any] = {
        "params": {},
        "factor_exprs": [],
        "factor_weights": [],
        "name": "strategy",
    }

    tree = ast.parse(code)

    for node in ast.walk(tree):
        # Extract PARAMS dict
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PARAMS":
                    if isinstance(node.value, ast.Dict):
                        for key, value in zip(node.value.keys, node.value.values):
                            if isinstance(key, ast.Constant):
                                info["params"][key.value] = ast.literal_eval(value)

            # Extract FACTOR_EXPRS list
            if isinstance(target, ast.Name) and target.id == "FACTOR_EXPRS":
                if isinstance(node.value, ast.List):
                    info["factor_exprs"] = [
                        ast.literal_eval(elt) for elt in node.value.elts
                        if isinstance(elt, ast.Constant)
                    ]

            # Extract FACTOR_WEIGHTS list
            if isinstance(target, ast.Name) and target.id == "FACTOR_WEIGHTS":
                if isinstance(node.value, ast.List):
                    info["factor_weights"] = [
                        ast.literal_eval(elt) for elt in node.value.elts
                    ]

    return info


def _to_pine(info: dict[str, Any]) -> str:
    """Convert strategy to TradingView Pine Script v5."""
    params = info.get("params", {})
    factors = info.get("factor_exprs", [])

    lines = [
        "// @version=5",
        f'strategy("{info["name"]}", overlay=false)',
        "",
    ]

    # Parameters
    for k, v in params.items():
        if isinstance(v, float):
            lines.append(f"{k} = input.float({v}, '{k}')")
        elif isinstance(v, int):
            lines.append(f"{k} = input.int({v}, '{k}')")
        elif isinstance(v, bool):
            lines.append(f"{k} = input.bool({v}, '{k}')")
        else:
            lines.append(f"{k} = input.string('{v}', '{k}')")

    lines.append("")

    # Factor signals
    if factors:
        for i, expr in enumerate(factors):
            # Convert factor expression to Pine Script (simplified)
            pine_expr = _factor_to_pine(expr)
            lines.append(f"factor_{i} = {pine_expr}")

        lines.append("")
        lines.append("// Strategy entry")
        if len(factors) >= 2:
            lines.append("long_signal = factor_0 > 0 and factor_1 > 0")
            lines.append("short_signal = factor_0 < 0 and factor_1 < 0")
        else:
            lines.append("long_signal = factor_0 > 0")
            lines.append("short_signal = factor_0 < 0")

        lines.append("if long_signal")
        lines.append("    strategy.entry('Long', strategy.long)")
        lines.append("if short_signal")
        lines.append("    strategy.entry('Short', strategy.short)")

    return "\n".join(lines)


def _factor_to_pine(expr: str) -> str:
    """Convert factor expression to Pine Script (simplified)."""
    # ts_mean(close, N) → ta.sma(close, N)
    expr = re.sub(r"ts_mean\((\w+),\s*(\d+)\)", r"ta.sma(\1, \2)", expr)
    # ts_std(close, N) → ta.stdev(close, N)
    expr = re.sub(r"ts_std\((\w+),\s*(\d+)\)", r"ta.stdev(\1, \2)", expr)
    # ts_return(close, N) → close / close[N] - 1
    expr = re.sub(r"ts_return\((\w+),\s*(\d+)\)", r"\1 / \1[\2] - 1", expr)
    # ts_rank(close, N) → ta.rank(close)
    expr = re.sub(r"ts_rank\((\w+),\s*(\d+)\)", r"ta.rank(\1)", expr)
    return expr


def _to_tdx(info: dict[str, Any]) -> str:
    """Convert strategy to TDX (通达信) formula format."""
    params = info.get("params", {})
    factors = info.get("factor_exprs", [])

    lines = [
        f"// {info['name']} — 通达信公式",
        "",
    ]

    # Parameters
    for k, v in params.items():
        lines.append(f"{k}:={v};")

    lines.append("")

    # Factor signals
    for i, expr in enumerate(factors):
        tdx_expr = _factor_to_tdx(expr)
        lines.append(f"FACTOR{i}:={tdx_expr};")

    lines.append("")

    # Buy/Sell signals
    if len(factors) >= 2:
        lines.append("买入:=FACTOR0>0 AND FACTOR1>0;")
        lines.append("卖出:=FACTOR0<0 AND FACTOR1<0;")
    elif factors:
        lines.append("买入:=FACTOR0>0;")
        lines.append("卖出:=FACTOR0<0;")

    lines.append("DRAWTEXT(买入,LOW,'买'),COLORRED;")
    lines.append("DRAWTEXT(卖出,HIGH,'卖'),COLORGREEN;")

    return "\n".join(lines)


def _factor_to_tdx(expr: str) -> str:
    """Convert factor expression to TDX format."""
    # ts_mean(close, N) → MA(close, N)
    expr = re.sub(r"ts_mean\((\w+),\s*(\d+)\)", r"MA(\1, \2)", expr)
    # ts_std(close, N) → STD(\1, \2)
    expr = re.sub(r"ts_std\((\w+),\s*(\d+)\)", r"STD(\1, \2)", expr)
    # ts_return(close, N) → (close - REF(close, N)) / REF(close, N)
    expr = re.sub(
        r"ts_return\((\w+),\s*(\d+)\)",
        r"(\1 - REF(\1, \2)) / REF(\1, \2)",
        expr,
    )
    return expr


def _factor_to_vnpy(expr: str) -> str:
    """Convert factor expression to vnpy format."""
    # ts_mean(close, N) → sma(close, N) or simply keep as-is
    expr = re.sub(r"ts_mean\((\w+),\s*(\d+)\)", r"sma(\1, \2)", expr)
    expr = re.sub(r"ts_std\((\w+),\s*(\d+)\)", r"std(\1, \2)", expr)
    expr = re.sub(r"ts_return\((\w+),\s*(\d+)\)", r"(\1 / ref(\1, \2) - 1)", expr)
    return expr


def _to_vnpy(info: dict[str, Any]) -> str:
    """Convert strategy to vnpy CtaTemplate format."""
    params = info.get("params", {})
    factors = info.get("factor_exprs", [])

    param_lines = []
    var_lines = []
    for k, v in params.items():
        param_lines.append(f"    {k} = {repr(v)}")
        var_lines.append(f"        self.{k} = {k}")

    param_str = ",\n".join(param_lines) if param_lines else "    pass"
    init_vars = "\n".join(var_lines) if var_lines else "        pass"

    # Convert factors to vnpy signal logic
    signal_lines = []
    for i, expr in enumerate(factors):
        vnpy_expr = _factor_to_vnpy(expr)
        signal_lines.append(f"        factor_{i} = {vnpy_expr}")

    signals = "\n".join(signal_lines) if signal_lines else "        pass"

    return f'''from vnpy_ctastrategy import (
    CtaTemplate,
    StopOrder,
    TickData,
    BarData,
    TradeData,
    OrderData,
    BarGenerator,
    ArrayManager,
)


class {info["name"].replace("-", "_").title().replace("_", "")}Strategy(CtaTemplate):
    """Auto-exported strategy."""

{param_str}

    parameters = [{", ".join(repr(k) for k in params.keys())}]
    variables = [{", ".join(repr(k) for k in params.keys())}]

    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
{init_vars}

    def on_init(self):
        self.write_log("策略初始化")
        self.load_bar(10)

    def on_start(self):
        self.write_log("策略启动")

    def on_stop(self):
        self.write_log("策略停止")

    def on_bar(self, bar: BarData):
{signals}

        # TODO: Add trading logic based on factors
        self.put_event()

    def on_order(self, order: OrderData):
        pass

    def on_trade(self, trade: TradeData):
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder):
        pass
'''
