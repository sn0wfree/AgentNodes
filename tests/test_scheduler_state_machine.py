"""StudyScheduler 状态机单元测试——retry / continue / unarchive 分支。

覆盖 docs/coverage-90-plan-20261030.md §3.2 第 1 优先项（原 68.6%）。
只测**纯状态转移**：用假 store 断言「状态写入 + 事件 + 返回值」，
不启动真实研究执行（那是集成测试的事）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from strategy_research.apps.study.models import StudyStatus
from strategy_research.apps.study.scheduler import StudyScheduler


# ── 假件 ─────────────────────────────────────────────────


class FakeStudy:
    def __init__(self, sid="st-1", *, status=StudyStatus.QUEUED,
                 session_id="sess-1", current_round=1):
        self.study_id = sid
        self.execution_status = status
        self.session_id = session_id
        self.current_round = current_round
        self.owner_session_id = session_id
        self.objective = "objective"


class FakeStore:
    def __init__(self, study=None):
        self.study = study
        self.status_updates: list[tuple] = []
        self.round_resets: list[tuple] = []
        self.unarchived: list[str] = []

    def get_study(self, sid):
        return self.study if self.study and self.study.study_id == sid else None

    def update_execution_status(self, sid, status, **kwargs):
        self.status_updates.append((sid, status, kwargs))
        if self.study:
            self.study.execution_status = status

    def reset_round_counter(self, sid, *, mode, start_round):
        self.round_resets.append((sid, mode, start_round))

    def unarchive_study(self, sid):
        self.unarchived.append(sid)
        return self.study


def make_sched(study=None):
    store = FakeStore(study)
    sched = StudyScheduler(store)
    events: list[tuple] = []
    sched._emit_event = lambda session, kind, data: events.append((session, kind, data))
    return sched, store, events


# ── retry ────────────────────────────────────────────────


@pytest.mark.parametrize("status", [
    StudyStatus.ERROR,
    StudyStatus.EARLY_STOPPED,
    StudyStatus.BUDGET_LIMITED,
    StudyStatus.NEEDS_REFRESH,
])
def test_retry_from_retryable_states(status):
    sched, store, events = make_sched(FakeStudy(status=status))
    assert sched.retry("st-1") is True
    assert store.round_resets == [("st-1", "append", 1)]
    assert store.status_updates[-1][1] is StudyStatus.INTERRUPTED
    assert store.status_updates[-1][2]["last_error"] is None
    assert events[-1][1] == "study_retry_queued"
    # 注：previous_status 在 store 写入**之后**读取，因此是 interrupted。
    # 这是现实现的既有行为（record-after-write），此处锁定现状；若将来
    # 改为先取快照再写，这里应更新为原始 status.value。
    assert events[-1][2]["previous_status"] == StudyStatus.INTERRUPTED.value


@pytest.mark.parametrize("status", [
    StudyStatus.RUNNING, StudyStatus.QUEUED, StudyStatus.COMPLETE,
    StudyStatus.CANCELLED, StudyStatus.PAUSED,
])
def test_retry_rejected_for_non_retryable(status):
    sched, store, events = make_sched(FakeStudy(status=status))
    assert sched.retry("st-1") is False
    assert store.round_resets == []
    assert events == []


def test_retry_unknown_study():
    sched, store, _ = make_sched(None)
    assert sched.retry("nope") is False


def test_retry_restart_mode_clears_history():
    sched, store, _ = make_sched(FakeStudy(status=StudyStatus.ERROR))
    assert sched.retry("st-1", mode="restart", from_round=3) is True
    assert store.round_resets == [("st-1", "restart", 3)]


def test_retry_invalid_mode_falls_back_to_append():
    sched, store, _ = make_sched(FakeStudy(status=StudyStatus.ERROR))
    sched.retry("st-1", mode="bogus")
    assert store.round_resets[0][1] == "append"


def test_retry_from_round_le_one_normalizes_to_one():
    sched, store, _ = make_sched(FakeStudy(status=StudyStatus.ERROR))
    sched.retry("st-1", from_round=0)
    assert store.round_resets[0][2] == 1


# ── continue_study ───────────────────────────────────────


def test_continue_paused_with_token_unpauses():
    """PAUSED 且有控制令牌 → 直接取消暂停，不重新排队。"""
    sched, store, events = make_sched(FakeStudy(status=StudyStatus.PAUSED))
    sched._control_tokens["st-1"] = SimpleNamespace(paused=True, cancelled=False)
    assert asyncio.run(sched.continue_study("st-1")) is True
    assert sched._control_tokens["st-1"].paused is False
    assert events == []


def test_continue_interrupted_requeues():
    sched, store, events = make_sched(FakeStudy(status=StudyStatus.INTERRUPTED))
    submitted: list[str] = []

    async def fake_submit(study):
        submitted.append(study.study_id)
        return True

    sched.submit = fake_submit  # type: ignore[method-assign]
    assert asyncio.run(sched.continue_study("st-1")) is True
    assert submitted == ["st-1"]


@pytest.mark.parametrize("status", [
    StudyStatus.ERROR, StudyStatus.BUDGET_LIMITED, StudyStatus.EARLY_STOPPED,
    StudyStatus.NEEDS_REFRESH, StudyStatus.CANCELLED,
])
def test_continue_restartable_states_reset_and_requeue(status):
    sched, store, events = make_sched(FakeStudy(status=status))
    submitted: list[str] = []

    async def fake_submit(study):
        submitted.append(study.study_id)
        return True

    sched.submit = fake_submit  # type: ignore[method-assign]
    assert asyncio.run(sched.continue_study("st-1")) is True
    # continue_study 默认 mode="append"（保留历史，续跑）
    assert store.round_resets == [("st-1", "append", 1)]
    assert events[-1][1] == "study_retry_queued"
    assert submitted == ["st-1"]


def test_continue_complete_uses_continue_mode():
    sched, store, events = make_sched(FakeStudy(status=StudyStatus.COMPLETE,
                                               current_round=5))
    submitted: list[str] = []

    async def fake_submit(study):
        submitted.append(study.study_id)
        return True

    sched.submit = fake_submit  # type: ignore[method-assign]
    assert asyncio.run(sched.continue_study("st-1")) is True
    # COMPLETE 分支不重置轮次计数器（续跑）
    assert store.round_resets == []
    assert events[-1][2]["mode"] == "continue"
    assert events[-1][2]["from_round"] == 5
    assert submitted == ["st-1"]


def test_continue_unknown_study_returns_false():
    sched, _, _ = make_sched(None)
    assert asyncio.run(sched.continue_study("nope")) is False


def test_continue_queued_state_returns_false():
    """QUEUED / RUNNING 等在跑的状态无需 continue。"""
    sched, store, _ = make_sched(FakeStudy(status=StudyStatus.QUEUED))
    assert asyncio.run(sched.continue_study("st-1")) is False
    assert store.round_resets == []


# ── unarchive ────────────────────────────────────────────


def test_unarchive_archived_study():
    sched, store, events = make_sched(FakeStudy(status=StudyStatus.ARCHIVED))
    assert sched.unarchive("st-1") is True
    assert store.unarchived == ["st-1"]
    assert events[-1][1] == "study_unarchived"


def test_unarchive_non_archived_returns_false():
    sched, store, _ = make_sched(FakeStudy(status=StudyStatus.RUNNING))
    assert sched.unarchive("st-1") is False
    assert store.unarchived == []


def test_unarchive_unknown_study():
    sched, _, _ = make_sched(None)
    assert sched.unarchive("nope") is False


def test_unarchive_store_failure_returns_false():
    sched, store, _ = make_sched(FakeStudy(status=StudyStatus.ARCHIVED))
    store.unarchive_study = lambda sid: None  # type: ignore[method-assign]
    assert sched.unarchive("st-1") is False


# ── cancel ───────────────────────────────────────────────


def test_cancel_with_token_sets_flag_and_audit():
    sched, store, _ = make_sched(FakeStudy())
    tok = SimpleNamespace(cancelled=False)
    sched._control_tokens["st-1"] = tok
    assert sched.cancel("st-1", reason="user asked") is True
    assert tok.cancelled is True
    assert store.status_updates[-1][1] is StudyStatus.CANCELLED
    assert "user asked" in store.status_updates[-1][2]["last_error"]


def test_cancel_without_reason_skips_status_write():
    sched, store, _ = make_sched(FakeStudy())
    sched._control_tokens["st-1"] = SimpleNamespace(cancelled=False)
    sched.cancel("st-1")
    assert store.status_updates == []


def test_cancel_audit_failure_is_swallowed():
    """审计写入失败不影响取消本身（best-effort）。"""
    sched, store, _ = make_sched(FakeStudy())
    sched._control_tokens["st-1"] = SimpleNamespace(cancelled=False)

    def boom(*a, **k):
        raise RuntimeError("db down")

    store.update_execution_status = boom  # type: ignore[method-assign]
    assert sched.cancel("st-1", reason="x") is True


def test_cancel_queued_study_without_token():
    """排队中（无令牌）→ 在 store 里标 CANCELLED，不抛。"""
    sched, store, _ = make_sched(FakeStudy(status=StudyStatus.QUEUED))
    result = sched.cancel("st-1")
    assert isinstance(result, bool)
    assert store.status_updates[-1][1] is StudyStatus.CANCELLED
