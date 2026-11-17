"""SkipMonitor 单元测试（RSI 跳过聚合器，评审 O 的产出）。

模块此前 0% 覆盖——它是纯逻辑（计数 + 滚动窗口 + 升级告警 + snapshot 落盘），
全部行为可注入时钟驱动，无需任何 IO。
"""
from __future__ import annotations

import pytest

from agentnodes.kernel.evolution.skip_monitor import (
    DEFAULT_ESCALATION_THRESHOLD,
    ESCALATING_REASONS,
    SkipMonitor,
    SkipStats,
    monitor_from_sink,
    summarize,
)


class FakeClock:
    def __init__(self, start: float = 1000.0, step: float = 60.0):
        self.now = start
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def _monitor(**kw) -> SkipMonitor:
    kw.setdefault("clock", FakeClock())
    return SkipMonitor(**kw)


class TestObserveBasics:
    def test_str_form_counts(self):
        m = _monitor()
        m.observe("holdout_failed")
        m.observe("holdout_failed")
        assert m.totals() == {"holdout_failed": 2}
        assert m.counts() == {"holdout_failed": 2}

    def test_dict_form_counts(self):
        m = _monitor()
        m.observe({"reason": "gateway_unavailable"})
        assert m.totals() == {"gateway_unavailable": 1}

    def test_empty_reason_becomes_unknown(self):
        m = _monitor()
        m.observe("")
        m.observe({"reason": None})
        assert set(m.totals()) == {"unknown"}

    def test_non_escalating_reason_stays_silent(self):
        """no_new_evidence / budget / halted 是节流设计使然，不告警。"""
        emitted: list = []
        m = _monitor(emit=lambda k, d: emitted.append(k))
        for _ in range(10):
            m.observe("no_new_evidence")
        assert emitted == []
        assert m.totals()["no_new_evidence"] == 10


class TestEscalation:
    def test_escalates_above_threshold(self):
        emitted: list = []
        m = _monitor(emit=lambda k, d: emitted.append((k, d)))
        for _ in range(DEFAULT_ESCALATION_THRESHOLD):
            m.observe("holdout_failed")          # 阈值内不告警
        assert emitted == []
        m.observe("holdout_failed")              # 第 4 次 > 3 → 告警
        assert len(emitted) == 1
        kind, data = emitted[0]
        assert kind == "rsi.skip_escalated"
        assert data["reason"] == "holdout_failed"
        assert data["count_in_window"] == 4

    def test_window_old_marks_do_not_escalate(self):
        """窗口外的历史跳过不参与升级（24h 滚动窗口语义）。

        FakeClock 每次调用都前进 —— observe 与 counts() 都读时钟，
        断言前必须冻结（step=0），否则计数瞬间连窗口都出不去。
        """
        clock = FakeClock(step=25 * 3600)        # 每次前进 >24h
        m = _monitor(clock=clock)
        for _ in range(5):
            m.observe("cycle_crashed")
        clock.step = 0                            # 冻结在最后一个 mark
        assert m.counts() == {"cycle_crashed": 1}, "旧 mark 应被窗口淘汰"

    def test_threshold_boundary_is_strictly_above(self):
        clock = FakeClock()
        m = _monitor(clock=clock, threshold=2)
        m.observe("proposal_failed")
        m.observe("proposal_failed")
        assert m.totals()["proposal_failed"] == 2  # == 阈值，未升级
        # counts 也只 2
        assert m.counts()["proposal_failed"] == 2


class TestSnapshotRoundTrip:
    def test_snapshot_load_restores_marks_and_totals(self):
        """落盘→恢复后窗口轨迹一起回来（跨进程连续告警语义）。"""
        m1 = _monitor()
        for _ in range(2):
            m1.observe("wiring_skipped")
        snap = m1.snapshot()

        m2 = _monitor(clock=FakeClock(start=m1.snapshot and 0 or 0))
        # 恢复到同一时钟位置：新进程时钟从 snapshot 的最后 mark 起算
        last_mark = snap["stats"][0]["marks"][-1]
        m2 = _monitor(clock=lambda: last_mark)
        m2.load_snapshot(snap)
        assert m2.totals()["wiring_skipped"] == 2
        m2.observe("wiring_skipped")             # 第 3 次 == 阈值
        assert m2.counts()["wiring_skipped"] == 3

    def test_load_snapshot_none_is_noop(self):
        m = _monitor()
        m.load_snapshot(None)
        m.load_snapshot({})
        assert m.totals() == {}

    def test_load_snapshot_prunes_expired_marks(self):
        m = _monitor(clock=lambda: 100_000.0)  # cutoff=13600 > 全部 stale marks
        stale = {
            "window_s": 86400,
            "threshold": 3,
            "stats": [{
                "reason": "cycle_crashed", "total": 5, "recent": 5,
                "last_ts": 100.0, "escalated_at": 0.0,
                "marks": [10.0, 20.0, 30.0],   # 全部远在窗口外
            }],
        }
        m.load_snapshot(stale)
        assert m.counts() == {}, "窗口外轨迹全部淘汰"
        assert m.totals() == {"cycle_crashed": 5}, "累计数保留"

    def test_from_dict_tolerates_bad_marks(self):
        s = SkipStats.from_dict({
            "reason": "x", "total": "3", "marks": [1.0, "bad", None, "7"],
        })
        assert s.total == 3
        assert s.marks == [1.0, 7.0]


class TestSinkAdapters:
    def test_observe_event_only_subscribes_skip_kind(self):
        m = _monitor()
        m.observe_event("other.kind", {"reason": "x"})
        assert m.totals() == {}
        m.observe_event("rsi.cycle_skipped", {"reason": "holdout_failed"})
        assert m.totals() == {"holdout_failed": 1}

    def test_emit_failure_does_not_break_monitor(self):
        def bad_emit(kind, data):
            raise RuntimeError("sink dead")

        m = _monitor(emit=bad_emit)
        for _ in range(5):
            m.observe("holdout_failed")          # emit 抛错被吞
        assert m.totals()["holdout_failed"] == 5

    def test_monitor_from_sink_attaches_and_detaches(self):
        class Sink:
            def __init__(self):
                self.sinks = []

            def add_sink(self, fn):
                self.sinks.append(fn)
                return lambda: self.sinks.remove(fn)

        sink = Sink()
        monitor, unsubscribe = monitor_from_sink(sink, threshold=1)
        assert monitor is not None and callable(unsubscribe)
        monitor.observe_event("rsi.cycle_skipped", {"reason": "cycle_crashed"})
        assert sink.sinks, "observe_event 已挂到 sink"
        unsubscribe()
        assert not sink.sinks

    def test_monitor_from_plain_function_returns_none(self):
        monitor, unsubscribe = monitor_from_sink(lambda k, d: None)
        assert monitor is None and unsubscribe is None

    def test_max_marks_bounded(self):
        clock = FakeClock(step=1)
        m = _monitor(clock=clock, max_marks=3, threshold=10 ** 6)
        for _ in range(10):
            m.observe("holdout_failed")
        snap = m.snapshot()
        assert len(snap["stats"][0]["marks"]) == 3, "窗口轨迹有界（防落盘膨胀）"
        assert m.totals()["holdout_failed"] == 10, "累计数不受界"


def test_summarize_pure_function():
    events = [
        {"reason": "holdout_failed"}, {"reason": "holdout_failed"},
        {"reason": "no_proposal"}, {},
        ]
    assert summarize(events) == {
        "holdout_failed": 2, "no_proposal": 1, "unknown": 1,
    }


def test_escalating_reasons_catalog():
    """升级清单是显式白名单——节流类 reason 不在其中（防告警疲劳回潮）。"""
    assert "no_new_evidence" not in ESCALATING_REASONS
    assert "session_budget" not in ESCALATING_REASONS
    assert "daily_budget" not in ESCALATING_REASONS
    assert "halted" not in ESCALATING_REASONS
    assert "cycle_crashed" in ESCALATING_REASONS
