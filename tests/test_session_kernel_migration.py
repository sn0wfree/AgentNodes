"""会话生命周期下沉 —— 双入口同源契约测试（批次 A / B1 的固化网）。

**为什么需要这份测试**：迁移期间新旧两条 import 路径并存。
如果哪天有人「顺手」把垫片改成复制一份实现（而不是 re-export），
两个路径就会返回**不同的类对象** —— 类型检查失效、isinstance 判断失败、
且不会有任何现有测试报警。这份测试钉住「同源」这件事。

对应方案：docs/design/session-lifecycle-kernel-migration-20261116.md §4 批次 A / B1
"""

from __future__ import annotations

import importlib
import inspect

import pytest

#: 已下沉的模块 → 该模块的公开符号（新增符号时同步这里）
SUNK_MODULES = {
    # ── 批次 A：零依赖词汇表 ──
    "models": [
        "Attempt", "AttemptId", "AttemptStatus", "Message",
        "MessageId", "Session", "SessionId", "SessionStatus", "_utc_now_iso",
    ],
    "projection_models": ["ProjectedPart", "ProjectedMessage", "ProjectedSession"],
    "part_accumulate": [
        "_accumulate_part", "_accumulate_text_started", "_accumulate_text_delta",
        "_accumulate_text_ended", "_build_tool_call_part", "_accumulate_tool_result",
        "_accumulate_thinking_delta",
    ],
    # ── 批次 B1：叶子下沉（2026-11-16）──
    "history_convert": [
        "_build_tool_indexes", "_convert_one_history_message", "_decide_kept_compactions",
        "_defer_or_drop_tool_message", "_extract_tool_calls", "_find_compaction_indices",
        "MAX_HISTORY_CHARS", "_HistoryConvertContext",
    ],
    "projection_handlers": [
        "apply_event", "HANDLERS", "_on_message_received", "_on_tool_result",
        "_on_compact", "_on_goal_updated",
    ],
    "projection_store": ["ProjectionStore"],
    "trace_projection": ["TraceProjection"],
    "backfill_event_log": [
        # `main` 是 argparse CLI 入口，不进包级公共 API（垫片仍转出它）
        "backfill_event_log", "_generate_events_for_session",
    ],
}


def _pair(module: str):
    """返回 (kernel 路径模块, L1 垫片模块)。"""
    new = importlib.import_module(f"agentnodes.kernel.session.{module}")
    old = importlib.import_module(f"agentnodes.interfaces.api.session.{module}")
    return new, old


@pytest.mark.parametrize("module", sorted(SUNK_MODULES))
def test_shim_reexports_same_objects(module: str):
    """垫片必须是 re-export，不是复制 —— 每个符号都是同一个对象。"""
    new, old = _pair(module)
    for name in SUNK_MODULES[module]:
        assert hasattr(new, name), f"kernel 侧缺符号 {name}"
        assert hasattr(old, name), f"L1 垫片缺符号 {name}"
        assert getattr(new, name) is getattr(old, name), (
            f"{module}.{name} 在两个路径上不是同一对象 —— "
            f"垫片被改成了复制，双入口同源已破裂"
        )


@pytest.mark.parametrize("module", sorted(SUNK_MODULES))
def test_symbols_belong_to_kernel_module(module: str):
    """可调用符号的 __module__ 必须指向 kernel —— 证明实现真的在 L3。

    只查类/函数：常量（如 ``MAX_HISTORY_CHARS`` / ``HANDLERS``）没有
    ``__module__`` 属性，查它们会得到 None。常量的归属由身份同一性
    （``test_shim_reexports_same_objects``）覆盖。
    """
    new, _ = _pair(module)
    for name in SUNK_MODULES[module]:
        obj = getattr(new, name)
        if not (inspect.isclass(obj) or inspect.isfunction(obj)
                or inspect.isbuiltin(obj)):
            continue
        owner = getattr(obj, "__module__", None)
        assert owner is not None, f"{module}.{name} 没有 __module__"
        assert owner.startswith("agentnodes.kernel.session"), (
            f"{module}.{name}.__module__ = {owner!r}，实现不在 kernel"
        )


def _defined_names(module) -> set[str]:
    """模块**自己定义**的顶层名字（AST 推导，不含 import 进来的名字）。

    ``dir()`` 会包含 ``Optional`` / ``json`` / ``logging`` 这类被 import 的名字，
    用它当基线会让断言永远失败。
    """
    import ast
    from pathlib import Path

    src = Path(module.__file__).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    names = set()
    for n in tree.body:
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            for tg in n.targets:
                if isinstance(tg, ast.Name) and tg.id != "__all__":
                    names.add(tg.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
    return names


def test_kernel_session_package_exports_public_names():
    """kernel.session 包的导出面能取到全部已下沉符号的**公有**名。

    只查公有：私有名（``_convert_one_history_message`` 等）属实现细节，
    不是包的公共 API；它们由 ``test_every_top_level_name_is_reexported``
    在模块级保证（垫片必须转出）。
    """
    pkg = importlib.import_module("agentnodes.kernel.session")
    missing = []
    for module, names in SUNK_MODULES.items():
        for name in names:
            if name.startswith("_"):
                continue
            if not hasattr(pkg, name):
                missing.append(f"{module}.{name}")
    assert not missing, f"agentnodes.kernel.session 未导出: {missing}"


@pytest.mark.parametrize(
    "consumer",
    [
        "agentnodes.interfaces.api.session.store",
        "agentnodes.interfaces.api.session.service",
        "agentnodes.interfaces.api.session.projector",
    ],
)
def test_l1_consumers_still_import(consumer: str):
    """依赖旧路径的 L1 行为模块仍能 import（迁移期间不能断链）。"""
    importlib.import_module(consumer)


@pytest.mark.parametrize("module", sorted(SUNK_MODULES))
def test_every_top_level_name_is_reexported(module: str):
    """垫片必须转出 kernel 模块的**全部**顶层名字（含私有）。

    SUNK_MODULES 是「消费方用到的核心符号」清单；这条更严 —— 它按 kernel 模块的
    实际内容推导，防的是「新增符号忘了加进垫片」这种静默漂移。
    """
    new, old = _pair(module)
    defined = _defined_names(new)
    missing = []
    for name in defined:
        if not hasattr(old, name):
            missing.append(name)
        elif getattr(new, name) is not getattr(old, name):
            missing.append(f"{name}(非同一对象)")
    assert not missing, (
        f"{module} 的顶层符号未被垫片完整转出: {sorted(missing)}"
    )
