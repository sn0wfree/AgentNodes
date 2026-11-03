"""B3 状态查询测试——非阻塞 session_status 快照。

设计：docs/stage-b3-controller-assessment-20261030.md §3
"""
from __future__ import annotations

from dataclasses import dataclass, field

from strategy_research.core.gateway import Gateway
from strategy_research.kernel.orchestration import SourceKind, WorkKind, WorkRequest


@dataclass
class Resp:
    content: str = "回答"
    usage: dict = field(default_factory=lambda: {"total_tokens": 5})


class FakeClient:
    def __init__(self, answer: str = "回答") -> None:
        self.answer = answer

    def chat(self, messages, **kwargs):
        return Resp(content=self.answer)


def chat(text: str = "q", key: str = "chat:s") -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.CLI,
        session_key=key, payload={"text": text},
    )


def test_unknown_session_is_all_empty():
    gw = Gateway(client=FakeClient())
    st = gw.session_status("chat:never")
    assert st["active"] is False
    assert st["session_id"] == ""
    assert st["active_run_id"] == ""
    assert st["queued"] == 0
    assert st["last_turn_id"] == ""
    assert st["last_ok"] is None
    assert st["last_answer"] == ""


def test_queued_turn_is_visible_not_active():
    gw = Gateway(client=FakeClient())
    gw.entry.submit(chat("排队中"))
    st = gw.session_status("chat:s")
    assert st["active"] is False  # 没开跑
    assert st["queued"] == 1


def test_active_run_is_visible():
    gw = Gateway(client=FakeClient())
    gw.fence.claim("s", "run-1")  # 模拟活跃
    st = gw.session_status("chat:s")
    assert st["active"] is True
    assert st["active_run_id"] == "run-1"


def test_completed_turn_is_visible():
    gw = Gateway(client=FakeClient())
    gw.start_consumer(kinds={WorkKind.CHAT})
    try:
        gw.entry.submit(chat("你好"))
        sid = gw.entry.session_id_for("chat:s")
        gw.wait_turn(sid, timeout=5.0)
        st = gw.session_status("chat:s")
        assert st["active"] is False
        assert st["last_ok"] is True
        assert st["last_answer"] == "回答"
        assert st["last_turn_id"].endswith("#1")
    finally:
        gw.stop_consumer(grace=2.0)


def test_cancelled_turn_shows_failure():
    gw = Gateway(client=FakeClient())
    gw.start_consumer(kinds={WorkKind.CHAT})
    try:
        gw.entry.submit(chat("q"))
        sid = gw.entry.session_id_for("chat:s")
        gw.wait_turn(sid, timeout=5.0)
        # 伪造一条失败结果（模拟取消后的留存）
        from strategy_research.core.gateway.session_controller import RunResult

        gw._turn_results[sid] = RunResult(f"{sid}#1", f"{sid}#1:r0", False,
                                          error="cancelled: user")
        st = gw.session_status("chat:s")
        assert st["last_ok"] is False
        assert "cancelled" in (st["last_error"] or "")
    finally:
        gw.stop_consumer(grace=2.0)
