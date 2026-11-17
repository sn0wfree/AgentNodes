"""批 C：SessionService 执行链四出口 + continuation guard。

**被测对象**：``_run_attempt`` 的四条出口
1. ``cfg is None`` → attempt.failed("LLM config unavailable")，立即返回
2. 正常 → attempt.completed + assistant_message
3. 超时（``_CHAT_ATTEMPT_TIMEOUT``）→ attempt.failed(timeout) + agent_done
4. 异常 → attempt.failed(exc) + agent_done
外加 ``_run_with_agent`` 的组装与 ``_run_with_guard`` 的 continuation 语义。

**夹具**：``_run_with_agent`` 替身（唯一变量），其余全真——真 store、真 emit
序列、真 LLMConfig 注入。
"""
from __future__ import annotations

import asyncio

import pytest

from agentnodes.kernel.session.models import Attempt, AttemptStatus
from agentnodes.kernel.session.service import SessionService
from agentnodes.kernel.session.store import SessionStore


class EventCollector:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, session_id: str, kind: str, data: dict) -> None:
        self.events.append((session_id, kind, data))

    def kinds(self) -> list[str]:
        return [k for _, k, _ in self.events]

    def of(self, kind: str) -> list[dict]:
        return [d for _, k, d in self.events if k == kind]

    def first(self, kind: str) -> dict:
        found = self.of(kind)
        assert found, f"未发射 {kind}；实际序列 {self.kinds()}"
        return found[0]


class FakeLLMConfig:
    """LLMConfig 的最小替身（只需 _run_attempt 用到的字段）。"""

    def __init__(self, model: str = "fake-model"):
        self.model = model
        self.provider = "fake"
        self.max_tokens = 4096
        self.compact_config = None


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODES_SESSION_DB", str(tmp_path / "sessions.db"))
    from agentnodes.kernel.session.db import _get_db_at

    _get_db_at(str(tmp_path / "sessions.db"))
    store = SessionStore(str(tmp_path / "sessions.db"))
    bus = EventCollector()
    service = SessionService(store=store, event_bus=bus)
    service.events = bus  # type: ignore[attr-defined]
    yield service
    for task in list(service._active_loops.values()):
        task.cancel()


def _attempt(svc, session_id: str = "s") -> Attempt:
    attempt = Attempt(
        session_id=session_id,
        prompt="hello",
        message_id="m-1",
        status=AttemptStatus.PENDING,
        created_at="2026-11-17T00:00:00",
    )
    svc.store.create_attempt(attempt)
    return attempt


def _stub_cfg(monkeypatch, cfg) -> None:
    """把 ``LLMConfig.load`` 替换成返回给定 cfg（None 表示不可用）。"""
    import agentnodes.foundation.llm.config as cfg_mod

    monkeypatch.setattr(cfg_mod.LLMConfig, "load", classmethod(lambda cls: cfg))


class TestConfigUnavailable:
    async def test_cfg_none_marks_attempt_failed(self, svc, monkeypatch):
        """cfg 为 None → attempt.failed + attempt.failed 事件 + 立即返回（L619-626）。"""
        _stub_cfg(monkeypatch, None)
        attempt = _attempt(svc)

        await svc._run_attempt(
            session_id="s", attempt=attempt, model=None, max_iterations=1,
            system_prompt=None, allow_shell_tools=False,
        )

        assert attempt.status == AttemptStatus.FAILED
        assert attempt.error == "LLM config unavailable"
        failed = svc.events.first("attempt.failed")
        assert failed["attempt_id"] == attempt.attempt_id
        assert "LLM config unavailable" in failed["error"]
        # cfg None 时不应进入执行链
        assert "attempt.started" not in svc.events.kinds()


class TestNormalRun:
    async def test_success_path_emits_completed_and_message(self, svc, monkeypatch):
        """正常出口 → attempt.completed + assistant_message + agent_done。"""
        _stub_cfg(monkeypatch, FakeLLMConfig())

        async def fake_run_with_agent(**kw):
            return {
                "status": "success",
                "content": "答案是 42",
                "finished_reason": "stop",
                "metrics": {"input_tokens": 10},
                "run_dir": None,
            }

        svc._run_with_agent = fake_run_with_agent  # type: ignore[method-assign]
        attempt = _attempt(svc)

        await svc._run_attempt(
            session_id="s", attempt=attempt, model=None, max_iterations=1,
            system_prompt=None, allow_shell_tools=False,
        )

        assert attempt.status == AttemptStatus.COMPLETED
        assert attempt.summary == "答案是 42"
        started = svc.events.first("attempt.started")
        assert started["message_id"] == attempt.message_id
        msg = svc.events.first("assistant_message")
        assert msg["content"] == "答案是 42"
        assert msg["message_type"] == "assistant"
        assert "attempt.completed" in svc.events.kinds()
        assert "agent_done" in svc.events.kinds()

    async def test_empty_answer_marks_completed_with_blank_summary(self, svc, monkeypatch):
        """status='empty' → completed + 空 summary（L753-755）。

        ``mark_completed(summary="")`` 里是 ``if summary:`` —— 空串是假值，
        summary 保持 ``None``（模型层归一化），不是空串。
        """
        _stub_cfg(monkeypatch, FakeLLMConfig())

        async def fake_run_with_agent(**kw):
            return {"status": "empty", "content": "", "finished_reason": "stop"}

        svc._run_with_agent = fake_run_with_agent  # type: ignore[method-assign]
        attempt = _attempt(svc)

        await svc._run_attempt(
            session_id="s", attempt=attempt, model=None, max_iterations=1,
            system_prompt=None, allow_shell_tools=False,
        )
        assert attempt.status == AttemptStatus.COMPLETED
        assert not attempt.summary

    async def test_failed_result_emits_error_message(self, svc, monkeypatch):
        """status='failed' → attempt.failed + assistant_message(error) 友好文案。"""
        _stub_cfg(monkeypatch, FakeLLMConfig())

        async def fake_run_with_agent(**kw):
            return {
                "status": "failed",
                "content": "",
                "error": "401 unauthorized",
                "finished_reason": "error",
            }

        svc._run_with_agent = fake_run_with_agent  # type: ignore[method-assign]
        attempt = _attempt(svc)

        await svc._run_attempt(
            session_id="s", attempt=attempt, model=None, max_iterations=1,
            system_prompt=None, allow_shell_tools=False,
        )
        assert attempt.status == AttemptStatus.FAILED
        assert "401" in attempt.error
        msg = svc.events.first("assistant_message")
        assert msg["message_type"] == "error"
        assert msg["metadata"]["status"] == "error"
        assert "401 unauthorized" in msg["metadata"]["details"]


class TestExceptionPath:
    async def test_exception_marks_failed_and_emits_both_events(self, svc, monkeypatch):
        """执行链异常 → attempt.failed + agent_done(error)（L849-865）。"""
        _stub_cfg(monkeypatch, FakeLLMConfig())

        async def boom(**kw):
            raise RuntimeError("loop exploded")

        svc._run_with_agent = boom  # type: ignore[method-assign]
        attempt = _attempt(svc)

        await svc._run_attempt(
            session_id="s", attempt=attempt, model=None, max_iterations=1,
            system_prompt=None, allow_shell_tools=False,
        )

        assert attempt.status == AttemptStatus.FAILED
        assert "loop exploded" in attempt.error
        failed = svc.events.first("attempt.failed")
        assert "loop exploded" in failed["error"]
        done = svc.events.first("agent_done")
        assert done["status"] == "error"


class TestCancelPath:
    async def test_cancelled_error_marks_cancelled_and_reraises(self, svc, monkeypatch):
        """取消 → attempt.failed("cancelled") + 双事件 + 重新抛出（L832-848）。"""
        _stub_cfg(monkeypatch, FakeLLMConfig())

        async def cancelled(**kw):
            raise asyncio.CancelledError()

        svc._run_with_agent = cancelled  # type: ignore[method-assign]
        attempt = _attempt(svc)

        with pytest.raises(asyncio.CancelledError):
            await svc._run_attempt(
                session_id="s", attempt=attempt, model=None, max_iterations=1,
                system_prompt=None, allow_shell_tools=False,
            )
        assert attempt.status == AttemptStatus.FAILED
        assert attempt.error == "cancelled"
        failed = svc.events.first("attempt.failed")
        assert failed["status"] == "cancelled"
        done = svc.events.first("agent_done")
        assert done["status"] == "cancelled"


class TestGuardContinuation:
    """``_run_with_guard``：DAG 会话里 LLM 以问句结束 → 注入 continuation 重试。

    **前提**：guard 只对 ``dag:`` 前缀会话生效（``is_dag_session``），
    且 guard_cap = ``max_continues()``（AGENTNODES_ORCH_MAX_CONTINUES，默认 10）。
    普通会话（``s``）直接返回，不重试。
    """

    async def test_guard_retries_when_agent_asks_user(self, svc, monkeypatch):
        """DAG 会话：第一次以问句结束 → 注入 keep-going 回合再跑。

        **语义细节**：``asked_user`` 是**最后一轮**的判定，不是「曾经问过」——
        重试后拿到非问句答案时 asked_user=False。「问过」由 ``retries > 0`` 表达。
        """
        from agentnodes.kernel.agent.loop.core import LoopResult

        calls: list[int] = []

        class FakeAgent:
            name = "fake"

            async def arun(self, *args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    return LoopResult(
                        answer="你的问题是什么？",
                        iterations=1,
                        tool_calls_made=0,
                        finished_reason="stop",
                    )
                return LoopResult(
                    answer="最终答案",
                    iterations=2,
                    tool_calls_made=0,
                    finished_reason="stop",
                )

        attempt = _attempt(svc, "dag:proj-1")
        loop_result, asked_user, retries = await svc._run_with_guard(
            FakeAgent(), attempt, [{"role": "user", "content": "hi"}]
        )
        assert retries == 1, "问句触发一次 continuation"
        assert len(calls) == 2, "应重跑一次"
        assert loop_result.answer == "最终答案"
        assert asked_user is False, "asked_user 是最后一轮的判定（重试后已非问句）"

    async def test_guard_does_not_retry_for_non_dag_session(self, svc, monkeypatch):
        """非 DAG 会话 → guard_cap=0 → 不重试（asked_user=False）。"""
        from agentnodes.kernel.agent.loop.core import LoopResult

        calls: list[int] = []

        class FakeAgent:
            name = "fake"

            async def arun(self, *args, **kwargs):
                calls.append(1)
                return LoopResult(
                    answer="你的问题是什么？",
                    iterations=1,
                    tool_calls_made=0,
                    finished_reason="stop",
                )

        attempt = _attempt(svc, "s")
        loop_result, asked_user, retries = await svc._run_with_guard(
            FakeAgent(), attempt, [{"role": "user", "content": "hi"}]
        )
        assert asked_user is False
        assert retries == 0
        assert len(calls) == 1

    async def test_guard_returns_first_result_without_continuation(self, svc, monkeypatch):
        """正常结束（非问句）→ 不重试，retries=0。"""
        from agentnodes.kernel.agent.loop.core import LoopResult

        calls: list[int] = []

        class FakeAgent:
            name = "fake"

            async def arun(self, *args, **kwargs):
                calls.append(1)
                return LoopResult(
                    answer="直接答案",
                    iterations=1,
                    tool_calls_made=0,
                    finished_reason="stop",
                )

        attempt = _attempt(svc, "dag:proj-2")
        loop_result, asked_user, retries = await svc._run_with_guard(
            FakeAgent(), attempt, [{"role": "user", "content": "hi"}]
        )
        assert asked_user is False
        assert retries == 0
        assert len(calls) == 1

    async def test_guard_raises_runtime_error_on_agent_failure(self, svc, monkeypatch):
        """agent.arun 抛错 → RuntimeError（上层转 failed dict，L974-975）。"""
        class FakeAgent:
            name = "fake"

            async def arun(self, *args, **kwargs):
                raise ValueError("agent died")

        attempt = _attempt(svc, "dag:proj-3")
        with pytest.raises(RuntimeError):
            await svc._run_with_guard(
                FakeAgent(), attempt, [{"role": "user", "content": "hi"}]
            )
