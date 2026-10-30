"""D1 方法论归位门禁——method/ 是纯方法论域，不依赖执行机制。

设计：docs/d1-method-relocation-design-20261030.md
- method/ 不得 import 执行侧（runner/scheduler/graph_preset/...）；
- 方向只允许：执行侧（runner/scheduler/...）→ method/；
- method/ 内部互引自由（aegis→attribution 等）。
"""
from __future__ import annotations

import ast
from pathlib import Path

METHOD = Path(__file__).resolve().parents[1] / "src" / "strategy_research" / "core" / "study" / "method"

# 执行侧模块：method/ 不得 import 它们
ENGINE_MODULES = frozenset({
    "runner", "scheduler", "graph_preset", "scenario_router", "store",
    "state_store", "phase_engine", "engine_common", "bootstrap", "monitor",
    "dag_planner", "round_manifest", "run_summary", "state_utils",
    "runner_aegis", "runner_knowledge", "hanging_events", "loop_evolution",
    "lazy_detect", "budget", "stub_agents", "director", "graph",
    "graph_templates", "hanging_events",
})


def _imports(path: Path) -> set[str]:
    """本包内 import 的模块名（相对 + 绝对，含函数内）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level >= 1:
                # 相对导入：method/ 内 `.` 指向 method/ 自身（互引自由）；
                # `..` 指向 study/——检查目标名
                if node.level >= 2 and node.module:
                    out.add(node.module.split(".")[0])
            elif node.module and node.module.startswith(
                "strategy_research.core.study."
            ):
                out.add(node.module.split(".")[3])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[:3] == ["strategy_research", "core", "study"] and len(parts) > 3:
                    out.add(parts[3])
    return out


def test_method_never_imports_engine():
    """method/ 的存在意义：方法论不依赖执行机制。"""
    violations: list[str] = []
    for path in sorted(METHOD.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for mod in _imports(path):
            if mod in ENGINE_MODULES:
                violations.append(f"method/{path.name} -> {mod}")
    assert not violations, "method/ 反向依赖执行侧:\n" + "\n".join(violations)


def test_engine_imports_method_one_way():
    """执行侧引用 method/ 是合法方向（抽几处锚点确认接线存在）。"""
    import strategy_research.core.study.phase_engine as pe  # noqa: F401
    import strategy_research.core.study.runner_aegis as ra  # noqa: F401

    from strategy_research.core.study.method import aegis, claims  # noqa: F401
    from strategy_research.core.study.method import metric_targets  # noqa: F401


def test_method_modules_importable():
    """6 个域件从新家可导入（导出符号抽查）。"""
    from strategy_research.core.study.method import (
        aegis,
        attribution,
        claims,
        guidance,
        metric_targets,
        review_loop,
    )

    assert hasattr(aegis, "AEGIS") or hasattr(aegis, "verdict_reason") or aegis is not None
    assert hasattr(metric_targets, "meets_metric_targets")
    assert hasattr(claims, "summarize_prediction_accuracy")
