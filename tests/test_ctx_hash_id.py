"""ctx_hash_id：内容绑定的上下文指纹（统一引擎 U6）。

**契约**（docs/design/ctx-hash-id-design-20261112.md）：

    ctx_hash_id = f"{trace_id}_{system_hash}_{tools_hash}_{c_digest}"

四段全 hex → ``split("_")`` 可无损反解；差异来源一眼可辨。

**九组断言**：
1. 同一上下文两次算 → 同 id（确定性）
2. messages 内容改（**长度不变**）→ c 段变（内容级 vs 长度级）
3. tools 改 → tools 段变；system 改 → system 段变
4. 跨进程一致
5. 四段全 hex、split 可反解
6. 每轮 id 随上下文变；同一 run 内 trace_id 段不变
7. trace 记录里 llm_request 带 ctx_hash_id
8. 事件载荷带 ctx_hash_id（当前状态）
9. 性能：1000 条消息 c_digest < 5ms
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentnodes.foundation.llm import LLMConfig, LLMResponse
from agentnodes.kernel.agent.agent_telemetry import (
    compose_ctx_hash_id,
    compute_c_digest,
)
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop import AgentLoop

ROOT = Path(__file__).resolve().parents[1]

# **R-017 隔离标记**（2026-11-13）：本文件含一个**纯计时**断言
# （c_digest 1000 条 < 20ms）。串行稳定通过，但 ``-n 12`` 满载时 12 个
# worker 抢 CPU 会让它偶发超限——同组固定到一个 worker，降低争抢概率。
pytestmark = pytest.mark.xdist_group("ctx_hash_id")


def _msgs(*pairs):
    return [{"role": r, "content": c} for r, c in pairs]


SYS = "你是任务执行助手。"
TOOLS = json.dumps([{"name": "read"}], sort_keys=True)


def _compose(messages, trace_id="a1b2c3d4e5f6", system=SYS, tools=TOOLS):
    import hashlib
    s = hashlib.sha256(system.encode()).hexdigest()[:16]
    t = hashlib.sha256(tools.encode()).hexdigest()[:16]
    return compose_ctx_hash_id(trace_id, s, t, compute_c_digest(messages))


# ── 1. 确定性 ──


def test_same_context_same_id():
    m = _msgs(("system", SYS), ("user", "帮我分析因子"))
    assert _compose(m) == _compose(m)


# ── 2. 内容级（等长内容改变 → c 段变）──


def test_content_change_same_length_changes_c_digest():
    base = _msgs(("system", SYS), ("user", "u"), ("assistant", "AAAA"))
    changed = _msgs(("system", SYS), ("user", "u"), ("assistant", "BBBB"))
    assert len(base[2]["content"]) == len(changed[2]["content"])
    assert compute_c_digest(base) != compute_c_digest(changed), (
        "等长内容改变必须让 c_digest 变化——"
        "长度级指纹（history_hash）在这里完全失效"
    )


def test_c_digest_excludes_system():
    """c_digest 不含 messages[0]（system 有独立分量，三段职责正交）。"""
    a = _msgs(("system", "S1"), ("user", "u"))
    b = _msgs(("system", "S2"), ("user", "u"))
    assert compute_c_digest(a) == compute_c_digest(b), (
        "c_digest 不该包含 system——否则 system 段与 c 段会同时变"
    )


# ── 3. 分段定位 ──
# 段序：0=trace_id  1=system  2=tools  3=c_digest


def test_system_change_affects_only_system_segment():
    m = _msgs(("system", SYS), ("user", "u"))
    b = _compose(m).split("_")
    o = _compose(m, system="另一个助手").split("_")
    assert b[1] != o[1], "system 段应变"
    assert b[0] == o[0], "trace_id 段不应变"
    assert b[2] == o[2] and b[3] == o[3], "只有 system 段应变"


def test_tools_change_affects_only_tools_segment():
    m = _msgs(("system", SYS), ("user", "u"))
    other_tools = json.dumps([{"name": "write"}], sort_keys=True)
    b = _compose(m).split("_")
    o = _compose(m, tools=other_tools).split("_")
    assert b[2] != o[2], "tools 段应变"
    assert b[0] == o[0], "trace_id 段不应变"
    assert b[1] == o[1] and b[3] == o[3], "只有 tools 段应变"


def test_trace_id_change_affects_only_trace_segment():
    m = _msgs(("system", SYS), ("user", "u"))
    b = _compose(m, trace_id="a1b2c3d4e5f6").split("_")
    o = _compose(m, trace_id="ffffffffffff").split("_")
    assert b[0] != o[0], "trace_id 段应变"
    assert b[1:] == o[1:], "只有 trace_id 段应变"


# ── 4. 跨进程一致 ──


def test_cross_process_consistent():
    """子进程算的 c_digest 与主进程相同（确定性序列化）。"""
    # 注意 c_digest 跳过 messages[0]（system）→ 两边必须用**同一组**消息
    msgs_literal = (
        "[{'role':'system','content':%r},"
        "{'role':'user','content':'u'},"
        "{'role':'assistant','content':'A','tool_calls':None}]" % SYS
    )
    code = (
        "import sys; sys.path.insert(0, r'%s\\src');"
        "from agentnodes.kernel.agent.agent_telemetry import compute_c_digest;"
        "m=%s;"
        "print(compute_c_digest(m))" % (str(ROOT), msgs_literal)
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=120)
    assert r.returncode == 0, r.stderr
    m = _msgs(("system", SYS), ("user", "u"), ("assistant", "A"))
    assert r.stdout.strip() == compute_c_digest(m), "跨进程不一致"


# ── 5. 形态 ──


def test_four_segments_hex_and_splittable():
    cid = _compose(_msgs(("system", SYS), ("user", "u")))
    parts = cid.split("_")
    assert len(parts) == 4, f"应 4 段：{cid}"
    assert [len(p) for p in parts] == [12, 16, 16, 16], f"段长：{cid}"
    for p in parts:
        int(p, 16)  # 必须 hex


# ── 6/7/8. 引擎内行为 ──


class _Mock:
    def __init__(self, text="x"):
        self.text = text

    def chat(self, messages, **kw):
        return LLMResponse(content=self.text, tool_calls=[], finish_reason="stop")

    async def achat(self, messages, **kw):
        return self.chat(messages, **kw)

    async def astream(self, messages, tools=None, **kw):
        from agentnodes.foundation.llm.parser import StreamChunk
        yield StreamChunk(delta_content=self.text, finish_reason="stop")

    def stream(self, messages, tools=None, **kw):
        from agentnodes.foundation.llm.parser import StreamChunk

        def _gen():
            yield StreamChunk(delta_content=self.text, finish_reason="stop")
        return _gen()


def _make_loop(tmp_path: Path, text="x"):
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=tmp_path,
        trace_dir=tmp_path / "traces",
        strategy={"name": "react", "config": {"max_iterations": 3}},
    )
    m = _Mock(text)
    loop.client.chat = m.chat
    loop.client.achat = m.achat
    loop.client.stream = m.stream
    loop.client.astream = m.astream
    return loop


def test_engine_updates_ctx_hash_id_every_iteration(tmp_path: Path):
    """每轮 id 随上下文变；同一 run 内 trace_id 段不变。"""
    loop = _make_loop(tmp_path)
    seen: list[str] = []
    loop._on_event = lambda kind, payload=None: (
        seen.append(payload.get("ctx_hash_id", ""))
        if kind == "llm_request" else None
    )
    asyncio.run(loop.arun("测试"))
    ids = [s for s in seen if s]
    assert ids, "llm_request 事件未带 ctx_hash_id"
    assert len(set(ids)) >= 1
    # trace_id 段在 run 内不变
    assert len({i.split("_")[0] for i in ids}) == 1, (
        f"同一 run 内 trace_id 段应不变：{ids}"
    )


def test_result_carries_current_ctx_hash_id(tmp_path: Path):
    """result.ctx_hash_id = 当前状态的指纹（非空）。"""
    loop = _make_loop(tmp_path)
    r = asyncio.run(loop.arun("测试"))
    assert r.ctx_hash_id, "result 未带 ctx_hash_id"
    assert len(r.ctx_hash_id.split("_")) == 4


def test_trace_record_has_ctx_hash_id(tmp_path: Path):
    """trace.jsonl 的 llm_request 记录带 ctx_hash_id。"""
    loop = _make_loop(tmp_path)
    asyncio.run(loop.arun("测试"))
    assert loop._trace_writer is not None
    path = Path(loop._trace_writer.path)
    recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    reqs = [x for x in recs if x.get("type") == "llm_request"]
    assert reqs, "无 llm_request 记录"
    assert reqs[0].get("ctx_hash_id"), "llm_request 记录未带 ctx_hash_id"


def test_other_events_carry_ctx_hash_id(tmp_path: Path):
    """非 LLM 请求的事件也带当前状态的 ctx_hash_id（统一附加）。"""
    loop = _make_loop(tmp_path)
    events: list[tuple[str, str]] = []
    loop._on_event = lambda kind, payload=None: events.append(
        (kind, (payload or {}).get("ctx_hash_id", ""))
    )
    asyncio.run(loop.arun("测试"))
    with_hash = [(k, v) for k, v in events if v]
    assert with_hash, "没有事件带 ctx_hash_id"
    # llm_request 之后的事件也带（说明是「当前状态」而非「仅 llm_request 自带」）
    kinds = [k for k, v in events]
    assert "llm_request" in kinds
    idx = kinds.index("llm_request")
    after = [(k, v) for k, v in events[idx + 1:] if v]
    assert after, "llm_request 之后的事件未带 ctx_hash_id"


# ── 9. 性能 ──


def test_c_digest_performance():
    """1000 条消息 c_digest < 20ms（相对一次 LLM 请求 ~6%）。

    **阈值 20ms 的依据**（2026-11-13 实测，空载 n=30）：
    min 1.38ms / p50 1.44ms / p90 2.35ms / max 2.39ms。原阈值 5ms 只有
    **2.1x(max) 余量**——``-n 12`` 满载时 12 个 worker 抢 CPU，偶发超限
    （实测全量回归间歇红，串行 13/13 全绿 → R-017 隔离问题，非缺陷）。
    20ms ≈ 实测 p50 的 13 倍、一次 LLM 请求（~300ms）的 6.7%——**原设计
    意图「c_digest 相对 LLM 请求可忽略」依然成立**，同时不再被并行负载误伤。
    """
    m = [{"role": "user", "content": "x" * 200} for _ in range(1000)]
    t0 = time.perf_counter()
    compute_c_digest(m)
    dt = time.perf_counter() - t0
    assert dt < 0.020, f"c_digest 1000 条耗时 {dt*1000:.2f}ms，超 20ms 预算"
