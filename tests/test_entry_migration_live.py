"""阶段 3a 灰度验证：flag 打开后 /send 走新架构（真实 MiniMax）。

运行：
    $env:QNODES_LIVE_LLM="1";
    py -3.12 -m pytest tests/test_entry_migration_live.py -m llm -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.llm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

requires_live = pytest.mark.skipif(
    os.environ.get("QNODES_LIVE_LLM") != "1",
    reason="live LLM tests are opt-in: set QNODES_LIVE_LLM=1",
)


@requires_live
def test_gateway_path_real_llm_via_flag(live_client, monkeypatch):
    """flag 打开 → api_chat_request → Gateway → 真实回答。"""
    from strategy_research.core.gateway import Gateway, api_chat_request


    events: list[str] = []
    gw = Gateway(client=live_client, sink=lambda k, d: events.append(k))
    gw.verify_invariants()

    receipt = gw.entry.submit(api_chat_request("live-sess", "用一个词回答：天空是什么颜色？"))
    assert receipt.accepted

    runs = gw.run_once()
    assert runs and runs[0].ok, runs[0].error if runs else "no run"
    assert runs[0].answer.strip()
    # 事件流完整
    for expected in ("steer.became_turn", "work.enqueued", "turn.admitted",
                     "run.started", "node.started", "node.completed", "run.settled"):
        assert expected in events, expected


@requires_live
def test_gateway_two_turns_same_session(live_client, monkeypatch):
    """同一 session 两轮：turn 编号递增，run 派生正确。"""
    from strategy_research.core.gateway import Gateway, api_chat_request

    gw = Gateway(client=live_client)
    gw.entry.submit(api_chat_request("s2", "回答：1+1=?"))
    [r1] = gw.run_once()
    gw.entry.submit(api_chat_request("s2", "回答：2+2=?"))
    [r2] = gw.run_once()

    assert r1.ok and r2.ok
    assert r1.turn_id.endswith("#1") and r2.turn_id.endswith("#2")
    assert r1.turn_id.split("#")[0] == r2.turn_id.split("#")[0]  # 同 session
