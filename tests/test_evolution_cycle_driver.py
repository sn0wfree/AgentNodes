# -*- coding: utf-8 -*-
"""WP-8 批 3b.2：``cycle_driver``（RSI 驱动三道门）单测。

迁移前 ``run_once`` / ``check_budget`` / ``record_cycle`` / 状态三件 /
三道门**零测试覆盖**（roadmap WP-8 审计 §4 缺口）——本文件补上。
halt 与闭环装配按宿主注入契约以 stub 驱动，不触真实 Gateway / LLM。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentnodes.apps.evolution import cycle_driver as cd


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    """状态文件重定向到 tmp（env 覆盖本身就是被测语义之一）。"""
    p = tmp_path / "state.json"
    monkeypatch.setenv("AGENTNODES_RSI_STATE_PATH", str(p))
    return p


def _gw() -> SimpleNamespace:
    """最小 Gateway stub：无 store（证据门按空处理）、无 sink。"""
    return SimpleNamespace(store=None, ticket_sink=None)


class TestStatePersistence:
    def test_state_path_env_override(self, state_file):
        assert cd.state_path() == state_file

    def test_load_missing_returns_skeleton(self, state_file):
        assert cd.load_state()["cycles_total"] == 0
        assert cd.load_state()["sessions"] == []

    def test_corrupted_state_returns_skeleton(self, state_file):
        state_file.write_text("{not json", encoding="utf-8")
        assert cd.load_state()["cycles_total"] == 0

    def test_save_load_roundtrip(self, state_file):
        state = cd.record_cycle(
            cd.load_state(), session_id="s1",
            evidence_count=3, signature="sig",
        )
        cd.save_state(state)
        loaded = cd.load_state()
        assert loaded["cycles_total"] == 1
        assert loaded["last_signature"] == "sig"


class TestBudgetGate:
    def test_fresh_session_allowed(self, state_file):
        assert cd.check_budget(cd.load_state(), session_id="s1") is None

    def test_session_repeat_blocked(self, state_file):
        state = cd.record_cycle(
            cd.load_state(), session_id="s1",
            evidence_count=1, signature="sig",
        )
        assert cd.check_budget(state, session_id="s1") == "session_budget"

    def test_daily_limit_blocked(self, state_file, monkeypatch):
        monkeypatch.setattr(cd, "daily_limit", lambda: 2)
        now = cd._utc_now()
        state = cd.load_state()
        for i in range(2):
            state = cd.record_cycle(
                state, session_id=f"s{i}",
                evidence_count=1, signature="sig", now=now,
            )
        assert cd.check_budget(state, session_id="other", now=now) == "daily_budget"

    def test_daily_resets_next_day(self, state_file, monkeypatch):
        monkeypatch.setattr(cd, "daily_limit", lambda: 1)
        from datetime import datetime, timedelta, timezone

        day1 = datetime(2026, 11, 19, tzinfo=timezone.utc)
        day2 = day1 + timedelta(days=1)
        state = cd.record_cycle(
            cd.load_state(), session_id="a",
            evidence_count=1, signature="s", now=day1,
        )
        assert cd.check_budget(state, session_id="b", now=day2) is None

    def test_record_cycle_bounds_session_list(self, state_file):
        state = cd.load_state()
        for i in range(cd.MAX_TRACKED_SESSIONS + 10):
            state = cd.record_cycle(
                state, session_id=f"s{i}",
                evidence_count=0, signature="",
            )
        assert len(state["sessions"]) == cd.MAX_TRACKED_SESSIONS


class TestSelectSignature:
    def test_deterministic_on_tie(self):
        reports = [
            {"task_signature": "bbb"},
            {"task_signature": "aaa"},
        ]
        assert cd.select_signature(reports) == ("aaa", 1)

    def test_most_evidence_wins(self):
        reports = [{"task_signature": "x"}] * 3 + [{"task_signature": "y"}]
        assert cd.select_signature(reports) == ("x", 3)

    def test_unsigned_ignored(self):
        assert cd.select_signature([{"task_signature": ""}]) == ("", 0)


class TestRunOnceGates:
    def _signed_reports(self, monkeypatch):
        monkeypatch.setattr(
            cd, "collect_reports", lambda gw, **kw: [{"task_signature": "sig1"}],
        )
        monkeypatch.setattr(
            cd, "resolve_objective", lambda sig, **kw: "objective-x",
        )
        monkeypatch.setattr(
            cd, "_signature_scope", lambda gw: ("planner", ("read",)),
        )

    def test_halt_gate_blocks(self, state_file, monkeypatch):
        self._signed_reports(monkeypatch)
        outcome = cd.run_once(
            gateway=_gw(), session_id="s-halt",
            halt_check=lambda: (True, "stop"),
            loop_builder=lambda gw, emit, seed: object(),
        )
        assert outcome.ran is False
        assert outcome.reason == "halted"
        assert cd.load_state()["skip_counts"]["halted"] == 1

    def test_halt_check_exception_treated_as_not_halted(self, state_file, monkeypatch):
        """护栏 callable 抛异常 → 未 halt（旧「halt 不可用不影响驱动」语义）。"""
        def _boom():
            raise RuntimeError("halt module unavailable")

        self._signed_reports(monkeypatch)
        outcome = cd.run_once(
            gateway=_gw(), session_id="s-boom",
            halt_check=_boom,
            loop_builder=lambda gw, emit, seed: None,
        )
        assert outcome.reason == "wiring_skipped"

    def test_force_bypasses_budget_not_halt(self, state_file, monkeypatch):
        self._signed_reports(monkeypatch)
        # 已消耗预算
        cd.record_cycle(
            cd.load_state(), session_id="s-f",
            evidence_count=1, signature="sig1",
        )
        halted_calls = []

        def halt():
            halted_calls.append(1)
            return False, ""

        outcome = cd.run_once(
            gateway=_gw(), session_id="s-f", force=True,
            halt_check=halt,
            loop_builder=lambda gw, emit, seed: None,
        )
        assert halted_calls, "force 不得绕过 halt 门"
        assert outcome.reason == "wiring_skipped"  # 预算闸被跳过，走到了装配

    def test_loop_builder_none_skips_wiring(self, state_file, monkeypatch):
        self._signed_reports(monkeypatch)
        outcome = cd.run_once(
            gateway=_gw(), session_id="s-wire",
            halt_check=lambda: (False, ""),
            loop_builder=None,
        )
        assert outcome.reason == "wiring_skipped"
        assert outcome.detail["loop_builder"] == "unwired"

    def test_happy_path_runs_and_records(self, state_file, monkeypatch):
        self._signed_reports(monkeypatch)

        class _Loop:
            def run_once(self, signature, *, objective, role, tool_set):
                assert objective == "objective-x"
                return {"ok": True, "reason": "validated", "metric": 0.9}

        outcome = cd.run_once(
            gateway=_gw(), session_id="s-run",
            halt_check=lambda: (False, ""),
            loop_builder=lambda gw, emit, seed: _Loop(),
        )
        assert outcome.ran is True
        assert outcome.ok is True
        state = cd.load_state()
        assert state["cycles_total"] == 1
        assert "s-run" in state["sessions"]

    def test_gateway_unavailable(self, state_file, monkeypatch):
        monkeypatch.setattr(
            cd, "_resolve_gateway", lambda gw: (None, "no gateway"),
        )
        outcome = cd.run_once(session_id="s-gw")
        assert outcome.reason == "gateway_unavailable"


class TestMakeLoopBuilder:
    def test_forwards_gw_emit_and_seed(self):
        """emit 作为显式参数透传，gw 原样到达 build_rsi_loop（无私有字段绕道）。"""
        captured = {}
        emitted = []

        def fake_build(gw, *, emit, seed=0):
            captured["gw"] = gw
            captured["emit"] = emit
            captured["seed"] = seed
            return SimpleNamespace(name="loop")

        gw = SimpleNamespace(store="S", genome="G")
        builder = cd.make_loop_builder(fake_build)
        emit = lambda kind, data: emitted.append((kind, data))
        loop = builder(gw, emit, 7)

        assert loop.name == "loop"
        assert captured["gw"] is gw
        assert captured["emit"] is emit
        assert captured["seed"] == 7
        # 行为断言：透传的 emit 真的能发事件（接口就是测试面）
        captured["emit"]("rsi.probe", {"x": 1})
        assert emitted == [("rsi.probe", {"x": 1})]
