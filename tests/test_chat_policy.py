# -*- coding: utf-8 -*-
"""ChatPolicy 漂移守护 + 接线测试（顺序③，裁决 3：2026-11-18）。

裁决 3：apps/chat 拥有产品策略；先保行为再移默认值——builder 默认
（max_iterations=1 / allow_shell_tools=True）与 send_async legacy 默认
（50 / False）是两条路径的既有行为。本文件用 ``inspect.signature``
把两侧签名默认钉在 ``ChatPolicy`` 上：任何一侧改默认都必须显式裁决。
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from agentnodes.apps.chat.policy import ChatPolicy

# ── 漂移守护：签名默认 == ChatPolicy 声明 ────────────────────────────


def test_builder_signature_defaults_match_policy():
    from agentnodes.kernel.agent.factory.chat_loop import build_chat_agent_loop

    sig = inspect.signature(build_chat_agent_loop).parameters
    policy = ChatPolicy.for_builder()
    assert sig["max_iterations"].default == policy.max_iterations == 1
    assert sig["allow_shell_tools"].default == policy.allow_shell_tools is True
    assert sig["enable_goal_injection"].default == policy.enable_goal_injection is False
    assert (sig["enable_claim_validation"].default
            == policy.enable_claim_validation is False)
    assert (sig["strict_claim_validation"].default
            == policy.strict_claim_validation is False)


def test_send_async_signature_defaults_match_policy():
    from agentnodes.kernel.session.service import SessionService

    sig = inspect.signature(SessionService.send_message).parameters
    policy = ChatPolicy.for_send_async()
    assert sig["max_iterations"].default == policy.max_iterations == 50
    assert sig["allow_shell_tools"].default == policy.allow_shell_tools is False


# ── 接线：policy 覆盖 builder 签名默认 ───────────────────────────────


@pytest.fixture
def fake_registry(monkeypatch):
    fake = MagicMock()
    fake._tools = {"shell": object(), "search": object()}

    import agentnodes.kernel.agent.factory.chat_loop as chat_loop_mod
    monkeypatch.setattr(
        chat_loop_mod, "build_default_registry", lambda: fake, raising=False
    )
    # builder 内部是 `from ...builtin_tools import build_default_registry`
    #（函数内 import）——patch 真实来源模块
    import agentnodes.kernel.agent.builtin_tools as bt
    monkeypatch.setattr(bt, "build_default_registry", lambda: fake)
    return fake


@pytest.fixture
def fake_config():
    cfg = MagicMock()
    cfg.compact_config = MagicMock()
    return cfg


def test_policy_overrides_builder_defaults(fake_config, fake_registry):
    from agentnodes.kernel.agent.factory.chat_loop import build_chat_agent_loop

    policy = ChatPolicy(max_iterations=7, allow_shell_tools=True,
                        enable_goal_injection=True)
    loop = build_chat_agent_loop(config=fake_config, session_id="s1", policy=policy)
    assert loop.max_iterations == 7
    assert loop.enable_goal_injection is True
    # allow_shell_tools=True → shell 保留在 registry
    assert "shell" in loop.registry._tools


def test_policy_false_removes_shell_from_registry(fake_config, fake_registry):
    from agentnodes.kernel.agent.factory.chat_loop import build_chat_agent_loop

    policy = ChatPolicy(max_iterations=1, allow_shell_tools=False)
    loop = build_chat_agent_loop(config=fake_config, session_id="s1", policy=policy)
    assert "shell" not in loop.registry._tools


def test_no_policy_keeps_current_defaults(fake_config, fake_registry):
    """不传 policy → 行为与签名默认一致（零行为变化的回归锚）。"""
    from agentnodes.kernel.agent.factory.chat_loop import build_chat_agent_loop

    loop = build_chat_agent_loop(config=fake_config, session_id="s1")
    assert loop.max_iterations == 1
    # builder 默认 allow_shell_tools=True → shell 在 registry
    assert "shell" in loop.registry._tools
