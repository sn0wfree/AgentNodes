"""R7 技术债修复测试——sink 累积、wait_turn 返旧结果、结果留存。

设计：docs/stage-r7-r2-techdebt-design-20261030.md
  A. add_sink：不累积、可注销、幂等、并发安全
  B. wait_turn：代际比较，第二次等新一轮；超时返回 None
  C. bridge：收尾事件后自注销，不重复推送
  D. 结果留存：只保留最近一次
"""
from __future__ import annotations

import threading
import time
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


# ── A. add_sink 不累积 ───────────────────────────────────


def test_sinks_do_not_accumulate():
    gw = Gateway(client=FakeClient())
    seen: list[str] = []
    unsubs = [gw.add_sink(lambda k, d, _i=i: seen.append(f"{_i}:{k}")) for i in range(5)]

    gw._combined_sink("test.event", {})
    # 每个 sink 恰好收到一次，不是 N 层嵌套
    assert len(seen) == 5
    assert sorted(seen) == [f"{i}:test.event" for i in range(5)]

    for u in unsubs:
        u()
    seen.clear()
    gw._combined_sink("after.unsub", {})
    assert seen == []


def test_unsubscribe_is_idempotent():
    gw = Gateway(client=FakeClient())
    unsub = gw.add_sink(lambda k, d: None)
    unsub()
    unsub()  # 不抛
    assert len(gw._sinks) == 0


def test_constructor_sink_lands_in_set():
    calls: list[str] = []
    gw = Gateway(client=FakeClient(), sink=lambda k, d: calls.append(k))
    gw._combined_sink("x", {})
    assert calls == ["x"]
    assert len(gw._sinks) == 1


def test_concurrent_add_and_broadcast_is_safe():
    gw = Gateway(client=FakeClient())
    stop = threading.Event()

    def broadcaster():
        while not stop.is_set():
            gw._combined_sink("tick", {})

    def adder():
        for _ in range(200):
            gw.add_sink(lambda k, d: None)

    t1 = threading.Thread(target=broadcaster)
    t2 = threading.Thread(target=adder)
    t1.start(); t2.start()
    t2.join()
    stop.set(); t1.join(2.0)
    # 未抛 RuntimeError: set changed size during iteration
    assert True


# ── B. wait_turn 代际 ────────────────────────────────────


def test_second_wait_does_not_return_stale_result():
    """R7 核心：同一 session 第二次 wait 必须等新一轮，不是旧结果。"""
    gw = Gateway(client=FakeClient("第一轮"))
    gw.start_consumer(kinds={WorkKind.CHAT})
    try:
        sid_key = "chat:repeat"
        gw.entry.submit(chat("q1", sid_key))
        sid = gw.entry.session_id_for(sid_key)
        first = gw.wait_turn(sid, timeout=5.0)
        assert first is not None and first.answer == "第一轮"

        # 没有新 turn：第二次 wait 应超时，而不是立即返回旧结果
        t0 = time.monotonic()
        stale = gw.wait_turn(sid, timeout=0.3)
        elapsed = time.monotonic() - t0
        assert stale is None
        assert elapsed >= 0.25, f"应立即返回旧结果才耗时 {elapsed:.3f}s"

        # 提交新一轮 → 第三次 wait 拿到新结果
        gw.controller._client = FakeClient("第二轮")
        gw.entry.submit(chat("q2", sid_key))
        second = gw.wait_turn(sid, timeout=5.0)
        assert second is not None and second.answer == "第二轮"
    finally:
        gw.stop_consumer(grace=2.0)


def test_wait_turn_times_out_when_no_turn():
    gw = Gateway(client=FakeClient())
    assert gw.wait_turn("chat:never", timeout=0.05) is None


# ── C. bridge 自注销 ─────────────────────────────────────


def test_bridge_unsubscribes_after_terminal_event():
    from strategy_research.api.routers import chat as chat_mod

    gw = Gateway(client=FakeClient())
    holder: dict[str, str] = {}
    close = chat_mod._install_chat_event_bridge(
        gw, "sess-b", holder, service=None, persist=True,
    )
    assert len(gw._sinks) == 1

    # 收尾事件 → 自注销
    gw._combined_sink("run.answer", {"run_id": "t1:r0", "content": "ok"})
    assert len(gw._sinks) == 0

    close()  # 幂等兜底
    assert len(gw._sinks) == 0


def test_bridge_stays_registered_until_terminal():
    from strategy_research.api.routers import chat as chat_mod

    gw = Gateway(client=FakeClient())
    close = chat_mod._install_chat_event_bridge(
        gw, "sess-c", {}, service=None, persist=True,
    )
    gw._combined_sink("text_delta", {"text": "你"})
    assert len(gw._sinks) == 1  # 中间事件不注销
    close()


def test_two_requests_same_session_do_not_duplicate_events():
    """连续两个请求：第一个的 bridge 已注销，不会重复推送。"""
    from strategy_research.api.routers import chat as chat_mod

    gw = Gateway(client=FakeClient())
    delivered: list[str] = []

    unsub1 = gw.add_sink(lambda k, d: delivered.append(f"1:{k}"))
    close1 = chat_mod._install_chat_event_bridge(
        gw, "sess-d", {}, service=None, persist=True,
    )
    gw._combined_sink("run.answer", {"run_id": "a", "content": "x"})
    # 第一个 bridge 自注销；unsub1 仍在（调用方负责）
    assert len(gw._sinks) == 1

    close2 = chat_mod._install_chat_event_bridge(
        gw, "sess-d", {}, service=None, persist=True,
    )
    delivered.clear()
    gw._combined_sink("text_delta", {"text": "y"})
    # 只有当前 bridge + unsub1 收到；旧 bridge 不在
    assert delivered.count("1:text_delta") == 1
    assert len(gw._sinks) == 2

    close1(); close2(); unsub1()
    assert len(gw._sinks) == 0


# ── D. 结果留存 ──────────────────────────────────────────


def test_turn_results_keep_only_latest():
    gw = Gateway(client=FakeClient())
    gw.start_consumer(kinds={WorkKind.CHAT})
    try:
        gw.entry.submit(chat("a", "chat:keep"))
        sid = gw.entry.session_id_for("chat:keep")
        gw.wait_turn(sid, timeout=5.0)
        gw.entry.submit(chat("b", "chat:keep"))
        gw.wait_turn(sid, timeout=5.0)
        # 每 session 一条，不累积历史
        assert len(gw._turn_results) == 1
    finally:
        gw.stop_consumer(grace=2.0)
