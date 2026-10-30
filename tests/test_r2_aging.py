"""R2.3 优先级老化测试——领先有界，防 interrupt 饿死普通消息。

设计：docs/stage-r2-consumer-steer-aging-design-20261030.md §4
  A. 未到龄：interrupt 仍然插队（现行为不变）
  B. 到龄：普通项升入 interrupt 级先被服务（work.aged 事件一次）
  C. aging_s <= 0：禁用
  D. FIFO：多个到龄项按入队序
  E. ConfigHub：MailboxConfig.aging_s 可配、非法值拒启
"""
from __future__ import annotations

import pytest

from strategy_research.core.orchestration import (
    MailboxConfig,
    QueueMode,
    SourceKind,
    WorkKind,
    WorkRequest,
)
from strategy_research.core.orchestration.config_hub import (
    ConfigError,
    PlatformConfig,
    load_platform_config,
)
from strategy_research.core.orchestration.mailbox import Mailbox, QueueItem


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


def req(text: str, *, mode=QueueMode.FOLLOWUP) -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.CLI,
        session_key="chat:a", payload={"text": text},
        queue_mode=mode,
    )


# ── A. 未到龄 ────────────────────────────────────────────


def test_interrupt_still_jumps_before_aging():
    clock, rec = FakeClock(), Recorder()
    mb = Mailbox("sess-a", aging_s=10.0, clock=clock, sink=rec)
    mb.submit(req("普通"), resolve_session=lambda k: "s")
    clock.now += 3  # 未到 10s
    mb.submit(req("急", mode=QueueMode.INTERRUPT), resolve_session=lambda k: "s")
    assert mb.poll_due().payload["text"] == "急"
    assert rec.of("work.aged") == []


# ── B. 到龄 ──────────────────────────────────────────────


def test_aged_item_beats_later_interrupt_and_fires_once():
    clock, rec = FakeClock(), Recorder()
    mb = Mailbox("sess-a", aging_s=10.0, clock=clock, sink=rec)
    mb.submit(req("普通"), resolve_session=lambda k: "s")
    clock.now += 15  # 到龄
    mb.submit(req("急1", mode=QueueMode.INTERRUPT), resolve_session=lambda k: "s")
    mb.submit(req("急2", mode=QueueMode.INTERRUPT), resolve_session=lambda k: "s")

    first = mb.poll_due()
    assert first.payload["text"] == "普通"  # 到龄普通项压过后到的 interrupt

    aged = rec.of("work.aged")
    assert len(aged) == 1 and aged[0]["waited_s"] >= 10.0

    # 第二次 poll 不重复发事件
    mb.poll_due()
    assert len(rec.of("work.aged")) == 1


def test_aging_respects_fifo_among_aged():
    clock, rec = FakeClock(), Recorder()
    mb = Mailbox("sess-a", aging_s=5.0, clock=clock, sink=rec)
    mb.submit(req("先"), resolve_session=lambda k: "s")
    clock.now += 1
    mb.submit(req("后"), resolve_session=lambda k: "s")
    clock.now += 10  # 两者都到龄
    assert mb.poll_due().payload["text"] == "先"
    assert mb.poll_due().payload["text"] == "后"


# ── C. 禁用 ──────────────────────────────────────────────


def test_aging_disabled_when_zero():
    clock, rec = FakeClock(), Recorder()
    mb = Mailbox("sess-a", aging_s=0.0, clock=clock, sink=rec)
    mb.submit(req("普通"), resolve_session=lambda k: "s")
    clock.now += 999
    mb.submit(req("急", mode=QueueMode.INTERRUPT), resolve_session=lambda k: "s")
    assert mb.poll_due().payload["text"] == "急"
    assert rec.of("work.aged") == []


# ── E. ConfigHub ─────────────────────────────────────────


def test_mailbox_config_aging_field_and_validation():
    cfg = PlatformConfig()
    assert cfg.mailbox.aging_s == 15.0

    loaded = load_platform_config(env={"QNODES_MAILBOX__AGING_S": "30"})
    assert loaded.mailbox.aging_s == 30.0

    with pytest.raises(ConfigError):
        load_platform_config(env={"QNODES_MAILBOX__AGING_S": "-1"})
