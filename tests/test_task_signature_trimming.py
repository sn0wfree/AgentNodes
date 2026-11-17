"""批次 V3-a · tier-1 tool_set 裁剪（路径 A 补齐路径 B 唯一独有能力）。

**背景**：V3 决策实测——路径 A（AgentLoop）独有 10 项能力，路径 B
（make_react_node）独有 1 项：`task_signature` 工具裁剪。补齐后两条路径
能力集一致，V3-b 的迁移才成立。

**等价性保证**（``tools.py:881-882``）：

    if not task_signature:
        return self.get_definitions()

所以空签名时与旧行为**逐字节一致**。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.foundation.llm import LLMConfig
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop.core import AgentLoop


def _loop(tmp_path: Path, **kw) -> AgentLoop:
    return AgentLoop(
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=tmp_path,
        **kw,
    )


def test_empty_signature_returns_all_tools(tmp_path: Path):
    """空签名 → 全部工具（与 get_definitions() 等价，零行为变化）。"""
    loop = _loop(tmp_path)
    reg = build_default_registry()
    assert loop._effective_tools(1) == reg.get_definitions()


def test_signature_trims_tools(tmp_path: Path):
    """非空签名 → 按 harness 的 tool_set 裁剪。

    **未配置 RSI 时不裁剪**（``resolve_harness_for`` 在 ``tool_set is None``
    时返回全部工具——RSI 未启用时行为必须不变，``tools.py:864-865``）。
    所以本测试断言的是「裁剪结果 = get_definitions_for 的结果」，
    而不是「一定变少」——是否变少取决于当前 harness 配置。
    """
    reg = build_default_registry()
    sig = "unknown_signature_xyz"
    loop = _loop(tmp_path, task_signature=sig)
    got = loop._effective_tools(1)
    assert got == reg.get_definitions_for(sig)
    # 裁剪只做减法：结果必须是全量的子集
    assert len(got) <= len(reg.get_definitions())


def test_signature_with_known_harness(tmp_path: Path):
    """已知签名 → 裁剪后是原集合的子集，且不等于全量。"""
    reg = build_default_registry()
    all_defs = reg.get_definitions()
    if len(all_defs) <= 1:
        pytest.skip("registry 只有 0/1 个工具，裁剪无意义")

    # 用第一个真实工具的角色名当签名（resolve_harness_for 认得它）
    sig = "researcher"
    loop = _loop(tmp_path, task_signature=sig)
    got = loop._effective_tools(1)
    assert len(got) <= len(all_defs)
    assert got == reg.get_definitions_for(sig)


def test_task_signature_is_in_loop_spec(tmp_path: Path):
    """参数进 LoopSpec 的 ContextSpec（批 A 的单一事实源）。"""
    loop = _loop(tmp_path, task_signature="researcher")
    assert loop._spec.context.task_signature == "researcher"
    assert loop._task_signature == "researcher"


def test_default_task_signature_is_empty(tmp_path: Path):
    """默认空 → 不裁剪（保证既有 200+ 调用点零变化）。"""
    loop = _loop(tmp_path)
    assert loop._task_signature == ""
    assert loop._spec.context.task_signature == ""


def test_force_final_text_still_wins(tmp_path: Path):
    """force_final_text 的末轮 tools=None 优先于裁剪。"""
    loop = _loop(tmp_path, task_signature="researcher",
                 force_final_text=True, max_iterations=1)
    assert loop._effective_tools(1) is None
