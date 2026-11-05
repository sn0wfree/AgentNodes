"""llm 节点单元测试 —— 消息历史 / system 注入 / usage / 异常传播。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agentnodes.kernel.runtimes.nodes.llm import make_llm_node


@dataclass
class Resp:
    content: str = ""
    usage: dict = field(default_factory=dict)


class ScriptedClient:
    def __init__(self, *responses: Resp) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []
        self.kwargs: list[dict] = []

    def chat(self, messages: list[dict], **kwargs: Any) -> Resp:
        self.calls.append(list(messages))
        self.kwargs.append(kwargs)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def state(*messages: dict) -> dict:
    return {"messages": list(messages)}


# ── 基本行为 ─────────────────────────────────────────────


def test_appends_assistant_message():
    client = ScriptedClient(Resp(content="答案"))
    node = make_llm_node(client)
    out = node(state({"role": "user", "content": "问题"}))
    assert out["messages"] == [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "答案"},
    ]
    assert out["answer"] == "答案"


def test_passes_history_unchanged_to_client():
    client = ScriptedClient(Resp(content="ok"))
    node = make_llm_node(client)
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]
    node(state(*history))
    assert client.calls[0] == history  # 无 system 时不加任何东西


def test_usage_propagated():
    client = ScriptedClient(Resp(content="x", usage={"total_tokens": 42}))
    out = make_llm_node(client)(state({"role": "user", "content": "q"}))
    assert out["usage"] == {"total_tokens": 42}


def test_usage_missing_becomes_empty_dict():
    client = ScriptedClient(Resp(content="x", usage={}))
    out = make_llm_node(client)(state({"role": "user", "content": "q"}))
    assert out["usage"] == {}


def test_empty_content_allowed():
    client = ScriptedClient(Resp(content=""))
    out = make_llm_node(client)(state({"role": "user", "content": "q"}))
    assert out["answer"] == ""
    assert out["messages"][-1] == {"role": "assistant", "content": ""}


# ── system 注入 ──────────────────────────────────────────


def test_system_prompt_prepended_once():
    client = ScriptedClient(Resp(content="ok"))
    node = make_llm_node(client, system="你是研究助手")
    node(state({"role": "user", "content": "q"}))
    sent = client.calls[0]
    assert sent[0] == {"role": "system", "content": "你是研究助手"}
    assert len([m for m in sent if m["role"] == "system"]) == 1


def test_existing_system_message_not_duplicated():
    client = ScriptedClient(Resp(content="ok"))
    node = make_llm_node(client, system="默认系统提示")
    node(state({"role": "system", "content": "调用方自己的提示"},
               {"role": "user", "content": "q"}))
    sent = client.calls[0]
    assert sent[0]["content"] == "调用方自己的提示"  # 调用方优先
    assert len([m for m in sent if m["role"] == "system"]) == 1


def test_no_system_configured_adds_nothing():
    client = ScriptedClient(Resp(content="ok"))
    make_llm_node(client)(state({"role": "user", "content": "q"}))
    assert all(m["role"] != "system" for m in client.calls[0])


# ── 状态卫生 ─────────────────────────────────────────────


def test_input_state_not_mutated():
    client = ScriptedClient(Resp(content="ok"))
    original = state({"role": "user", "content": "q"})
    snapshot = [dict(m) for m in original["messages"]]
    make_llm_node(client)(original)
    assert original["messages"] == snapshot  # 输入不被改写


def test_missing_messages_key_treated_as_empty():
    client = ScriptedClient(Resp(content="ok"))
    out = make_llm_node(client)({})
    assert client.calls[0] == []
    assert out["messages"] == [{"role": "assistant", "content": "ok"}]


def test_other_state_keys_preserved():
    client = ScriptedClient(Resp(content="ok"))
    out = make_llm_node(client)({"messages": [], "run_id": "r1", "custom": [1, 2]})
    assert out["run_id"] == "r1"
    assert out["custom"] == [1, 2]


# ── 异常传播 ─────────────────────────────────────────────


def test_client_exception_propagates():
    class Boom:
        def chat(self, messages, **kwargs):
            raise RuntimeError("provider down")

    with pytest.raises(RuntimeError, match="provider down"):
        make_llm_node(Boom())(state({"role": "user", "content": "q"}))
