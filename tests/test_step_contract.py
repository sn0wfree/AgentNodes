"""B2-① §7 验收断言机检化（契约网）。

把设计文档 ``docs/design/step-protocol-migration-20261112.md`` §7 的
断言变成可执行网——**不靠"跑绿了"**，靠可 grep / 可 AST 判定的事实。

**为什么用 AST 而不是字符串计数**：``async_mode`` 这类标识符在 docstring
与注释里大量出现（描述迁移历史），字符串计数会把文档提及当成代码引用
——实测踩过两次（§7 断言 13 的判据错误、T6 注入网被自身 docstring 误伤）。
AST 只认**代码节点**：形参 / 名字 / 属性 / 关键字实参。

**分组**：

- **绿组**：迁移后已达成的契约（当前就该成立）
- **xfail 组**：T11 删除工序的目标态——用 ``strict=True`` 钉住，
  T11 达成后变 XPASS **强制**移除 marker（R-010：不提交红测试）
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/agentnodes/kernel/agent"
STRATEGY = SRC / "strategy"


# ── AST 辅助 ────────────────────────────────────────────────────────


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in str(p))


def _async_mode_refs(path: Path) -> list[tuple[int, str]]:
    """``async_mode`` 的**代码**引用（形参/名字/属性/关键字实参）。

    docstring 与注释不在 AST 里 → 天然排除（这正是要用 AST 的理由）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.arg == "async_mode":
            out.append((node.lineno, "arg"))
        elif isinstance(node, ast.Name) and node.id == "async_mode":
            out.append((node.lineno, "name"))
        elif isinstance(node, ast.Attribute) and node.attr == "async_mode":
            out.append((node.lineno, "attr"))
        elif isinstance(node, ast.keyword) and node.arg == "async_mode":
            out.append((node.lineno, "kwarg"))
    return out


def _defs(path: Path, name: str) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    ]


def _protocol_classes(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id == "Protocol":
                    out.append(node.name)
    return out


# ══════════════════════════════════════════════════════════════════
# 绿组：迁移后已达成的契约
# ══════════════════════════════════════════════════════════════════


def test_strategy_layer_has_zero_async_mode_code_refs():
    """§7 断言 2：step 层零 ``async_mode`` 代码引用。

    当前 8 处字符串命中**全在 docstring/注释**（描述迁移历史）→ AST 判定为 0。
    """
    refs = []
    for p in _py_files(STRATEGY):
        refs += [(p, *r) for r in _async_mode_refs(p)]
    assert not refs, f"step 层仍有 async_mode 代码引用: {refs}"


def test_strategy_layer_has_zero_execute_defs():
    """§7 断言 4：``strategy/`` 下无 ``def execute(``（7 个类全改 handle）。"""
    hits = []
    for p in _py_files(STRATEGY):
        hits += [(p.name, ln) for ln in _defs(p, "execute")]
    assert not hits, f"strategy/ 仍有 execute 实现: {hits}"


def test_protocol_py_has_exactly_four_decision_protocols():
    """§7 断言 5：``protocol.py`` 只剩 4 枚判定型 Protocol。"""
    names = _protocol_classes(STRATEGY / "protocol.py")
    assert sorted(names) == [
        "ContinuationStep", "ProgressStep", "ResilienceStep", "StopStep",
    ], f"判定型 Protocol 应恰 4 枚，实际: {names}"


def test_call_step_signature_has_no_async_mode():
    """§7 断言 3：``_call_step`` 签名无 ``async_mode``。"""
    tree = ast.parse((SRC / "loop.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_call_step":
            args = [a.arg for a in node.args.args + node.args.kwonlyargs]
            assert "async_mode" not in args, f"_call_step 形参残留: {args}"
            return
    pytest.fail("_call_step 未找到")


def test_call_sites_pass_no_async_mode_kwarg():
    """§7 断言 3（续）：8 个调用点都不再传 ``async_mode=``。"""
    hits = []
    for p in (SRC / "loop.py", SRC / "loop_engine_adapters.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_call_step"):
                for kw in node.keywords:
                    if kw.arg == "async_mode":
                        hits.append((p.name, node.lineno))
    assert not hits, f"_call_step 调用仍传 async_mode: {hits}"


def test_all_seven_step_classes_expose_handle():
    """7 个生产 step 类各有 ``handle``（compaction / llm_call /
    tool_execution / finalization / pre_run + 2 个 custom step）。"""
    from agentnodes.kernel.agent.strategy.custom_steps import (
        ClaimValidationFinalizationStep,
        NoOpToolExecutionStep,
    )
    from agentnodes.kernel.agent.strategy.steps import (
        DefaultCompactionStep,
        DefaultFinalizationStep,
        DefaultLLMCallStep,
        DefaultPreRunStep,
        DefaultToolExecutionStep,
    )

    classes = [
        DefaultCompactionStep, DefaultLLMCallStep, DefaultToolExecutionStep,
        DefaultFinalizationStep, DefaultPreRunStep,
        ClaimValidationFinalizationStep, NoOpToolExecutionStep,
    ]
    missing = [c.__name__ for c in classes if not callable(getattr(c, "handle", None))]
    assert not missing, f"缺 handle 的 step 类: {missing}"


def test_crash_backfill_count_is_four():
    """§7 断言 7：4 个读 stop 通道的调用点显式回填崩溃信号。

    形状：``stop_reason = <ctx>.stop_reason or "step_<name>"``——
    业务写优先（"error"/"llm_none"），崩溃才落 step_<name>。
    """
    pattern = re.compile(r'stop_reason\s*=\s*\w+\.stop_reason\s+or\s+"step_')
    total = 0
    for p in (SRC / "loop.py", SRC / "loop_engine_adapters.py"):
        hits = pattern.findall(p.read_text(encoding="utf-8"))
        assert len(hits) == 2, f"{p.name} 回填处应恰 2 处，实际 {len(hits)}"
        total += len(hits)
    assert total == 4, f"回填总数应 4，实际 {total}"


def test_finished_reason_has_eight_literals():
    """§7 断言 11：``finished_reason`` 的 8 种字面量一个不减。"""
    r = subprocess.run(
        ["git", "grep", "-n", "-I", "-E", r'finished_reason\s*=\s*"[a-z_]+"',
         "--", "src"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT),
    )
    vals = {
        m.group(1)
        for m in re.finditer(r'finished_reason\s*=\s*"([a-z_]+)"', r.stdout)
    }
    assert len(vals) == 8, f"finished_reason 字面量应 8 种，实际 {len(vals)}: {vals}"


def test_step_result_events_still_empty():
    """B2-① 边界：``StepResult.events`` 恒空（事件收编属 B2-②）。"""
    from agentnodes.kernel.agent._core_types import StepResult

    assert StepResult.ok().events == []
    assert StepResult.fail("x").events == []


# ══════════════════════════════════════════════════════════════════
# T11 目标态断言（2026-11-14 达成，strict xfail 已转正常绿断言）
# ══════════════════════════════════════════════════════════════════


def test_should_run_removed_from_src():
    """§7 断言 6：``def should_run`` = 0（T11 达成）。"""
    hits = []
    for p in _py_files(SRC):
        hits += [(p.name, ln) for ln in _defs(p, "should_run")]
    assert not hits, f"should_run 残留: {hits}"


def test_legacy_step_adapter_removed_from_src():
    """§7 断言 8：``LegacyStepAdapter`` = 0（T11 达成）。"""
    r = subprocess.run(
        ["git", "grep", "-n", "-I", "-F", "LegacyStepAdapter", "--", "src"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT),
    )
    assert not r.stdout.strip(), "LegacyStepAdapter 残留"


def test_step_adapters_has_zero_async_mode_refs():
    """§7 断言 2（续）：``step_adapters.py`` 文件已退役（T11 删除）。"""
    assert not (SRC / "step_adapters.py").exists(), "step_adapters.py 残留"


def test_call_step_has_no_legacy_execute_branch():
    """T11：``_call_step`` 的旧形状 ``step.execute(...)`` 分支删除。"""
    tree = ast.parse((SRC / "loop.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_call_step":
            calls = [
                n.func.attr
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            ]
            assert "execute" not in calls, f"_call_step 仍有 execute 分支: {calls}"
            return
    pytest.fail("_call_step 未找到")
