"""W3 · web chat assistant 占位 seed + gateway 流式接线（体验批）。

三个契约（问题与取证见 docs/design/v3-path-convergence-decision-20261109.md
§8.3 W3 / 决策文档 V3-b 落地实录）：

1. **占位 seed**：前端 `messageReceived` 只在事件带 `assistant_message_id`
   时创建 assistant 占位消息——提交时 run_id 尚不存在，receipt 的 emit 带
   不了；`updateMessage` 对不存在的消息是 no-op → 流式 chunk 全部落空。
   修复：桥在 run.started 补推完整 message_received 形状的 seed（含
   assistant_message_id=run_id + user 字段）。projector 首事件胜出（真实
   user 事件已在途）零副作用；前端幂等。
2. **桥安装时序**：work.enqueued 在 submit 内唤醒消费者——桥晚装会错过
   run.started。`_send_via_gateway` 必须先装桥后 submit（源序断言）。
3. **流式自适应**：`chat._get_gateway` 请求 stream=True；适配器在 client
   无 stream/astream（测试假件）时降级非流式，不炸 AttributeError。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from agentnodes.foundation.llm import LLMConfig
from agentnodes.kernel.agent.loop.core import AgentLoop
from agentnodes.kernel.gateway.session_controller import SessionController


# ── 1. 占位 seed ─────────────────────────────────────────


@pytest.fixture()
def bridge_env(monkeypatch):
    """装一个假 gateway + 收集 _push_event 的桥。"""
    import agentnodes.interfaces.api.routers.chat as chat_mod

    pushed: list[tuple[str, str, dict]] = []

    def fake_push_event(session_id: str, event_type: str, data) -> None:
        payload = data
        if isinstance(payload, str):
            import json as _json

            try:
                payload = _json.loads(payload)
            except Exception:
                payload = {"raw": payload}
        pushed.append((session_id, event_type, payload))

    monkeypatch.setattr(chat_mod, "_push_event", fake_push_event)

    sinks: list = []

    class _FakeGW:
        def add_sink(self, fn):
            sinks.append(fn)
            return lambda: sinks.remove(fn)

    gw = _FakeGW()
    holder: dict[str, str] = {}
    return chat_mod, gw, sinks, holder, pushed


def test_run_started_seeds_assistant_placeholder(bridge_env):
    chat_mod, gw, sinks, holder, pushed = bridge_env

    chat_mod._install_chat_event_bridge(
        gw, "sess-1", holder,
        persist=True,
        seed_user={
            "message_id": "u1", "user_message_id": "u1",
            "role": "user", "content": "hello",
        },
    )
    bridge = sinks[0]
    bridge("run.started", {"run_id": "s1#1:r0"})

    seeds = [p for _, k, p in pushed if k == "message_received"]
    assert len(seeds) == 1
    assert seeds[0]["assistant_message_id"] == "s1#1:r0"
    assert seeds[0]["user_message_id"] == "u1"
    assert seeds[0]["content"] == "hello"
    assert seeds[0]["status"] == "processing"
    # holder 置位 → 后续事件携带 message_id
    assert holder["id"] == "s1#1:r0"
    bridge("tool_call", {"tool": "x"})
    _, _, k, p = ("",) + pushed[-1]
    assert p["message_id"] == "s1#1:r0"


def test_seed_pushed_only_once(bridge_env):
    chat_mod, gw, sinks, holder, pushed = bridge_env
    chat_mod._install_chat_event_bridge(
        gw, "sess-1", holder, persist=True,
        seed_user={"message_id": "u1", "user_message_id": "u1", "role": "user"},
    )
    bridge = sinks[0]
    bridge("run.started", {"run_id": "r0"})
    bridge("run.started", {"run_id": "r0"})
    assert len([1 for _, k, _ in pushed if k == "message_received"]) == 1


def test_no_seed_user_keeps_old_behaviour(bridge_env):
    """不带 seed_user（sync/wait 路径）：holder 照置，但不推 seed——
    保持「失败不落任何行」的既有不变量。"""
    chat_mod, gw, sinks, holder, pushed = bridge_env
    chat_mod._install_chat_event_bridge(gw, "sess-1", holder, persist=False)
    bridge = sinks[0]
    bridge("run.started", {"run_id": "r0"})
    assert holder["id"] == "r0"
    assert pushed == []


def test_seed_missing_run_id_is_noop(bridge_env):
    chat_mod, gw, sinks, holder, pushed = bridge_env
    chat_mod._install_chat_event_bridge(
        gw, "sess-1", holder, persist=True,
        seed_user={"message_id": "u1"},
    )
    sinks[0]("run.started", {})
    assert pushed == []


# ── 2. 桥安装时序（先装桥后 submit）──────────────────────


def test_bridge_installed_before_submit():
    """源序断言：事件桥安装必须出现在 submit 之前——work.enqueued 在
    submit 内唤醒消费者，晚装会错过 run.started。

    3a.3b：编排下沉到 apps/chat.send_turn，断言移到新模块源码
    （_event_bridge 调用先于 gw.entry.submit）；语义不变。
    """
    from agentnodes.apps.chat import send_turn as usecase

    src = inspect.getsource(usecase.send_turn)
    install_at = src.find("_event_bridge(")
    submit_at = src.find("gw.entry.submit(")
    assert install_at != -1 and submit_at != -1
    assert install_at < submit_at, "桥必须先于 submit 安装（W3 时序）"


# ── 3. 流式自适应 ────────────────────────────────────────


class _ChatOnly:
    """只有 chat 的假 client（降级路径的最小 witness）。"""

    def chat(self, messages, tools=None, **kw):
        return SimpleNamespace(content="", tool_calls=[], usage=None,
                               finish_reason="stop")


class _FakeRegistry:
    def __init__(self) -> None:
        self._tools: dict = {}

    def get_definitions(self):
        return []

    def all_tools(self):
        return list(self._tools.values())

    def get(self, name):
        return self._tools.get(name)


class _EP:
    def iter_mailboxes(self):
        return []

    def session_id_for(self, key):
        return key


def _loop_of(ctrl: SessionController) -> AgentLoop:
    node = ctrl._build_engine()._fns["react"]
    return next(
        c.cell_contents for c in node.__closure__
        if isinstance(c.cell_contents, AgentLoop)
    )


def test_stream_requested_but_client_chat_only_degrades():
    """controller 要流式 + client 只有 chat → 降级非流式（不炸 AttributeError）。"""
    ctrl = SessionController(_EP(), client=_ChatOnly(), registry=_FakeRegistry(),
                             stream=True)
    loop = _loop_of(ctrl)
    assert loop._stream_mode is False


def test_stream_with_capable_client_stays_on():
    class _StreamCapable(_ChatOnly):
        def stream(self, messages, tools=None, **kw):
            return iter([])

    ctrl = SessionController(_EP(), client=_StreamCapable(), registry=_FakeRegistry(),
                             stream=True)
    loop = _loop_of(ctrl)
    assert loop._stream_mode is True


def test_stream_false_stays_false_even_if_capable():
    class _StreamCapable:
        def chat(self, messages, tools=None, **kw):
            return SimpleNamespace(content="")

        def stream(self, messages, tools=None, **kw):
            return iter([])

    ctrl = SessionController(_EP(), client=_StreamCapable(), registry=_FakeRegistry(),
                             stream=False)
    loop = _loop_of(ctrl)
    assert loop._stream_mode is False


def test_chat_router_requests_streaming_gateway():
    """chat 入口以 stream=True 取 Gateway（W3 流式接线的装配点）。"""
    import agentnodes.interfaces.api.routers.chat as chat_mod

    src = inspect.getsource(chat_mod._get_gateway)
    assert "stream=True" in src
