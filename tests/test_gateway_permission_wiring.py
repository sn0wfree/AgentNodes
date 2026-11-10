"""W5 · gateway /send 权限接线（V3 §8.2-b 之后发现的第三个装配缺口）。

此前 gateway /send 路径：``permission_evaluator=None``（工具无权限检查）
+ ``build_default_registry()`` 无条件注册 shell / run_bg_command ——
不设防组合：任何登录用户可经 /send 执行任意命令。同期的 /send_async
(SessionService→chat_loop) 有 evaluator（chat_loop 默认接线）+
shell opt-in 闸——两路不同构。

本文件钉住修复后的契约：
1. Gateway 显式 evaluator 透传 controller（直构不传 → None，既有测试路径不变）
2. singleton 默认装配 chat 默认 evaluator（env 覆盖 → L2 兜底）
3. shell / run_bg_command 按同一 env 闸 opt-in（默认剔除）
4. 端到端：deny 判定 → 工具不执行，run 照常完成
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

from agentnodes.kernel.gateway.gateway import Gateway
from agentnodes.kernel.orchestration import (
    EntryPoint,
    SourceKind,
    WorkKind,
    WorkRequest,
)


# ── 假件 ─────────────────────────────────────────────────


@dataclass
class Resp:
    content: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str = "stop"

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class _ProbeTool:
    name = "probe"
    description = "records executions"
    parameters = {}

    executed: list[dict] = []

    def execute(self, **kwargs):
        _ProbeTool.executed.append(dict(kwargs))
        return json.dumps({"status": "ok"})


class _ToolCallClient:
    """第一轮回 probe tool_call，之后回最终答案。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None, **kw):
        self.calls += 1
        if self.calls == 1:
            tc = SimpleNamespace(id="c1", name="probe", arguments={})
            return Resp(tool_calls=[tc])
        return Resp(content="done after deny")


class _MiniRegistry:
    def __init__(self) -> None:
        _ProbeTool.executed = []
        self._tools = {"probe": _ProbeTool()}

    def get(self, name):
        return self._tools.get(name)

    def get_definitions(self):
        return []

    def all_tools(self):
        return list(self._tools.values())

    def check_guards(self, name, arguments):
        return None


def _run_one(gw: Gateway) -> None:
    gw.entry.submit(WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.API_CHAT,
        session_key="chat:s1", payload={"text": "go"},
    ))
    gw.controller.run_pending()


# ── 1. 透传契约 ──────────────────────────────────────────


def test_gateway_passes_evaluator_through():
    sentinel = object()
    gw = Gateway(client=object(), permission_evaluator=sentinel)
    assert gw.controller._permission_evaluator is sentinel


def test_gateway_default_keeps_none():
    """直构 Gateway 不传 → None（既有测试路径零变化）。"""
    gw = Gateway(client=object())
    assert gw.controller._permission_evaluator is None


def test_evaluator_reaches_the_loop_and_blocks_tools():
    """端到端：evaluator 经 controller→loop→ToolContext 生效，deny 拦执行。"""
    from agentnodes.kernel.permission import PermissionEvaluator

    evaluator = PermissionEvaluator(ruleset=[], interactive=False)
    gw = Gateway(
        client=_ToolCallClient(), registry=_MiniRegistry(),
        permission_evaluator=evaluator,
    )
    _run_one(gw)

    # 未匹配规则 → ASK → 非交互 fail-closed deny → probe 从未执行
    assert _ProbeTool.executed == []


# ── 2. singleton 默认装配 ────────────────────────────────


def _fresh_gateway(monkeypatch, **kw):
    from agentnodes.kernel.gateway import singleton

    # 注意：不 delenv AGENTNODES_PERMISSION_MODE——env 覆盖测试要先 setenv；
    # monkeypatch 自带生命周期，测试间不泄漏。
    monkeypatch.delenv("AGENTNODES_WORKSPACE", raising=False)
    singleton.reset_gateway()
    try:
        return singleton.get_gateway(client=object(), **kw)
    finally:
        singleton.reset_gateway()


def test_singleton_wires_default_evaluator(monkeypatch):
    gw = _fresh_gateway(monkeypatch, registry=object())
    ev = gw.controller._permission_evaluator
    assert ev is not None, "singleton must wire the default evaluator"


def test_singleton_env_mode_overrides_preset(monkeypatch):
    from agentnodes.kernel.permission.schema import PermissionMode

    monkeypatch.setenv("AGENTNODES_PERMISSION_MODE", "L0_readonly")
    gw = _fresh_gateway(monkeypatch, registry=object())
    ev = gw.controller._permission_evaluator
    assert ev is not None
    # L0 预设含 write→deny 规则（对照 L2 的 write→allow）
    rules = {(r.permission, r.action.value) for r in ev.rules}
    assert ("write", "deny") in rules


def test_singleton_explicit_evaluator_wins(monkeypatch):
    sentinel = object()
    gw = _fresh_gateway(monkeypatch, registry=object(),
                        permission_evaluator=sentinel)
    assert gw.controller._permission_evaluator is sentinel


# ── 3. shell 工具闸 ──────────────────────────────────────


def _registry_with_shell(monkeypatch):
    """真 build_default_registry（含 shell/run_bg_command）作闸的输入。"""
    from agentnodes.kernel.agent.builtin_tools import build_default_registry

    return build_default_registry()


def test_shell_gated_off_by_default(monkeypatch):
    from agentnodes.kernel.gateway import singleton

    monkeypatch.delenv("AGENTNODES_ALLOW_SHELL_TOOLS", raising=False)
    monkeypatch.delenv("AGENTNODES_ALLOW_SHELL_TOOLS", raising=False)
    registry = _registry_with_shell(monkeypatch)
    singleton._gate_shell_tools(registry)

    assert registry.get("shell") is None
    assert registry.get("run_bg_command") is None
    # 无害工具不受影响
    assert registry.get("read") is not None
    assert registry.get("write") is not None


def test_shell_opt_in_keeps_tools(monkeypatch):
    from agentnodes.kernel.gateway import singleton

    monkeypatch.setenv("AGENTNODES_ALLOW_SHELL_TOOLS", "1")
    registry = _registry_with_shell(monkeypatch)
    singleton._gate_shell_tools(registry)

    assert registry.get("shell") is not None
    assert registry.get("run_bg_command") is not None


def test_singleton_registry_lacks_shell_by_default(monkeypatch):
    """端到端：singleton 默认装配的 registry 不含 shell（opt-in 才有）。"""
    from agentnodes.foundation.llm.config import LLMConfig
    from agentnodes.kernel.gateway import singleton

    class _Cfg:
        max_iterations = 5

    monkeypatch.setattr(
        LLMConfig, "load", classmethod(lambda cls: _Cfg()), raising=False,
    )
    monkeypatch.delenv("AGENTNODES_ALLOW_SHELL_TOOLS", raising=False)
    monkeypatch.delenv("AGENTNODES_ALLOW_SHELL_TOOLS", raising=False)
    monkeypatch.delenv("AGENTNODES_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("AGENTNODES_WORKSPACE", raising=False)
    monkeypatch.delenv("AGENTNODES_WORKSPACE", raising=False)
    singleton.reset_gateway()
    try:
        gw = singleton.get_gateway(client=object())
        assert gw.controller._registry.get("shell") is None
        assert gw.controller._registry.get("run_bg_command") is None
    finally:
        singleton.reset_gateway()
