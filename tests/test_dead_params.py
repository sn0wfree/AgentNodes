"""批次 13 附录 §3/§4 · 配置项是否真的生效（P1 回归）。

**由来**：排查发现两个「配置谎报」——

- ``retry_policy``：``loop.py`` 赋值后**全仓零读取点**，工具重试用硬编码
  ``_TOOL_MAX_RETRIES = 2``。传 ``RetryPolicy(max_retries=5)`` 无效。
- ``enable_hypothesis_auto_create``：默认 **True**，但
  ``resolve("hypothesis.auto_creator")`` 每次都抛 ``CapabilityNotFound``
  （capability 随 hypothesis 子系统归档），异常被 ``try/except`` 吞掉。

两项的共同特征：**参数在，消费点不在**，且没有任何测试断言它会生效。
"""
from __future__ import annotations

import json
from pathlib import Path

from agentnodes.foundation.llm import LLMConfig
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.guards.circuit_breaker import RetryPolicy
from agentnodes.kernel.agent.loop.core import AgentLoop


def _make_loop(**kw):
    base = dict(config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry())
    base.update(kw)
    return AgentLoop(**base)


# ── retry_policy 接线 ───────────────────────────────────────────────


def test_retry_policy_attribute_is_consumed():
    """显式传入的 ``RetryPolicy.max_retries`` 必须真的控制重试次数。"""
    loop = _make_loop(retry_policy=RetryPolicy(max_retries=5))

    assert loop._retry_policy.max_retries == 5
    assert loop._retry_policy_explicit is True, (
        "未标记为显式传入 → loop_tools 不会采用它"
    )


def test_retry_policy_default_keeps_legacy_attempts():
    """未显式传时保持既有 2 次重试（不因默认 RetryPolicy(3) 而改变行为）。"""
    loop = _make_loop()
    assert loop._retry_policy_explicit is False, (
        "默认 policy 被误标为显式，会悄悄把重试 2 次改成 3 次"
    )


def test_retry_policy_explicit_changes_attempt_count(tmp_path: Path):
    """端到端：显式 max_retries 改变实际尝试次数。

    用一个总是抛瞬态错误的自定义工具数调用次数。
    """
    from agentnodes.kernel.agent.tools.core import (
        TRANSIENT_TOOL_ERRORS, BaseTool, ToolRegistry,
    )

    calls = {"n": 0}

    class FlakyTool(BaseTool):
        name = "flaky_probe"
        description = "always raises a transient error"

        @property
        def is_readonly(self) -> bool:
            return True

        def execute(self, **kwargs):  # noqa: ANN003
            calls["n"] += 1
            exc_cls = next(iter(TRANSIENT_TOOL_ERRORS))
            raise exc_cls("boom")

    def _run_with(retry_policy, ws):
        calls["n"] = 0
        reg = ToolRegistry()
        reg.register(FlakyTool())
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"), registry=reg,
            workspace=ws, max_iterations=1,
            retry_policy=retry_policy,
        )
        from agentnodes.kernel.agent.loop.core import LoopResult
        from agentnodes.foundation.llm import ToolCall
        tc = ToolCall(id="c1", name="flaky_probe", arguments={})
        loop._run_coro_sync = None  # noqa: F841 — 仅确保属性访问不炸
        # 直接走 sync 核心（避免 LLM mock）
        loop._execute_tool_call_core_sync = None  # noqa: F841
        import asyncio
        asyncio.run(loop._execute_tool_call_core(
            tc, LoopResult(), async_mode=True,
        ))
        return calls["n"]

    # 显式 5 次
    assert _run_with(RetryPolicy(max_retries=5, base_delay=0.001), tmp_path) == 5

    # 未显式传 → 既有 2 次
    assert _run_with(None, tmp_path) == 2


# ── hypothesis 开关不再谎报 ─────────────────────────────────────────


def test_hypothesis_auto_create_defaults_off():
    """默认关闭——capability 归档期间该开关不可能生效。

    修复前默认 True，调用方以为 hypothesis 会自动创建，
    实际每次都抛 ``CapabilityNotFound`` 并被吞掉。
    """
    loop = _make_loop()
    assert loop.enable_hypothesis_auto_create is False, (
        "默认仍为 True，但 hypothesis.auto_creator 未注册 → 开关谎报"
    )


def test_hypothesis_capability_is_absent():
    """前提验证：该 capability 确实未注册（否则上一条的默认值应保持 True）。"""
    import agentnodes.apps.capabilities  # noqa: F401
    from agentnodes.kernel.agent.capability import (
        CapabilityNotFound, resolve,
    )

    try:
        resolve("hypothesis.auto_creator")
    except CapabilityNotFound:
        return
    raise AssertionError(
        "hypothesis.auto_creator 已注册——请把 "
        "enable_hypothesis_auto_create 的默认值翻回 True 并更新本测试"
    )
