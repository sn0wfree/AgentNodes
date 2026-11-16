"""会话生命周期下沉 —— 双入口同源契约测试（批次 A 的固化网）。

**为什么需要这份测试**：迁移期间新旧两条 import 路径并存。
如果哪天有人「顺手」把垫片改成复制一份实现（而不是 re-export），
两个路径就会返回**不同的类对象** —— 类型检查失效、isinstance 判断失败、
且不会有任何现有测试报警。这份测试钉住「同源」这件事。

对应方案：docs/design/session-lifecycle-kernel-migration-20261116.md §4 批次 A（A2/A6）
"""

from __future__ import annotations

import importlib

import pytest

#: 已下沉的模块 → 该模块的公开符号（新增符号时同步这里）
SUNK_MODULES = {
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
    """符号的 __module__ 必须指向 kernel —— 证明实现真的在 L3。"""
    new, _ = _pair(module)
    for name in SUNK_MODULES[module]:
        obj = getattr(new, name)
        owner = getattr(obj, "__module__", None)
        assert owner is not None, f"{module}.{name} 没有 __module__"
        assert owner.startswith("agentnodes.kernel.session"), (
            f"{module}.{name}.__module__ = {owner!r}，实现不在 kernel"
        )


def test_kernel_session_package_exports_public_names():
    """kernel.session 包的导出面能取到全部已下沉符号。"""
    pkg = importlib.import_module("agentnodes.kernel.session")
    for module, names in SUNK_MODULES.items():
        for name in names:
            assert hasattr(pkg, name), f"agentnodes.kernel.session 未导出 {module}.{name}"


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
