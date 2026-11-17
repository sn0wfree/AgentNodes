"""批 D：SessionService 的 LLM 链与纯逻辑（compact_history / 摘要提取 / emit）。

**注入面核查结论**（方案 §5 风险 4 已要求先做）：
- ``compact_messages``：模块顶层 import → monkeypatch ``service.compact_messages``
- ``OpenAICompatClient``：函数内 import → monkeypatch
  ``agentnodes.foundation.llm.OpenAICompatClient``
- ``LLMConfig.load``：类方法 → monkeypatch
- ``_extract_summary`` / ``_build_fallback_summary``：staticmethod → 直接调用

覆盖方案 §3 批 D：摘要提取三格式 / fallback 摘要 / compact_history 成功与
空历史守卫 / compact 事件序列 / emit 闭包的 message_id 注入。
"""
from __future__ import annotations

import pytest

from agentnodes.kernel.session.models import Attempt, AttemptStatus, Message
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


class TestExtractSummary:
    """``_extract_summary``：三种存储格式的解析。"""

    def test_new_format_compaction_parts(self):
        """新格式：message_type=compaction + parts_json 里的 compaction part。"""
        msgs = [{
            "message_type": "compaction",
            "content": "占位",
            "parts_json": '[{"type": "compaction", "summary": "真正的摘要"}]',
        }]
        assert SessionService._extract_summary(msgs) == "真正的摘要"

    def test_new_format_falls_back_to_content_when_no_compaction_part(self):
        """新格式但无 compaction part → 回退用 content。"""
        msgs = [{
            "message_type": "compaction",
            "content": "直接就是摘要",
            "parts_json": '[{"type": "text", "text": "x"}]',
        }]
        assert SessionService._extract_summary(msgs) == "直接就是摘要"

    def test_new_format_tolerates_broken_parts_json(self):
        """parts_json 坏掉 → 不抛，回退 content。"""
        msgs = [{
            "message_type": "compaction",
            "content": "内容",
            "parts_json": "{not json",
        }]
        assert SessionService._extract_summary(msgs) == "内容"

    def test_legacy_context_summary_prefix(self):
        """旧格式：``[context summary]`` 前缀。"""
        msgs = [{"role": "user", "content": "[context summary] 旧的摘要"}]
        assert SessionService._extract_summary(msgs) == "旧的摘要"

    def test_legacy_anchored_summary_prefix(self):
        """旧格式：``## Anchored Summary`` / ``## Objective`` 前缀。"""
        a = SessionService._extract_summary(
            [{"role": "user", "content": "## Anchored Summary\nxxx"}]
        )
        b = SessionService._extract_summary(
            [{"role": "user", "content": "## Objective\nyyy"}]
        )
        assert a.startswith("## Anchored Summary")
        assert b.startswith("## Objective")

    def test_non_summary_messages_ignored(self):
        """普通消息不进摘要。"""
        msgs = [{"role": "user", "content": "普通提问"}]
        assert SessionService._extract_summary(msgs) == ""

    def test_multiple_summaries_joined(self):
        """多条摘要用空行连接。"""
        msgs = [
            {"message_type": "compaction", "content": "A", "parts_json": "[]"},
            {"message_type": "compaction", "content": "B", "parts_json": "[]"},
        ]
        assert SessionService._extract_summary(msgs) == "A\n\nB"


class TestFallbackSummary:
    """``_build_fallback_summary``：LLM 摘要不可用时的结构化兜底。"""

    def test_structure_and_objective(self):
        original = [
            {"role": "user", "content": "我要研究动量因子"},
            {"role": "assistant", "content": "已完成因子筛选，" + "x" * 30},
        ]
        out = SessionService._build_fallback_summary([], original)
        assert "## Objective" in out
        assert "我要研究动量因子" in out
        assert "## Important Details" in out
        assert "## Work State" in out
        assert "## Next Move" in out

    def test_skips_compaction_and_legacy_messages(self):
        """compaction 与旧格式摘要消息不进片段收集。"""
        original = [
            {"role": "user", "content": "[context summary] 旧摘要"},
            {"role": "assistant", "content": "compaction 类型", "message_type": "compaction"},
            {"role": "assistant", "content": "真正的工作" + "y" * 30},
        ]
        out = SessionService._build_fallback_summary([], original)
        assert "旧摘要" not in out
        assert "compaction 类型" not in out
        assert "真正的工作" in out

    def test_short_assistant_message_ignored(self):
        """assistant 内容 ≤20 字不算工作片段。"""
        original = [{"role": "assistant", "content": "好的"}]
        out = SessionService._build_fallback_summary([], original)
        assert "### Completed\n- (none)" in out

    def test_removed_count_reported(self):
        """压缩掉了消息 → 报 removed 数量。"""
        original = [{"role": "user", "content": f"q{i}"} for i in range(5)]
        out = SessionService._build_fallback_summary([], original)
        assert "- (none)" in out, "无压缩时 removed 为 none"

        out2 = SessionService._build_fallback_summary([{"role": "user", "content": "q0"}], original)
        assert "4 messages removed during compaction" in out2

    def test_objective_none_when_no_user_message(self):
        original = [{"role": "assistant", "content": "只有助手消息" + "z" * 30}]
        out = SessionService._build_fallback_summary([], original)
        assert "- (none)" in out


class TestCompactHistory:
    async def test_empty_history_returns_zero(self, svc):
        """历史为空 → 零值返回（不进 LLM 链）。"""
        res = await svc.compact_history("s-empty")
        assert res == {
            "layers": [], "before_tokens": 0, "after_tokens": 0, "summary": "",
        }

    async def test_compaction_emits_count_and_ended(self, svc, monkeypatch):
        """压缩成功 → compact.count + compact.ended 事件（含 compacted_until_seq）。"""
        import agentnodes.kernel.session.service as svc_mod

        def fake_compact_messages(history, **kw):
            # 压缩掉前两条，保留最近一条（seq=3）
            compressed = [{"role": "user", "content": "最近的问题", "seq": 3}]
            return compressed, ["L1"], None, None

        monkeypatch.setattr(svc_mod, "compact_messages", fake_compact_messages)

        # 造**两条**消息：``_convert_messages_to_history`` 排除当前轮
        # （``messages[:-1]``）——只有一条时 history 为空，压缩不会发生。
        svc.store.get_messages = lambda session_id, limit=10000: [  # type: ignore[method-assign]
            Message(message_id="m1", session_id=session_id, role="user",
                    content="问题一", created_at=1.0, metadata={"seq": 1}),
            Message(message_id="m2", session_id=session_id, role="user",
                    content="问题二", created_at=2.0, metadata={"seq": 2}),
        ]

        res = await svc.compact_history("s1")
        assert res["layers"] == ["L1"]
        assert res["summary"], "无 LLM 摘要时走 fallback 摘要"

        count = svc.events.first("compact.count")
        assert count["messages_before"] == 2
        assert count["layers"] == ["L1"]

        ended = svc.events.first("compact.ended")
        assert ended["layers"] == ["L1"]
        assert ended["summary"]

    async def test_compact_event_failure_is_swallowed(self, svc, monkeypatch):
        """事件发射抛错 → 记录日志但不抛出（L1337-1338）。"""
        import agentnodes.kernel.session.service as svc_mod

        def fake_compact_messages(history, **kw):
            return [{"role": "user", "content": "x", "seq": 1}], ["L1"], None, None

        monkeypatch.setattr(svc_mod, "compact_messages", fake_compact_messages)

        def boom(*args, **kwargs):
            raise RuntimeError("emit failed")

        svc.event_bus.emit = boom  # type: ignore[method-assign]
        # 两条消息（排除当前轮后仍有 1 条进 history）
        svc.store.get_messages = lambda session_id, limit=10000: [  # type: ignore[method-assign]
            Message(message_id="m1", session_id=session_id, role="user",
                    content="q1", created_at=1.0, metadata={"seq": 1}),
            Message(message_id="m2", session_id=session_id, role="user",
                    content="q2", created_at=2.0, metadata={"seq": 2}),
        ]
        res = await svc.compact_history("s2")
        assert res["layers"] == ["L1"], "事件失败不影响返回值"


class TestEmitClosure:
    async def test_emit_injects_message_and_attempt_id(self, svc):
        """``_run_test_script`` 的 emit 闭包自动补 message_id / attempt_id。"""
        attempt = Attempt(
            session_id="s",
            prompt="测试提示",
            message_id="msg-42",
            status=AttemptStatus.PENDING,
            created_at="2026-11-17T00:00:00",
        )
        svc.store.create_attempt(attempt)
        parts: list[dict] = []

        res = await svc._run_test_script(attempt=attempt, accumulated_parts=parts)

        assert res["status"] == "success"
        assert res["content"]
        assert parts and parts[0]["type"] == "text"
        kinds = svc.events.kinds()
        assert "iter_start" in kinds
        assert "text_delta" in kinds
        assert "text.ended" in kinds
        # 每个事件都带 message_id / attempt_id
        for _, _, data in svc.events.events:
            assert data.get("message_id") == "msg-42"
            assert data.get("attempt_id") == attempt.attempt_id
