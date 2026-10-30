"""研究域内无循环导入门禁（docs/break-circular-import-design-20261030.md）。

背景：`phase_engine ↔ runner` 曾互为 import。环的实质是 4 个纯数据
符号（常量 + 停因枚举）住在 runner.py，而 phase_engine 是 runner 的
下游——两者互为上下游。

解法：符号下沉 `study/contracts.py`，依赖变成 DAG：
    runner → phase_engine → contracts
    runner → engine_common → contracts

本测试用 AST 检查（含函数内 import），比「import 能跑通」更强——
后者在有 `from __future__ import annotations` 或延迟 import 时可能假通过。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

STUDY = Path(__file__).resolve().parents[1] / "src" / "strategy_research" / "core" / "study"

# 引擎相关模块（解环的当事方 + 新契约模块）
ENGINE_MODULES = ("runner", "phase_engine", "engine_common", "contracts")


def _module_imports(path: Path) -> set[str]:
    """该文件 import 的所有本包内模块名（含函数内、相对导入）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            # 相对导入 node.level>0：module 是相对路径（如 "runner"）
            out.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "strategy_research" and len(parts) > 3:
                    out.add(parts[3])
    return out


def test_engine_modules_have_no_cycles():
    """runner/phase_engine/engine_common/contracts 之间的 import 图无环。"""
    edges: dict[str, set[str]] = {}
    for name in ENGINE_MODULES:
        deps = _module_imports(STUDY / f"{name}.py")
        edges[name] = {d for d in deps if d in ENGINE_MODULES and d != name}

    # DFS 找环
    def find_cycle() -> list[str] | None:
        for start in edges:
            stack = [(start, [start])]
            while stack:
                node, path = stack.pop()
                for nxt in edges.get(node, ()):
                    if nxt == start:
                        return path + [nxt]
                    if nxt not in path:
                        stack.append((nxt, path + [nxt]))
        return None

    cycle = find_cycle()
    assert cycle is None, f"域内循环导入: {' -> '.join(cycle or [])}"


def test_phase_engine_never_imports_runner():
    """解环的核心断言：phase_engine 不得 import runner（任何形式）。

    它需要的一切已下沉 contracts。若将来又加回 import，说明有人把新的
    依赖塞进了 runner 而不肯下沉——本测试会挡住。
    """
    deps = _module_imports(STUDY / "phase_engine.py")
    assert "runner" not in deps, (
        "phase_engine 仍在 import runner——新增的共享符号请下沉 contracts"
    )


def test_engine_common_never_imports_runner():
    deps = _module_imports(STUDY / "engine_common.py")
    assert "runner" not in deps


def test_contracts_is_a_leaf():
    """contracts 不得 import 任何域内实现模块（它是最底层词汇表）。"""
    deps = _module_imports(STUDY / "contracts.py")
    forbidden = {"runner", "phase_engine", "engine_common", "scheduler", "store"}
    assert not (deps & forbidden), f"contracts 反向依赖实现模块: {deps & forbidden}"


# ── 搬迁等价：旧路径仍可用，值不变 ──────────────────────


def test_runner_reexports_contract_symbols():
    """旧路径 `from .runner import ShutdownReason` 等继续可用（re-export）。"""
    from strategy_research.core.study import runner

    assert runner.ShutdownReason.TARGETS_MET == "targets_met"
    assert runner.SR_AGENT_MAX_ITER == 50
    assert runner.SR_STUDY_MAX_DEVIATION == 3
    assert runner.SR_STUDY_COLLECT_INTERVAL == 5
    assert runner.SR_STUDY_MAX_DISCARD == 5
    assert callable(runner._parse_failed_agents)


def test_contract_values_unchanged():
    """搬迁必须逐字保真——这些值决定等待/重试/停止行为。"""
    from strategy_research.core.study.contracts import (
        SR_AGENT_MAX_ITER,
        SR_AGENT_MAX_PARSE_RETRIES,
        SR_AGENT_NO_PROGRESS_WINDOW,
        SR_AGENT_PARSE_BACKOFF_BASE,
        SR_AGENT_PARSE_BACKOFF_MAX,
        SR_STUDY_COLLECT_INTERVAL,
        SR_STUDY_MAX_DEVIATION,
        SR_STUDY_MAX_DISCARD,
        ShutdownReason,
    )

    assert SR_STUDY_MAX_DEVIATION == 3
    assert SR_STUDY_COLLECT_INTERVAL == 5
    assert SR_STUDY_MAX_DISCARD == 5
    assert SR_AGENT_MAX_ITER == 50
    assert SR_AGENT_NO_PROGRESS_WINDOW == 5
    assert SR_AGENT_MAX_PARSE_RETRIES == 2
    assert SR_AGENT_PARSE_BACKOFF_BASE == 5.0
    assert SR_AGENT_PARSE_BACKOFF_MAX == 30.0
    # 停因枚举完整性（Director 的映射表以此为准）
    expected = {
        "TARGETS_MET": "targets_met",
        "MAX_ROUNDS": "max_rounds",
        "STAGNATION": "stagnation",
        "BUDGET": "budget_exceeded",
        "CANCELLED": "cancelled",
        "PAUSED": "paused",
        "ERROR": "error",
        "EARLY_STOPPED": "early_stopped",
        "NOVELTY_REJECTED": "novelty_rejected",
        "REPEATED_DEVIATION": "repeated_deviation",
        "REVIEW_FAILED": "review_failed",
        "DISCARD_STREAK": "stagnation_discard_streak",
        "NEEDS_REFRESH": "needs_refresh",
    }
    for attr, value in expected.items():
        assert getattr(ShutdownReason, attr) == value, attr


def test_parse_failed_agents_behaviour():
    from strategy_research.core.study.contracts import parse_failed_agents

    assert parse_failed_agents({"strategist_output": {"error": "parse_failed"}}) == [
        "strategist"
    ]
    assert parse_failed_agents({"strategist_output": {"ok": 1}}) == []
    assert parse_failed_agents({}) == []


def test_same_object_across_both_paths():
    """runner 的 re-export 与 contracts 必须是同一对象（不能各拷一份）。"""
    from strategy_research.core.study import contracts, runner

    assert runner.ShutdownReason is contracts.ShutdownReason
    assert runner.SR_AGENT_MAX_ITER == contracts.SR_AGENT_MAX_ITER
    assert runner._parse_failed_agents is contracts.parse_failed_agents
