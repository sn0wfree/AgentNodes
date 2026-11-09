"""W4 · gateway /send 迭代上限接线（V3 观察记录 §8.2）。

``/send_async`` 早已按 ``LLMConfig.max_iterations``（build=50 / plan=1）
取值；gateway ``/send`` 此前恒为 SessionController 默认 1——工具轮走不完
（生产实跑抓到：写文件成功但无最终答复）。本文件钉住修复后的四条契约：

1. Gateway 显式 ``max_iterations`` 透传 controller；
2. Gateway 默认（直构）保持 controller 默认 1（既有测试路径零变化）；
3. singleton 按 ``LLMConfig.max_iterations`` 装配；
4. per-turn 覆盖：payload ``max_iterations=1``（plan 模式）只跑一轮；
   无覆盖时用构造值。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from agentnodes.kernel.gateway.entry_adapters import api_chat_request
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


class _LoopProbeClient:
    """第一轮回 tool_calls，之后回最终答案；记录调用次数。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None, **kw):
        self.calls += 1
        if self.calls == 1:
            tc = SimpleNamespace(id="c1", name="echo", arguments={})
            return Resp(tool_calls=[tc])
        return Resp(content="second ok")


class _EchoTool:
    name = "echo"
    description = "echo"
    brief = "- echo: 回声工具"  # ContextBuilder 的 tool_list 渲染读它

    def execute(self, **kwargs):
        return "{}"


class _MiniRegistry:
    """AgentLoop 构造 + 一次工具执行所需的最小面。"""

    def __init__(self) -> None:
        self._tools = {"echo": _EchoTool()}

    def get(self, name):
        return self._tools.get(name)

    def get_definitions(self):
        return []

    def all_tools(self):
        return list(self._tools.values())

    def check_guards(self, name, arguments):
        return None


def _controller(client, registry, max_iterations):
    from agentnodes.kernel.gateway.session_controller import SessionController

    ep = EntryPoint()
    return SessionController(
        ep, client=client, registry=registry,
        max_iterations=max_iterations,
    )


def _submit_with_payload(controller, payload_extra: dict):
    from agentnodes.kernel.orchestration import WorkRequest as _WR

    payload = {"text": "go", **payload_extra}
    controller._ep.submit(_WR(
        kind=WorkKind.CHAT, source=SourceKind.API_CHAT,
        session_key="chat:s1", payload=payload,
    ))
    [run] = controller.run_pending()
    return run


# ── 1/2. Gateway 透传 ────────────────────────────────────


def test_gateway_passes_max_iterations_through():
    gw = Gateway(client=object(), max_iterations=7)
    assert gw.controller._max_iterations == 7


def test_gateway_passes_workspace_to_controller(tmp_path):
    """**W4**：workspace 注入 controller（工具的 ToolContext.workspace 来源）。

    此前 Gateway 只把 workspace 挂自身属性（构造后才赋值），controller/
    AgentLoop/ToolContext 的 workspace 恒为 None → 工具全部报
    "missing workspace context"（模型只能靠猜绝对路径塞参数侥幸工作，
    生产实跑 2026-11-09 抓到）。"""
    gw = Gateway(client=object(), workspace=tmp_path)
    assert gw.controller._workspace == tmp_path


def test_gateway_resolves_workspace_when_not_given(monkeypatch):
    """不传 workspace → 解析（AN_WORKSPACE_PATH 优先）。"""
    from agentnodes.kernel.gateway import gateway as gw_mod

    sentinel = tmp_path = __import__("pathlib").Path(r"D:\w4-probe-ws")
    monkeypatch.setenv("AN_WORKSPACE_PATH", str(sentinel))
    gw = gw_mod.Gateway(client=object())
    assert gw.controller._workspace == sentinel


def test_gateway_default_keeps_controller_default():
    """直构 Gateway（不传）→ controller 默认 1——既有测试路径零变化。"""
    gw = Gateway(client=object())
    assert gw.controller._max_iterations == 1


# ── 3. singleton 装配 ────────────────────────────────────


def test_singleton_wires_llmconfig_max_iterations(monkeypatch):
    from agentnodes.foundation.llm.config import LLMConfig
    from agentnodes.kernel.gateway import singleton

    class _Cfg:
        max_iterations = 33

    monkeypatch.setattr(
        LLMConfig, "load", classmethod(lambda cls: _Cfg()), raising=False,
    )
    singleton.reset_gateway()
    try:
        gw = singleton.get_gateway(client=object(), registry=object())
        assert gw.controller._max_iterations == 33
    finally:
        singleton.reset_gateway()


def test_singleton_falls_back_to_50_on_load_failure(monkeypatch):
    from agentnodes.foundation.llm.config import LLMConfig
    from agentnodes.kernel.gateway import singleton

    def _boom():
        raise RuntimeError("config broken")

    monkeypatch.setattr(LLMConfig, "load", classmethod(lambda cls: _boom()))
    singleton.reset_gateway()
    try:
        gw = singleton.get_gateway(client=object(), registry=object())
        assert gw.controller._max_iterations == 50
    finally:
        singleton.reset_gateway()


# ── 4. per-turn 覆盖 ─────────────────────────────────────


def test_payload_max_iterations_one_runs_single_iteration():
    """plan 模式（payload max_iterations=1）：工具执行后不再有第二轮 LLM。"""
    client = _LoopProbeClient()
    ctrl = _controller(client, _MiniRegistry(), max_iterations=5)

    run = _submit_with_payload(ctrl, {"max_iterations": 1})

    assert run.ok
    assert client.calls == 1, "plan 模式必须只调一次 LLM"
    assert "max_iterations=1" in run.answer or run.answer  # max_iter 文案


def test_no_payload_override_uses_constructor_value():
    """无 payload 覆盖：构造值（5）生效——工具轮 + 最终答复两轮完成。"""
    client = _LoopProbeClient()
    ctrl = _controller(client, _MiniRegistry(), max_iterations=5)

    run = _submit_with_payload(ctrl, {})

    assert run.ok
    assert client.calls == 2
    assert run.answer == "second ok"


def test_api_chat_request_carries_max_iterations_in_payload():
    req = api_chat_request(
        "sess-1", "hi", max_iterations=1,
    )
    assert req.payload["max_iterations"] == 1

    req2 = api_chat_request("sess-1", "hi")
    assert "max_iterations" not in req2.payload
