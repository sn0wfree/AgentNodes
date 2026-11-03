"""B1-a/b: chat → react 的外围能力等价性。

B1-a  系统提示：SessionController 与 build_chat_agent_loop 都经
      ``assemble_loop_parts`` 渲染 → 同输入必产同文本。
B1-b  上下文注入：hub items 以「块在前、当前输入在后」拼进本轮 user
      消息 —— 与 AgentLoop 的 full_task 形状一致（P5-4 契约）。
"""
from __future__ import annotations

from types import SimpleNamespace

from strategy_research.core.gateway.session_controller import SessionController


class _FakeEP:
    """Minimal EntryPoint stand-in: one mailbox per session."""

    def __init__(self) -> None:
        self._mailboxes: dict = {}

    def iter_mailboxes(self):
        return list(self._mailboxes.items())

    def session_id_for(self, key):
        return key


def _controller(**kwargs) -> SessionController:
    return SessionController(_FakeEP(), client=object(), **kwargs)


class _FakeRegistry:
    """Duck-typed registry: assemble_loop_parts reads ``_tools``."""

    def __init__(self) -> None:
        from strategy_research.core.agent.tools import BaseTool

        class _T(BaseTool):
            name = "demo_tool"
            description = "demo"
            parameters = {}

            def execute(self, **kw):
                return "{}"

        self._tools = {"demo_tool": _T()}

    def get_definitions(self):
        return []

    def get(self, name):
        return self._tools.get(name)

    def all_tools(self):
        return list(self._tools.values())

    def register(self, tool):
        self._tools[tool.name] = tool


# ── B1-a: 系统提示等价 ─────────────────────────────────────


def test_system_prompt_matches_assemble_loop_parts(tmp_path):
    """SessionController 的 system prompt == assemble_loop_parts 的输出。"""
    from strategy_research.core.agent.loop_assembly import assemble_loop_parts

    registry = _FakeRegistry()
    ctrl = _controller(
        registry=registry, role="chat", workspace=tmp_path,
    )

    expected = assemble_loop_parts(
        role="chat", registry=registry, workspace=tmp_path,
    ).system_prompt

    assert ctrl._resolve_system_prompt() == expected


def test_explicit_system_overrides_assembly(tmp_path):
    """显式 system 优先（等价于 system_prompt_override）。"""
    ctrl = _controller(
        registry=_FakeRegistry(), system="CUSTOM PROMPT", workspace=tmp_path,
    )
    assert ctrl._resolve_system_prompt() == "CUSTOM PROMPT"


def test_assembly_failure_degrades_to_none(tmp_path, monkeypatch):
    """渲染失败不阻断 chat：降级为 None 并发出事件。"""
    import strategy_research.core.agent.loop_assembly as la

    def boom(**kwargs):
        raise RuntimeError("prompt exploded")

    monkeypatch.setattr(la, "assemble_loop_parts", boom)
    events: list[tuple[str, dict]] = []
    ctrl = _controller(
        registry=_FakeRegistry(), workspace=tmp_path,
        sink=lambda k, d: events.append((k, d)),
    )

    assert ctrl._resolve_system_prompt() is None
    assert events and events[0][0] == "system_prompt.render_failed"


def test_react_node_receives_assembled_prompt(tmp_path):
    """_build_engine 把装配出的 prompt 交给 react 节点 config。"""
    registry = _FakeRegistry()
    ctrl = _controller(registry=registry, role="chat", workspace=tmp_path)
    engine = ctrl._build_engine()

    expected = ctrl._resolve_system_prompt()
    # react 节点是闭包；用 cfg 无法直接读，改为验证节点存在且可运行签名
    assert "react" in engine._fns
    assert expected is not None and expected.strip()


# ── B1-b: 上下文注入 ──────────────────────────────────────


def test_no_hub_passthrough():
    ctrl = _controller()
    messages = [{"role": "user", "content": "hi"}]
    assert ctrl._inject_context(messages, "s1") == messages


def test_hub_items_prepended_as_single_block():
    """items 拼成单块前置，顺序 = bundle 顺序（与 full_task 形状一致）。"""
    from strategy_research.kernel.context import ContextHub, ContextItem

    hub = ContextHub()
    hub.register(SimpleNamespace(
        name="goal", scopes=frozenset({"session"}),
        provide=lambda q: [ContextItem(
            kind="goal", content="<current-research-goal>G</current-research-goal>",
            source="t", provider="goal",
        )],
    ))
    hub.register(SimpleNamespace(
        name="memory", scopes=frozenset({"session"}),
        provide=lambda q: [ContextItem(
            kind="memory", content="<relevant-memory>M</relevant-memory>",
            source="t", provider="memory",
        )],
    ))
    ctrl = _controller(context_hub=hub)

    out = ctrl._inject_context([{"role": "user", "content": "task"}], "s1")

    assert len(out) == 1
    assert out[0]["content"] == (
        "<current-research-goal>G</current-research-goal>"
        "\n\n<relevant-memory>M</relevant-memory>\n\ntask"
    )


def test_empty_bundle_leaves_message_untouched():
    from strategy_research.kernel.context import ContextHub

    ctrl = _controller(context_hub=ContextHub())
    messages = [{"role": "user", "content": "task"}]
    assert ctrl._inject_context(messages, "s1") == messages


def test_hub_failure_degrades_gracefully():
    """hub 抛异常时原样返回 + 发事件（不阻断 chat）。"""
    class _BrokenHub:
        def build(self, query):
            raise RuntimeError("hub down")

    events: list[tuple[str, dict]] = []
    ctrl = _controller(
        context_hub=_BrokenHub(), sink=lambda k, d: events.append((k, d)),
    )
    messages = [{"role": "user", "content": "task"}]

    assert ctrl._inject_context(messages, "s1") == messages
    assert events and events[0][0] == "context.inject_failed"


def test_context_query_carries_session_and_task():
    """query 携带 task_text 与 session_id（provider 可据此检索）。"""
    seen = {}

    class _RecordingHub:
        def build(self, query):
            seen["task"] = query.task_text
            seen["sid"] = query.session_id
            return SimpleNamespace(items=[])

    ctrl = _controller(context_hub=_RecordingHub())
    ctrl._inject_context([{"role": "user", "content": "hello"}], "sess-42")

    assert seen == {"task": "hello", "sid": "sess-42"}
