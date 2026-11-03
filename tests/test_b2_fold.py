"""B2 fold 重建测试——重启恢复状态（不恢复执行）。

设计：docs/stage-b2-fold-recovery-design-20261030.md
  A. turn 计数恢复（下一 turn 续号，不撞号）
  B. session_key → uuid 映射恢复（同 key 落回同一 aggregate）
  C. interrupted 标记（started 无 settled → run.interrupted；幂等）
  D. 正常收尾不误标
  E. 无 store no-op；auto_resume=false（mailbox 仍空）
  F. 端到端：重启 → recover → 提交续号
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from strategy_research.kernel.gateway import Gateway
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


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "platform"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_three_turns(gw: Gateway) -> str:
    """跑 3 个 turn，返回 EntryPoint 的 session uuid。"""
    for i in range(3):
        gw.entry.submit(chat(f"q{i}"))
    gw.run_once({WorkKind.CHAT})
    sid = gw.entry.session_id_for("chat:s")
    assert sid
    return sid


# ── A. turn 计数恢复 ─────────────────────────────────────


def test_turn_counter_restored_after_restart(data_dir):
    gw1 = Gateway(client=FakeClient(), data_dir=data_dir)
    sid = _run_three_turns(gw1)
    gw1.close()

    gw2 = Gateway(client=FakeClient(), data_dir=data_dir)
    try:
        stats = gw2.recover()
        assert stats["turns"] >= 1
        assert gw2.controller._turn_counters.get(sid) == 3
    finally:
        gw2.close()


# ── B. session_key 映射恢复 ──────────────────────────────


def test_session_key_mapping_restored(data_dir):
    gw1 = Gateway(client=FakeClient(), data_dir=data_dir)
    sid = _run_three_turns(gw1)
    gw1.close()

    gw2 = Gateway(client=FakeClient(), data_dir=data_dir)
    try:
        gw2.recover()
        # 同 key 落回同一 aggregate（不生成新 uuid）
        assert gw2.entry.session_id_for("chat:s") == sid
    finally:
        gw2.close()


# ── C. interrupted 标记 ──────────────────────────────────


def test_interrupted_run_marked_and_idempotent(data_dir):
    gw1 = Gateway(client=FakeClient(), data_dir=data_dir)
    sid = _run_three_turns(gw1)
    # 伪造一个「started 但没 settled」的 run（模拟重启时在跑）
    gw1._store.emit(sid, "run.started", {"run_id": f"{sid}#4:r0"})
    gw1.close()

    gw2 = Gateway(client=FakeClient(), data_dir=data_dir)
    try:
        stats = gw2.recover()
        assert stats["interrupted"] == 1
        events = gw2._store.replay(sid, kinds=["run.interrupted"])
        assert len(events) == 1
        assert events[0].data["run_id"] == f"{sid}#4:r0"
        assert events[0].data["reason"] == "restart"

        # 幂等：第二次 recover 不重复标记
        stats2 = gw2.recover()
        assert stats2["interrupted"] == 0
        assert len(gw2._store.replay(sid, kinds=["run.interrupted"])) == 1
    finally:
        gw2.close()


# ── D. 正常收尾不误标 ────────────────────────────────────


def test_settled_runs_not_marked(data_dir):
    gw1 = Gateway(client=FakeClient(), data_dir=data_dir)
    sid = _run_three_turns(gw1)
    gw1.close()

    gw2 = Gateway(client=FakeClient(), data_dir=data_dir)
    try:
        stats = gw2.recover()
        assert stats["interrupted"] == 0
    finally:
        gw2.close()


# ── E. 无 store / auto_resume=false ──────────────────────


def test_no_store_is_noop():
    gw = Gateway(client=FakeClient())  # 无 data_dir
    stats = gw.recover()
    assert stats == {"sessions": 0, "turns": 0, "interrupted": 0}


def test_no_auto_resume(data_dir):
    """定稿：恢复状态，不恢复执行——mailbox 不重建。"""
    gw1 = Gateway(client=FakeClient(), data_dir=data_dir)
    gw1.entry.submit(chat("queued"))  # 不消费，留在队里
    gw1.close()

    gw2 = Gateway(client=FakeClient(), data_dir=data_dir)
    try:
        gw2.recover()
        assert gw2.entry.mailbox_for("chat:s") is None  # 队列未重建
    finally:
        gw2.close()


# ── F. 端到端：重启 → recover → 续号 ─────────────────────


def test_end_to_end_resume_numbering(data_dir):
    gw1 = Gateway(client=FakeClient(), data_dir=data_dir)
    sid = _run_three_turns(gw1)
    gw1.close()

    gw2 = Gateway(client=FakeClient(), data_dir=data_dir)
    try:
        gw2.recover()
        gw2.entry.submit(chat("new"))
        runs = gw2.run_once({WorkKind.CHAT})
        assert len(runs) == 1 and runs[0].ok
        # 续号 #4，不撞历史
        assert runs[0].turn_id.endswith("#4")
        # 事件落在同一 aggregate（session_key 映射恢复生效）
        events = gw2._store.replay(sid, kinds=["turn.admitted"])
        assert events[-1].data["turn_id"] == runs[0].turn_id
    finally:
        gw2.close()
