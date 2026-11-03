"""Alpha Zoo .py → .yaml 批量转换脚本。

分析每个因子 .py 文件的 compute() 函数，
尝试转换为 YAML AST 格式。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, cast

import yaml

# ============================================================
# Python AST 分析
# ============================================================

_BIN_OP_MAP = {
    ast.Add: "add",
    ast.Sub: "sub",
    ast.Mult: "mul",
    ast.Div: "div",
    ast.Pow: "pow",
}

_CMP_OP_MAP = {
    ast.Lt: "lt",
    ast.LtE: "lte",
    ast.Gt: "gt",
    ast.GtE: "gte",
    ast.Eq: "eq",
    ast.NotEq: "neq",
}

# Helpers → operator-name mapping used by python_ast_to_yaml_ast.
_HELPER_OP_MAP = {
    "_rolling_sum": "ts_sum",
    "_rolling_mean": "ts_mean",
    "_rolling_std": "ts_std",
    "_rolling_var": "ts_var",
    "_rolling_min": "ts_min",
    "_rolling_max": "ts_max",
    "_rolling_rank": "ts_rank",
    "_rolling_corr": "ts_corr",
    "_rolling_cov": "ts_cov",
    "_rolling_prod": "ts_prod",
    "_where_ternary": "where",
    "_sma": "ts_mean",
    "_make_one": "fill_null",
}

_NO_EARLY_RETURN = object()


def _apply_call_rename(func: str) -> str | None:
    """Map a Python func name to its YAML operator name, or None if unchanged."""
    return {
        "_cross_sectional_zscore": "zscore",
        "_delay": "delay",
        "_rolling_sum": "ts_sum",
        "_rolling_mean": "ts_mean",
        "_rolling_std": "ts_std",
        "_rolling_var": "ts_var",
        "_rolling_min": "ts_min",
        "_rolling_max": "ts_max",
        "_rolling_rank": "ts_rank",
        "_rolling_corr": "ts_corr",
        "_rolling_cov": "ts_cov",
        "_rolling_prod": "ts_prod",
        "_where_ternary": "where",
        "sum": "ts_sum",
        "mean": "ts_mean",
        "std": "ts_std",
        "var": "ts_var",
        "min": "ts_min",
        "max": "ts_max",
        "median": "ts_median",
        "skew": "ts_skew",
        "kurt": "ts_kurt",
        "quantile": "ts_rank",
        "cumsum": "expanding_sum",
        "log1p": "log",
        "pow": "signed_power",
        "power": "signed_power",
        "minimum": "ts_min",
        "maximum": "ts_max",
        "shift": "delay",
        "diff": "delta",
        "pct_change": "ts_return",
        "fillna": "fill_null",
    }.get(func)


def _apply_call_special(func: str, yaml_args: list) -> tuple[str, object]:
    """May return ``(func, CONVERTED)`` for early-return special cases.

    Default returns ``(func, _NO_EARLY_RETURN)`` so the caller builds the
    ``{"op": func, "args": yaml_args}`` node.
    """
    if func == "_sma":
        # _sma(x, n, m) -> ts_mean(x, n) - drop the m parameter
        if len(yaml_args) > 2:
            yaml_args = yaml_args[:2]
        return "ts_mean", _NO_EARLY_RETURN
    if func in ("_ind_neutralize", "cumprod", "replace", "to_numpy"):
        # Return the first argument unchanged.
        return func, yaml_args[0] if yaml_args else None
    if func == "_bench_close":
        return func, {"column": "close"}
    if func == "vwap":
        return func, {"op": "safe_div", "args": [{"column": "amount"}, {"column": "volume"}]}
    if func == "ones_like":
        return func, {"value": 1.0}
    if func == "full_like":
        # numpy: full_like(a, fill_value) —— 第一个参数只是形状/类型参考，
        # 第二个才是填充值。原实现返回 yaml_args[0]，把形状参考当成填充值：
        # ``np.full_like(x, 7)`` 会产出 ``ref x`` 而不是 ``7``，语义完全错误
        # 且不报错（2026-11-02 补测发现；alpha_zoo 里 10 个文件用到）。
        if len(yaml_args) > 1:
            return func, yaml_args[1]
        return func, {"value": 1.0}
    return func, _NO_EARLY_RETURN


def _extract_span(args: list) -> Any:
    """Extract the span/alpha kwarg for an ``ewm`` method call."""
    span = {"value": 20}
    for kw in args:
        if isinstance(kw, dict) and kw.get("_type") == "kwarg":
            kw_name = kw.get("name")
            if kw_name == "span":
                span = kw.get("value")
            elif kw_name == "alpha":
                span = _span_from_alpha(kw.get("value", {}))
        elif isinstance(kw, dict) and kw.get("_type") == "value":
            span = kw
    return span


def _extract_window(args: list) -> Any:
    """Extract the window kwarg for a ``rolling`` method call."""
    window = {"value": 20}
    for kw in args:
        if isinstance(kw, dict) and kw.get("_type") == "kwarg":
            if kw.get("name") == "window":
                window = kw.get("value")
        elif isinstance(kw, dict) and kw.get("_type") == "value":
            window = kw
    return window


def _span_from_alpha(alpha_val: dict) -> dict:
    """Convert an ewm alpha value to a span dict (span = 2/alpha - 1)."""
    span = {"value": 20}
    if not isinstance(alpha_val, dict):
        return span
    if alpha_val.get("_type") == "value":
        alpha = alpha_val.get("value", 0.1)
        if isinstance(alpha, (int, float)) and alpha > 0:
            span = {"value": round(2.0 / alpha - 1.0)}
    elif alpha_val.get("_type") == "call" and alpha_val.get("func") == "div":
        div_args = alpha_val.get("args", [])
        if len(div_args) == 2:
            a = div_args[0].get("value") if div_args[0].get("_type") == "value" else None
            b = div_args[1].get("value") if div_args[1].get("_type") == "value" else None
            if a is not None and b is not None and b != 0:
                alpha = a / b
                if alpha > 0:
                    span = {"value": round(2.0 / alpha - 1.0)}
    return span


_ROLLING_METHOD_MAP = {
    "mean": "ts_mean",
    "std": "ts_std",
    "sum": "ts_sum",
    "min": "ts_min",
    "max": "ts_max",
    "rank": "ts_rank",
    "corr": "ts_corr",
    "cov": "ts_cov",
    "skew": "ts_skew",
    "kurt": "ts_kurt",
    "var": "ts_var",
    "median": "ts_median",
}

_EWM_METHOD_MAP = {
    "mean": "ewm_mean",
    "std": "ewm_std",
    "corr": "ewm_corr",
}


def _handle_rolling_chain(
    converted_obj: dict,
    method: str,
    args: list,
    convert,
) -> object:
    """Merge a rolling/ewm chain like ``close.rolling(20).mean()``."""
    rolling_type = None
    inner_obj = None
    rolling_span_or_window = None
    if converted_obj.get("_type") == "rolling":
        rolling_type = "rolling"
        inner_obj = converted_obj.get("obj")
        rolling_span_or_window = converted_obj.get("window")
    elif converted_obj.get("op") == "ewm_mean" and converted_obj.get("args"):
        rolling_type = "ewm"
        inner_obj = converted_obj["args"][0] if len(converted_obj["args"]) > 0 else None
        rolling_span_or_window = converted_obj["args"][1] if len(converted_obj["args"]) > 1 else {"value": 20}

    method_map = _ROLLING_METHOD_MAP if rolling_type == "rolling" else _EWM_METHOD_MAP
    op_name = method_map.get(method) if rolling_type else None
    if op_name:
        if inner_obj is None:
            return None
        yaml_args = [inner_obj]
        if method in ("corr", "cov"):
            if args:
                arg_yaml = convert(args[0])
                if arg_yaml is None:
                    return None
                yaml_args.append(arg_yaml)
        yaml_args.append(rolling_span_or_window)
        return {"op": op_name, "args": yaml_args}

    if method == "quantile":
        return {"op": "ts_rank", "args": [inner_obj, rolling_span_or_window]}
    if method == "apply":
        return None
    return _NO_EARLY_RETURN


def _handle_where_method(
    converted_obj: dict,
    args: list,
    convert,
) -> object:
    """Translate ``.where(lambda..., val)`` into a YAML ``where`` node."""
    if not args:
        return _NO_EARLY_RETURN
    arg = args[0]
    if not (isinstance(arg, dict) and arg.get("_type") == "complex"):
        return _NO_EARLY_RETURN
    source = arg.get("source", "")
    import re
    op_map = {"Gt": "gt", "Lt": "lt", "GtE": "gte", "LtE": "lte", "Eq": "eq", "NotEq": "neq"}

    # 位或组合必须优先判定：``(a > 1) | (b > 2)`` 里含两个 Compare，
    # 下面的单条件正则会先命中第一个，**静默丢掉第二个分支和 or 结构**。
    # 那样的 YAML 与原 .py 不等价且毫无报错——故先处理 BitOr；认不出
    # 操作数时宁可拒绝转换（文件记 skipped），也不猜。
    if "op=BitOr()" in source:
        m_or = re.search(
            r"BinOp\(left=Name\(id='(\w+)'.*?op=BitOr\(\).*?right=Name\(id='(\w+)'",
            source,
        )
        if not m_or:
            return _NO_EARLY_RETURN
        return {"op": "where", "args": [
            converted_obj,
            {"op": "or_", "args": [
                {"ref": m_or.group(1)},
                {"ref": m_or.group(2)},
            ]},
            convert(args[1]) if len(args) > 1 else converted_obj,
        ]}

    m = re.search(r"ops=\[(Gt|Lt|GtE|LtE|Eq|NotEq)\(\)\].*?comparators=\[Constant\(value=(\d+\.?\d*)\)\]", source)
    if m:
        cmp_op = op_map.get(m.group(1), "gt")
        return {"op": "where", "args": [
            converted_obj,
            {"op": cmp_op, "args": [converted_obj, {"value": float(m.group(2))}]}
        ]}
    return _NO_EARLY_RETURN


def _convert_method_call(expr: dict, panel_keys: set[str], func_refs: dict, convert) -> dict | None:
    """Convert a ``method_call`` AST node to a YAML node."""
    obj = expr["obj"]
    method = expr["method"]
    args = expr["args"]

    # 特殊处理 np.xxx() / pd.DataFrame() 前置前缀调用
    prefix_result = _convert_prefixed_call(obj, method, args, convert)
    if prefix_result is not _NO_EARLY_RETURN:
        return cast(dict | None, prefix_result)

    # 特殊处理 rolling 方法
    if method == "rolling":
        return {"_type": "rolling", "obj": obj, "window": _extract_window(args)}

    if method == "ewm":
        span = _extract_span(args)
        obj_yaml = convert(obj)
        if obj_yaml is None:
            return None
        return {"op": "ewm_mean", "args": [obj_yaml, span]}

    # 链式调用合并: 先转换 obj，再检查是否为 rolling/ewm
    converted_obj = convert(obj)
    if isinstance(converted_obj, dict):
        result = _handle_rolling_chain(converted_obj, method, args, convert)
        if result is not _NO_EARLY_RETURN:
            return cast(dict | None, result)

    # 特殊处理 .where(lambda/complex, val) -> where(obj, condition, val)
    if method == "where":
        result = _handle_where_method(converted_obj, args, convert)
        if result is not _NO_EARLY_RETURN:
            return cast(dict | None, result)

    # 其他方法调用
    if converted_obj is None:
        return None
    yaml_args = [converted_obj]
    for arg in args:
        if isinstance(arg, dict) and arg.get("_type") == "kwarg":
            continue
        arg_yaml = convert(arg)
        if arg_yaml is None:
            return None
        yaml_args.append(arg_yaml)
    return {"op": method, "args": yaml_args}


def _convert_prefixed_call(obj: dict, method: str, args: list, convert) -> object:
    """Handle np.xxx() and pd.DataFrame() prefix method calls."""
    if not (isinstance(obj, dict) and obj.get("_type") == "ref"):
        return _NO_EARLY_RETURN

    if obj.get("name") == "np":
        yaml_args = []
        for arg in args:
            if isinstance(arg, dict) and arg.get("_type") == "kwarg":
                continue  # 跳过关键字参数
            arg_yaml = convert(arg)
            if arg_yaml is None:
                if method in ("ones_like", "full_like"):
                    continue
                return None
            yaml_args.append(arg_yaml)
        return {"op": method, "args": yaml_args}

    if obj.get("name") == "pd" and method == "DataFrame":
        yaml_args = []
        for arg in args:
            if isinstance(arg, dict) and arg.get("_type") == "kwarg":
                continue
            arg_yaml = convert(arg)
            if arg_yaml is None:
                return None
            yaml_args.append(arg_yaml)
        return {"op": "to_df", "args": yaml_args}

    return _NO_EARLY_RETURN


_REF_HELPER_MAP = {
    "_delay": {"op": "delay"},
    "_make_one": {"op": "fill_null"},
    "_rolling_sum": {"op": "ts_sum"},
    "_rolling_mean": {"op": "ts_mean"},
    "_rolling_std": {"op": "ts_std"},
    "_rolling_var": {"op": "ts_var"},
    "_rolling_min": {"op": "ts_min"},
    "_rolling_max": {"op": "ts_max"},
    "_rolling_rank": {"op": "ts_rank"},
    "_rolling_corr": {"op": "ts_corr"},
    "_rolling_cov": {"op": "ts_cov"},
    "_rolling_prod": {"op": "ts_prod"},
    "_where_ternary": {"op": "where"},
    "_sma": {"op": "ts_mean"},
    "_ind_neutralize": None,
    "_bench_close": None,
    "panel": None,
    "vwap": {"op": "safe_div", "args": [{"column": "amount"}, {"column": "volume"}]},
}


def _convert_ref(name: str, panel_keys: set[str], func_refs: dict) -> dict | None:
    """Convert a ``ref`` AST node to a YAML node."""
    if name == "np":
        return None
    builtin_types = {"float", "int", "bool", "str", "complex", "list", "dict", "set", "tuple"}
    if name in builtin_types:
        return {"value": name}
    if name in func_refs:
        helper_name = func_refs[name]
        return {"op": _HELPER_OP_MAP.get(helper_name, helper_name)}
    if name in _REF_HELPER_MAP:
        return _REF_HELPER_MAP[name]
    return {"ref": name}


def _convert_rolling(expr: dict, convert) -> dict | None:
    """Convert a ``rolling`` node; returns a placeholder merged later."""
    obj = expr.get("obj")
    window = expr.get("window", {"value": 20})
    if obj:
        obj_yaml = convert(obj)
        if obj_yaml is not None:
            return {"_type": "rolling", "obj": obj_yaml, "window": window}
    return None


def _convert_ewm(expr: dict, convert) -> dict | None:
    """Convert an ``ewm`` node to an ``ewm_mean`` op."""
    obj = expr.get("obj")
    span = expr.get("span", {"value": 20})
    if obj:
        obj_yaml = convert(obj)
        if obj_yaml is not None:
            return {"op": "ewm_mean", "args": [obj_yaml, span]}
    return None


def _convert_call(expr: dict, panel_keys: set[str], func_refs: dict, convert) -> dict | None:
    """Convert a ``call`` AST node to a YAML node."""
    func = expr["func"]
    args = expr["args"]

    # 检查是否是函数引用
    if func in func_refs:
        helper_name = func_refs[func]
        op_map = dict(_HELPER_OP_MAP)
        op_map.update({"_ind_neutralize": "__SKIP__", "_bench_close": "__SKIP__"})
        mapped = op_map.get(helper_name)
        if mapped == "__SKIP__":
            yaml_args_temp = []
            for arg in args:
                if isinstance(arg, dict) and arg.get("_type") == "kwarg":
                    continue
                arg_yaml = convert(arg)
                if arg_yaml is not None:
                    yaml_args_temp.append(arg_yaml)
            return yaml_args_temp[0] if yaml_args_temp else None
        elif mapped is not None:
            func = mapped

    yaml_args = _convert_call_args(func, args, convert)
    if yaml_args is None:
        return None

    func, early = _apply_call_special(func, yaml_args)
    if early is not _NO_EARLY_RETURN:
        return cast(dict | None, early)
    func = _apply_call_rename(func) or func
    return {"op": func, "args": yaml_args}


_ALLOW_NONE_FUNCS = ("vwap", "ones_like", "full_like", "_bench_close", "_ind_neutralize")


def _convert_call_args(func: str, args: list, convert) -> list | None:
    """Convert non-kwarg, non-panel args for a call node; None if any is un-convertible."""
    yaml_args = []
    for arg in args:
        if isinstance(arg, dict) and arg.get("_type") == "kwarg":
            continue
        if isinstance(arg, dict) and arg.get("_type") == "ref" and arg.get("name") == "panel":
            continue
        yaml_arg = convert(arg)
        if yaml_arg is None:
            if func in _ALLOW_NONE_FUNCS:
                continue
            return None
        yaml_args.append(yaml_arg)
    return yaml_args


class ComputeFunctionAnalyzer(ast.NodeVisitor):
    """分析 compute() 函数的 AST，提取操作序列。"""

    def __init__(self):
        self.panel_refs: dict[str, str] = {}  # variable_name -> panel_key
        self.assignments: dict[str, Any] = {}  # variable_name -> expression
        self.return_expr: Any = None
        self.complexity = 0  # 0=simple, 1=medium, 2=complex

    def visit_Assign(self, node):
        """处理赋值语句: x = some_expression"""
        if len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                var_name = target.id
                value = self._analyze_expr(node.value)
                # 处理变量重赋值: 创建新步骤名
                if var_name in self.assignments:
                    i = 2
                    while f"{var_name}_{i}" in self.assignments:
                        i += 1
                    new_name = f"{var_name}_{i}"
                    self.assignments[new_name] = value
                else:
                    self.assignments[var_name] = value
        self.generic_visit(node)

    def _post_process_refs(self):
        """后处理: 将重赋值变量链化。

        例如: inner=a, inner_2=b(inner), inner_3=c(inner_2)
        -> 保持原样，因为转换时已经通过序号链化了引用。
        但需要确保 inner_7 引用的是 inner_6，不是 inner_7 本身。
        """
        # 找到所有重赋值组 (如 inner, inner_2, inner_3, ...)
        groups = {}  # base_name -> [name, name_2, name_3, ...]
        for var_name in list(self.assignments.keys()):
            if '_' in var_name:
                parts = var_name.rsplit('_', 1)
                if len(parts) == 2 and parts[1].isdigit():
                    base = parts[0]
                    if base not in groups:
                        groups[base] = []
                    groups[base].append(var_name)

        # 对每组重赋值，更新内部引用
        for base, chain in groups.items():
            chain.sort(key=lambda x: int(x.rsplit('_', 1)[1]))
            # 确保第一个重赋值引用 base (原始变量)
            # 确保每个后续重赋值引用前一个
            for i, name in enumerate(chain):
                expr = self.assignments[name]
                # 找到表达式中对 base 的引用，更新为前一个版本
                prev = base if i == 0 else chain[i - 1]
                self._update_refs_in_expr(expr, base, prev)

    def _update_refs_in_expr(self, expr, old_name, new_name):
        """递归更新表达式中的引用。

        必须递归进 list（含嵌套 list）：表达式里 ``args`` 是 list，其元素
        还可以再是 list（例如 where 的三元参数）。只遍历一层会让深层 ref
        保留旧名，重赋值链在那种表达式上会断——链断后 YAML 引用到已被覆盖
        的变量名，因子语义静默错误（2026-11-02 补测发现）。
        """
        if isinstance(expr, dict):
            if expr.get("_type") == "ref" and expr.get("name") == old_name:
                expr["name"] = new_name
            for _key, val in expr.items():
                self._update_refs_in_expr(val, old_name, new_name)
        elif isinstance(expr, list):
            for item in expr:
                self._update_refs_in_expr(item, old_name, new_name)

    def visit_Return(self, node):
        """处理 return 语句"""
        if node.value:
            self.return_expr = self._analyze_expr(node.value)
        self.generic_visit(node)

    def _analyze_expr(self, node) -> Any:
        """分析表达式，返回简化表示。"""
        # panel 引用: panel["close"]
        if isinstance(node, ast.Subscript):
            result = self._analyze_subscript(node)
            if result is not None:
                return result

        # 各类 ast.Call 形式 (方法调用 / 类型转换 / 函数调用)
        if isinstance(node, ast.Call):
            result = self._analyze_call(node)
            if result is not None:
                return result

        # 变量引用: x
        if isinstance(node, ast.Name):
            return {"_type": "ref", "name": node.id}

        # 常量: 20, 0.5
        if isinstance(node, ast.Constant):
            return {"_type": "value", "value": node.value}

        # 一元负: -x
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            operand = self._analyze_expr(node.operand)
            return {"_type": "call", "func": "neg", "args": [operand]}

        # 一元取反: ~x, not x
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Invert, ast.Not)):
            operand = self._analyze_expr(node.operand)
            return {"_type": "call", "func": "not_", "args": [operand]}

        # 二元运算: a + b, a * b
        if isinstance(node, ast.BinOp):
            op_name = _BIN_OP_MAP.get(type(node.op))
            if op_name:
                return {"_type": "call", "func": op_name, "args": [
                    self._analyze_expr(node.left),
                    self._analyze_expr(node.right),
                ]}

        # 比较运算: a < b, a > b
        if isinstance(node, ast.Compare):
            if len(node.ops) == 1 and len(node.comparators) == 1:
                op_name = _CMP_OP_MAP.get(type(node.ops[0]))
                if op_name:
                    return {"_type": "call", "func": op_name, "args": [
                        self._analyze_expr(node.left),
                        self._analyze_expr(node.comparators[0]),
                    ]}

        # 复杂表达式
        self.complexity = max(self.complexity, 2)
        return {"_type": "complex", "source": ast.dump(node)}

    def _analyze_subscript(self, node) -> Any | None:
        """Analyze a ``panel["close"]`` subscript node, or None if not a panel ref."""
        if not (isinstance(node.value, ast.Name) and node.value.id == "panel"):
            return None
        if isinstance(node.slice, ast.Constant):
            return {"_type": "panel_ref", "key": node.slice.value}
        if isinstance(node.slice, ast.Index) and isinstance(node.slice.value, ast.Constant):
            return {"_type": "panel_ref", "key": node.slice.value.value}
        return None

    def _analyze_call(self, node) -> Any | None:
        """分析 ast.Call 节点，返回简化表示或 None（非可识别调用）。"""
        # 方法调用: close.pct_change(fill_method=None)
        if isinstance(node.func, ast.Attribute):
            obj = self._analyze_expr(node.func.value)
            method = node.func.attr
            args = [self._analyze_expr(arg) for arg in node.args]
            # 忽略 fill_method 关键字参数
            for kw in node.keywords:
                if kw.arg != "fill_method":
                    args.append({"_type": "kwarg", "name": kw.arg, "value": self._analyze_expr(kw.value)})
            return {"_type": "method_call", "obj": obj, "method": method, "args": args}

        # float() / int() 类型转换
        if isinstance(node.func, ast.Name) and node.func.id in ("float", "int"):
            if len(node.args) == 1:
                arg = self._analyze_expr(node.args[0])
                # 如果参数是常量，直接返回转换后的值
                if isinstance(arg, dict) and arg.get("_type") == "value":
                    if node.func.id == "float":
                        return {"_type": "value", "value": float(arg["value"])}
                    else:
                        return {"_type": "value", "value": int(arg["value"])}
                # 否则返回参数本身（类型转换在 YAML 中不需要）
                return arg
            return None

        # 函数调用: ts_mean(close, 20)
        func_name = self._get_func_name(node)
        if func_name:
            # 处理关键字参数
            args = [self._analyze_expr(arg) for arg in node.args]
            for kw in node.keywords:
                if kw.arg == "fill_method":
                    continue
                args.append({"_type": "kwarg", "name": kw.arg, "value": self._analyze_expr(kw.value)})
            return {"_type": "call", "func": func_name, "args": args}

        return None

    def _get_func_name(self, node) -> str | None:
        """获取函数调用的名称。"""
        if isinstance(node.func, ast.Name):
            return node.func.id
        # np.xxx() 调用
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "np":
                return node.func.attr
            return node.func.attr
        return None


def analyze_compute_function(py_path: Path) -> dict:
    """分析 .py 文件中的 compute() 函数。

    Returns:
        dict: {
            "meta": {...},
            "analyzer": ComputeFunctionAnalyzer,
            "complexity": int,
            "error": str or None
        }
    """
    try:
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception as e:
        return {"meta": {}, "analyzer": None, "complexity": 2, "error": str(e)}

    # 提取 __alpha_meta__
    meta = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__alpha_meta__":
                    try:
                        meta = ast.literal_eval(node.value)
                    except Exception:
                        pass

    # 查找 compute() 函数
    analyzer = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "compute":
            analyzer = ComputeFunctionAnalyzer()
            analyzer.visit(node)
            break

    if analyzer is None:
        return {"meta": meta, "analyzer": None, "complexity": 2, "error": "No compute() found"}

    analyzer._post_process_refs()
    return {"meta": meta, "analyzer": analyzer, "complexity": analyzer.complexity, "error": None}


# ============================================================
# 表达式转换: Python AST -> YAML AST
# ============================================================

def python_ast_to_yaml_ast(expr: dict, panel_keys: set[str], func_refs: dict = None) -> dict | None:
    """将 Python AST 分析结果转换为 YAML AST。

    Args:
        expr: Python AST 分析结果
        panel_keys: 可用的 panel 键集合
        func_refs: 函数引用映射 (var_name -> helper function name)

    Returns:
        YAML AST 节点，或 None (如果无法转换)
    """
    if func_refs is None:
        func_refs = {}

    # 使用内部函数进行递归调用，自动传递 func_refs
    def _convert(e):
        return python_ast_to_yaml_ast(e, panel_keys, func_refs)
    if expr["_type"] == "panel_ref":
        key = expr["key"]
        if key in panel_keys:
            return {"column": key}
        return None

    if expr["_type"] == "value":
        return {"value": expr["value"]}

    if expr["_type"] == "ref":
        return _convert_ref(expr["name"], panel_keys, func_refs)

    # 处理 rolling/ewm 节点
    if expr["_type"] == "rolling":
        return _convert_rolling(expr, _convert)

    if expr["_type"] == "ewm":
        return _convert_ewm(expr, _convert)

    if expr["_type"] == "method_call":
        return _convert_method_call(expr, panel_keys, func_refs, _convert)

    if expr["_type"] == "call":
        return _convert_call(expr, panel_keys, func_refs, _convert)

    if expr["_type"] == "complex":
        return None

    return None


# ============================================================
# YAML 生成
# ============================================================

def convert_py_to_yaml(py_path: Path) -> dict | None:
    """将 .py 因子转换为 YAML 配置。

    Returns:
        dict: YAML 配置，或 None (如果无法转换)
    """
    result = analyze_compute_function(py_path)
    if result["error"] or result["analyzer"] is None:
        return None

    analyzer = result["analyzer"]
    meta = result["meta"]

    # 提取 panel 键引用
    panel_keys = set()
    for var_name, expr in analyzer.assignments.items():
        if expr["_type"] == "panel_ref":
            panel_keys.add(expr["key"])

    # 收集所有需要的列
    columns_required = meta.get("columns_required", list(panel_keys))

    # 辅助函数映射 (用于识别函数引用)
    helper_names = {
        "_delay", "_make_one", "_rolling_sum", "_rolling_mean", "_rolling_std",
        "_rolling_var", "_rolling_min", "_rolling_max", "_rolling_rank",
        "_rolling_corr", "_rolling_cov", "_rolling_prod", "_where_ternary", "_sma",
        "_ind_neutralize", "_bench_close", "_cross_sectional_zscore",
    }

    # 将 assignments 转换为 steps
    # 首先识别函数引用 (如 rolling_sum = _rolling_sum)
    func_refs = {}  # var_name -> helper function name
    for var_name, expr in analyzer.assignments.items():
        if expr["_type"] == "ref" and expr["name"] in helper_names:
            func_refs[var_name] = expr["name"]

    steps = []
    for var_name, expr in analyzer.assignments.items():
        # 跳过函数引用 (如 rolling_sum = _rolling_sum)
        if var_name in func_refs:
            continue

        yaml_expr = python_ast_to_yaml_ast(
            expr, panel_keys | {k for k in analyzer.assignments}, func_refs
        )
        if yaml_expr is None:
            return None  # 无法转换的复杂表达式

        # 规范化表达式
        yaml_expr = _normalize_yaml(yaml_expr)
        if yaml_expr is None:
            return None

        steps.append({
            "name": var_name,
            "expr": yaml_expr,
        })

    # 将 return 转换为 final
    if analyzer.return_expr is None:
        return None

    final_expr = python_ast_to_yaml_ast(
        analyzer.return_expr, panel_keys | {k for k in analyzer.assignments}, func_refs
    )
    if final_expr is None:
        return None

    # 规范化 final 表达式
    final_expr = _normalize_yaml(final_expr)
    if final_expr is None:
        return None

    # 构建 YAML 配置
    config = {
        "id": meta.get("id", py_path.stem),
        "zoo": meta.get("zoo", py_path.parent.name),
        "nickname": meta.get("nickname", ""),
        "theme": meta.get("theme", []),
        "formula_latex": meta.get("formula_latex", ""),
        "columns_required": columns_required,
        "universe": meta.get("universe", []),
        "frequency": meta.get("frequency", []),
        "decay_horizon": meta.get("decay_horizon", 0),
        "min_warmup_bars": meta.get("min_warmup_bars", 0),
        "steps": steps,
        "final": final_expr,
    }

    return config


def yaml_to_string(config: dict) -> str:
    """将配置转换为 YAML 字符串。"""
    config = _normalize_yaml(config)
    return yaml.dump(
        config,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )


def _normalize_yaml(node):
    """递归规范化 YAML AST 节点，去除内部 _type 标记。"""
    if isinstance(node, dict):
        if "_type" in node:
            return _normalize_typed(node)
        # 规范化 op/args 结构
        result = {}
        for k, v in node.items():
            if k == "args" and isinstance(v, list):
                result[k] = [_normalize_yaml(a) for a in v]
            elif isinstance(v, dict):
                result[k] = _normalize_yaml(v)
            else:
                result[k] = v
        return result
    if isinstance(node, list):
        return [_normalize_yaml(item) for item in node]
    return node


def _normalize_typed(node: dict):
    """Normalize a node carrying an internal ``_type`` marker."""
    t = node["_type"]
    if t == "value":
        return node.get("value", 0)
    if t in ("column", "panel_ref"):
        return {"column": node.get("key", "")}
    if t == "ref":
        return {"ref": node.get("name", "")}
    if t == "call":
        func = node.get("func", "")
        args = [_normalize_yaml(a) for a in node.get("args", [])]
        return {"op": func, "args": args}
    if t == "method_call":
        method = node.get("method", "")
        obj = _normalize_yaml(node.get("obj"))
        args = [_normalize_yaml(a) for a in node.get("args", [])]
        return {"op": method, "args": [obj] + args}
    if t == "kwarg":
        return _normalize_yaml(node.get("value"))
    if t in ("rolling", "ewm", "complex"):
        return None
    return _normalize_yaml({k: v for k, v in node.items() if k != "_type"})


# ============================================================
# 批量转换
# ============================================================

def convert_zoo(zoo_dir: Path, output_dir: Path | None = None) -> dict:
    """转换整个 zoo 目录。

    Args:
        zoo_dir: zoo 目录路径
        output_dir: 输出目录 (默认为 zoo_dir 同级的 yaml 目录)

    Returns:
        dict: {success: int, failed: int, skipped: int, details: [...]}
    """
    if output_dir is None:
        output_dir = zoo_dir.parent / f"{zoo_dir.name}_yaml"

    output_dir.mkdir(parents=True, exist_ok=True)

    stats = {"success": 0, "failed": 0, "skipped": 0, "details": []}

    for py_file in sorted(zoo_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        try:
            config = convert_py_to_yaml(py_file)
            if config is None:
                stats["skipped"] += 1
                stats["details"].append({"file": py_file.name, "status": "skipped"})
                continue

            yaml_str = yaml_to_string(config)
            yaml_file = output_dir / f"{py_file.stem}.yaml"
            yaml_file.write_text(yaml_str, encoding="utf-8")
            stats["success"] += 1
            stats["details"].append({"file": py_file.name, "status": "success"})

        except Exception as e:
            stats["failed"] += 1
            stats["details"].append({"file": py_file.name, "status": "failed", "error": str(e)})

    return stats


def convert_all_zoos(alpha_zoo_dir: Path) -> dict:
    """转换所有 zoo 目录。

    防护栏:
      - 跳过名称以 `_yaml` 结尾的目录, 避免 convert_zoo() 默认追加 `_yaml`
        后下一轮把它当输入再处理, 造成 alpha101 → alpha101_yaml → alpha101_yaml_yaml
        指数增殖 (历史遗留 bug, 见 chore commit)。
      - 跳过名称以 `_` 开头的目录 (保留原行为)。
    """
    all_stats = {}
    skipped = []
    for zoo_dir in sorted(alpha_zoo_dir.iterdir()):
        if not (zoo_dir.is_dir() and not zoo_dir.name.startswith("_")):
            continue
        if zoo_dir.name.endswith("_yaml"):
            print(f"跳过 {zoo_dir.name} (已是转换输出, 不递归)")
            skipped.append(zoo_dir.name)
            continue
        print(f"转换 {zoo_dir.name}...")
        stats = convert_zoo(zoo_dir)
        all_stats[zoo_dir.name] = stats
        print(f"  成功: {stats['success']}, 跳过: {stats['skipped']}, 失败: {stats['failed']}")

    if skipped:
        all_stats["__skipped_yaml_dirs__"] = skipped
    return all_stats


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import sys

    alpha_zoo_dir = Path(__file__).parent / "alpha_zoo"
    if len(sys.argv) > 1:
        alpha_zoo_dir = Path(sys.argv[1])

    print(f"Alpha Zoo 目录: {alpha_zoo_dir}")
    stats = convert_all_zoos(alpha_zoo_dir)

    # 汇总
    total_success = sum(s["success"] for s in stats.values())
    total_failed = sum(s["failed"] for s in stats.values())
    total_skipped = sum(s["skipped"] for s in stats.values())
    print(f"\n总计: 成功 {total_success}, 跳过 {total_skipped}, 失败 {total_failed}")
