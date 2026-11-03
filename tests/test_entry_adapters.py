"""entry_adapters 边界测试 —— 路径归一、白名单、payload 类型、跨源差异。"""
from __future__ import annotations

import pytest

from strategy_research.core.gateway.entry_adapters import (
    api_chat_request,
    cli_request,
    mcp_request,
    scheduler_request,
    submit_via_entry,
)
from strategy_research.kernel.orchestration import (
    DEFAULT_QUEUE_MODE,
    EntryPoint,
    QueueMode,
    RejectReason,
    SourceKind,
    WorkKind,
)


# ── session_key 归一 ─────────────────────────────────────


@pytest.mark.parametrize(
    ("fn", "given", "expected"),
    [
        (api_chat_request, "sess-1", "chat:sess-1"),
        (cli_request, "cli-1", "chat:cli-1"),
        (mcp_request, "client", "mcp:client"),
        (scheduler_request, "nightly", "automation:nightly"),
    ],
)
def test_each_adapter_prefixes_its_domain(fn, given, expected):
    if fn in (api_chat_request,):
        req = fn(given, "text")
    else:
        req = fn(given, {"k": 1})
    assert req.session_key == expected


@pytest.mark.parametrize("key", ["chat:existing", "study:x", "automation:job", "mcp:c"])
def test_existing_domain_prefix_preserved(key):
    assert cli_request(key, "t").session_key == key


def test_colon_in_name_kept_as_is():
    """已带域前缀的一律不二次加前缀（避免 chat:chat:x）。"""
    assert cli_request("chat:double", "t").session_key == "chat:double"


# ── 源默认 queue_mode（经五行表） ────────────────────────


@pytest.mark.parametrize(
    ("req_fn", "args", "expected_source", "expected_mode"),
    [
        (api_chat_request, ("s", "t"), SourceKind.API_CHAT, QueueMode.STEER),
        (cli_request, ("s", "t"), SourceKind.CLI, QueueMode.FOLLOWUP),
        (mcp_request, ("s", {"k": 1}), SourceKind.MCP, QueueMode.FOLLOWUP),
        (scheduler_request, ("s", {"k": 1}), SourceKind.SCHEDULER, QueueMode.INTERRUPT),
    ],
)
def test_images_map_to_expected_default_mode(req_fn, args, expected_source, expected_mode):
    req = req_fn(*args)
    assert req.source is expected_source
    # 未显式声明 → 走 DEFAULT_QUEUE_MODE
    assert req.queue_mode is None
    assert DEFAULT_QUEUE_MODE[expected_source] is expected_mode


# ── payload 白名单（协议字段剥除） ───────────────────────


def test_api_chat_payload_has_only_text():
    req = api_chat_request("s", "内容")
    assert set(req.payload) == {"text"}


def test_mcp_payload_passed_through_as_copy():
    original = {"q": 1, "nested": {"a": 2}}
    req = mcp_request("c", original)
    assert req.payload == original
    assert req.payload is not original  # 防御性拷贝


def test_mcp_payload_protocol_key_rejected_downstream():
    """adapter 不剥 mcp payload 的键——由 EntryPoint 白名单拒绝（防误传）。"""
    ep = EntryPoint(sink=lambda k, d: None)
    req = mcp_request("c", {"http": "x"})
    receipt = submit_via_entry(ep, req)
    assert not receipt.accepted
    assert receipt.reason is RejectReason.PAYLOAD_PROTOCOL_FIELD


def test_scheduler_payload_passed_through():
    req = scheduler_request("j", {"study_id": "s1", "objective": "o"})
    assert req.payload["study_id"] == "s1"


# ── kind 映射 ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("req_fn", "args", "expected_kind"),
    [
        (api_chat_request, ("s", "t"), WorkKind.CHAT),
        (cli_request, ("s", "t"), WorkKind.CHAT),
        (mcp_request, ("s", {"k": 1}), WorkKind.CHAT),
        (scheduler_request, ("s", {"k": 1}), WorkKind.AUTOMATION),
    ],
)
def test_kind_mapping(req_fn, args, expected_kind):
    assert req_fn(*args).kind is expected_kind


# ── 提交路径 ────────────────────────────────────────────


def test_submit_via_entry_returns_receipt():
    ep = EntryPoint(sink=lambda k, d: None)
    receipt = submit_via_entry(ep, api_chat_request("s", "t"))
    assert receipt.accepted
    assert receipt.session_id


def test_submit_via_entry_rejects_collect():
    from strategy_research.kernel.orchestration import WorkRequest

    ep = EntryPoint(sink=lambda k, d: None)
    req = WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.CLI,
        session_key="chat:s", payload={"text": "t"},
        queue_mode=QueueMode.COLLECT,
    )
    receipt = submit_via_entry(ep, req)
    assert not receipt.accepted
    assert receipt.reason is RejectReason.UNSUPPORTED_QUEUE_MODE


# ── 空/异常输入 ─────────────────────────────────────────


def test_empty_text_allowed_by_adapter():
    """adapter 不校验内容语义（校验属入口层）；空文本仍可构造。"""
    req = api_chat_request("s", "")
    assert req.payload["text"] == ""


def test_empty_session_id_produces_domain_prefix_only():
    """空 id → 'chat:'（缺 name 部分，EntryPoint 的 MALFORMED 校验会拒）。"""
    req = api_chat_request("", "t")
    assert req.session_key == "chat:"
    ep = EntryPoint(sink=lambda k, d: None)
    receipt = submit_via_entry(ep, req)
    assert not receipt.accepted
    assert receipt.reason is RejectReason.MALFORMED
