"""E2E 全链路测试: 真实 AgentLoop + 真实工具 + 确定性离线数据。

守护 examples/e2e_flow.py 验证过的链路:
  因子 → 回测 → Goal 闭环(create/add_evidence(auto-attach)/complete)
+ trace_dir 落盘 / 失败路径 / no_progress 终止。

离线 seed price_data 保证 CI 确定性; 网络取数单测独立冒烟(无网 skip)。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import shutil
from pathlib import Path

import pytest

from strategy_research.kernel.agent.builtin_tools import build_default_registry
from strategy_research.kernel.agent.loop import AgentLoop
from strategy_research.foundation.llm import LLMConfig, LLMResponse, ToolCall

REPO = Path(__file__).resolve().parents[1]
CODES = ["600519.SH", "000858.SZ", "000001.SZ"]
N_DAYS = 600


# ── Helpers ──────────────────────────────────────────────────────────


def tc(name: str, **args) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=dict(args))


def resp(content: str, calls: list[ToolCall] | None = None) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=calls or [],
        finish_reason="tool_calls" if calls else "stop",
    )


class ScriptedLLM:
    """按轮次消费预定义响应; 耗尽后抛错 (暴露迭代数超出预期)。"""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[int] = []

    def chat(self, messages, **kwargs):
        self.calls.append(len(messages))
        if not self.responses:
            raise RuntimeError("ScriptedLLM exhausted — unexpected extra iteration")
        return self.responses.pop(0)


def make_loop(ws: Path, llm, *, trace_dir: Path | None = None, max_iterations: int = 12):
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-e2e"),
        registry=build_default_registry(ws),
        workspace=ws,
        max_iterations=max_iterations,
        trace_dir=trace_dir,
        session_id="e2e-test",
        # 离线 e2e：无人值守审批。gate 若触发（连续相同工具调用），
        # 1s 超时后按 policy 收尾——否则 no_progress 用例会等 1800s。
        approval_timeout=1.0,
        approval_on_timeout="reject",
    )
    loop.client.chat = llm.chat
    return loop


def seed_price_data(ws: Path, codes: list[str] | None = None, n: int = N_DAYS) -> None:
    """确定性合成 OHLCV 写入 workspace DuckDB (跳过周末)."""
    import duckdb

    codes = codes or CODES
    con = duckdb.connect(str(ws / "data.duckdb"))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS price_data(
            strategy_name VARCHAR, asset_code VARCHAR, date DATE,
            open DOUBLE, high DOUBLE, low DOUBLE, "close" DOUBLE,
            volume DOUBLE, PRIMARY KEY(strategy_name, asset_code, date))
        """
    )
    day = dt.date(2022, 1, 3)
    rows: list[tuple] = []
    for i, code in enumerate(codes):
        price = 100.0 + i * 10.0
        d = day
        j = 0
        while j < n:
            if d.weekday() < 5:
                drift = 0.0005 + 0.002 * math.sin(i * 3 + j)
                price *= 1.0 + drift
                rows.append(
                    ("default", code, d, round(price * 0.99, 3),
                     round(price * 1.01, 3), round(price * 0.98, 3),
                     round(price, 3), float(1_000_000 + i * 100_000))
                )
                j += 1
            d += dt.timedelta(days=1)
    con.executemany(
        "INSERT INTO price_data VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    con.close()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    for d in ("strategies", "templates", "memory", "logs", "data", "tools/combo"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    strat = ws / "strategies" / "rotation_demo"
    strat.mkdir(parents=True)
    cfg = (REPO / "templates" / "config.yaml").read_text()
    cfg = cfg.replace("{strategy_name}", "rotation_demo").replace("{strategy_type}", "rotation")
    cfg = cfg.replace("incremental: true", "incremental: false")
    (strat / "config.yaml").write_text(cfg)
    shutil.copy(REPO / "templates" / "strategy.py", strat / "strategy.py")
    shutil.copy(REPO / "templates" / "prepare.py", strat / "prepare.py")
    seed_price_data(ws)
    return ws


def parse(result: str) -> dict:
    return json.loads(result)


# ── 全链路 (离线确定性) ──────────────────────────────────────────────


class TestFullFlow:
    def test_factor_backtest_goal_loop(self, workspace):
        """因子 → 回测 → Goal 闭环, 产物落库."""
        llm = ScriptedLLM([
            resp("计算动量因子。", [tc("compute_factor",
                                      factor_code="ts_mean(close, 20) / ts_mean(close, 60) - 1",
                                      factor_name="momentum_20_60", asset="600519.SH")]),
            resp("运行回测。", [tc("run_backtest", strategy_name="rotation_demo",
                                  description="e2e test")]),
            resp("创建目标。", [tc("create_goal",
                                  objective="E2E 目标",
                                  criteria=["回测完成", "因子验证"])]),
            resp("补充证据。", [tc("add_evidence", text="回测与因子均已产出。")]),
            resp("完成目标。", [tc("complete_goal", recap="闭环达成。")]),
            resp("全部完成。"),
        ])
        loop = make_loop(workspace, llm)
        r = loop.run("完整研究流程")

        assert r.finished_reason == "stop"
        assert r.tool_calls_made == 5
        assert r.iterations == 6

        import duckdb
        con = duckdb.connect(str(workspace / "data.duckdb"), read_only=True)
        assert con.execute("SELECT count(*) FROM price_data").fetchone()[0] >= 3 * N_DAYS
        assert con.execute("SELECT count(*) FROM backtest_results").fetchone()[0] == 1
        con.close()

        runs = sorted((workspace / "strategies" / "rotation_demo" / "runs").glob("run_*"))
        assert len(runs) == 1
        run_dir = runs[0]
        for f in ("config.yaml", "strategy.py", "metrics.json", "run_card.md", "run_card.json"):
            assert (run_dir / f).exists(), f"{f} 缺失"
        metrics = json.loads((run_dir / "metrics.json").read_text())
        assert "ann_return" in metrics



    def test_missing_required_arg_error_json(self, workspace):
        """LLM 漏必填参数 → TypeError 框架兜底 (hint), loop 继续并自愈."""
        llm = ScriptedLLM([
            resp("缺 codes 调用。", [tc("get_market_data", start_date="2023-01-01")]),
            resp("收到错误, 修正参数。", [tc("get_market_data", codes=["600519.SH"],
                                              start_date="2023-01-01", end_date="2023-12-31")]),
            resp("完成。"),
        ])
        loop = make_loop(workspace, llm)
        r = loop.run("取数")
        assert r.finished_reason == "stop"
        tool_evidences = [m for m in r.messages if m.get("role") == "tool"]
        first = parse(tool_evidences[0]["content"])
        assert first["status"] == "error"
        assert "TypeError" in first["error"]
        assert "hint" in first
        second = parse(tool_evidences[1]["content"])
        assert second["status"] == "ok"

    def test_no_progress_termination(self, workspace):
        """重复相同工具调用 → 审批 gate 触发 → 超时按 policy 收尾。

        历史注记：本用例最初断言 finished_reason == "no_progress"——
        该终止路径后来被 no-progress 审批 gate 取代（连续相同工具
        调用改为请求人工批准，超时按 policy 处理）。离线 e2e 无人
        守批 → 1s 超时 + reject policy → approval_timeout_rejected。
        「重复调用会被拦截」的守护意图不变。
        """
        same = tc("get_market_data", codes=["600519.SH"],
                  start_date="2023-01-01", end_date="2023-12-31")
        llm = ScriptedLLM([
            resp("再取一次。", [same]),
            resp("再取一次。", [same]),
            resp("再取一次。", [same]),
            resp("再取一次。", [same]),
            resp("再取一次。", [same]),
            resp("再取一次。", [same]),
        ])
        loop = make_loop(workspace, llm, max_iterations=12)
        r = loop.run("重复")
        assert r.finished_reason == "approval_timeout_rejected"
        assert r.iterations <= 8


# ── trace 集成 ───────────────────────────────────────────────────────


class TestTraceIntegration:
    def test_trace_events_complete_sequence(self, workspace, tmp_path):
        """trace_dir 接线 → 事件序列完整: loop_start/iter/llm/tool/loop_end/final."""
        trace_dir = tmp_path / "trace"
        llm = ScriptedLLM([
            resp("取数。", [tc("get_market_data", codes=["600519.SH"],
                              start_date="2023-01-01", end_date="2023-12-31")]),
            resp("完成。"),
        ])
        loop = make_loop(workspace, llm, trace_dir=trace_dir)
        r = loop.run("取数流程")
        assert r.finished_reason == "stop"

        lines = (trace_dir / "trace.jsonl").read_text().strip().splitlines()
        events = [json.loads(ln) for ln in lines]
        kinds = [ev["type"] for ev in events]

        assert kinds[0] == "loop_start"
        assert kinds[-2] == "loop_end"
        assert kinds[-1] == "loop_final"
        assert kinds.count("iter_start") == 2
        assert kinds.count("llm_response") == 2
        assert kinds.count("tool_result") == 1

        tool = [ev for ev in events if ev["type"] == "tool_result"][0]
        assert tool["tool"] == "get_market_data"
        assert tool["call_id"] == "call_get_market_data"
        assert tool["status"] in ("done", "error")
        assert tool["iteration"] >= 1
        assert tool["elapsed_ms"] >= 0

        assert events[-1]["tool_calls_made"] == 1
        assert events[-1]["reason"] == "stop"

    def test_loop_final_tool_calls_match(self, workspace, tmp_path):
        """loop_final 的 tool_calls_made 与 llm_response 的调用数一致."""
        trace_dir = tmp_path / "trace"
        llm = ScriptedLLM([
            resp("取数。", [tc("get_market_data", codes=["600519.SH"],
                              start_date="2023-01-01", end_date="2023-12-31")]),
            resp("再取数。", [tc("get_market_data", codes=["000858.SZ"],
                                start_date="2023-01-01", end_date="2023-12-31")]),
            resp("完成。"),
        ])
        loop = make_loop(workspace, llm, trace_dir=trace_dir)
        r = loop.run("两次取数")
        assert r.finished_reason == "stop"

        events = [json.loads(ln) for ln in (trace_dir / "trace.jsonl").read_text().splitlines()]
        tool_events = [ev for ev in events if ev["type"] == "tool_result"]
        assert len(tool_events) == 2
        assert events[-1]["tool_calls_made"] == 2

    def test_no_trace_dir_no_file(self, workspace):
        """不传 trace_dir → 不落盘."""
        llm = ScriptedLLM([resp("完成。")])
        loop = make_loop(workspace, llm)
        loop.run("x")
        assert not list(workspace.rglob("trace.jsonl"))

    def test_trace_iteration_numbers_monotonic(self, workspace, tmp_path):
        """每个 iter_start/llm_response 的 iteration 单调递增."""
        trace_dir = tmp_path / "trace"
        llm = ScriptedLLM([
            resp("取数。", [tc("get_market_data", codes=["600519.SH"],
                              start_date="2023-01-01", end_date="2023-12-31")]),
            resp("完成。"),
        ])
        loop = make_loop(workspace, llm, trace_dir=trace_dir)
        loop.run("x")
        events = [json.loads(ln) for ln in (trace_dir / "trace.jsonl").read_text().splitlines()]
        iters = [ev["iteration"] for ev in events
                 if ev["type"] in ("iter_start", "llm_response")]
        assert iters == sorted(iters)
        assert iters[-1] == len(iters) // 2


# ── 网络冒烟 (无网 skip) ──────────────────────────────────────────────


def _net_available(host: str = "push2his.eastmoney.com") -> bool:
    import socket
    try:
        socket.create_connection((host, 443), timeout=2).close()
        return True
    except OSError:
        return False


class TestNetworkSmoke:
    def test_get_market_data_real_network(self, tmp_path):
        """真实网络取数单工具冒烟 (CI 无网自动 skip)."""
        if not _net_available():
            pytest.skip("no network access")
        ws = tmp_path / "ws"
        for d in ("strategies", "data", "logs"):
            (ws / d).mkdir(parents=True)
        loop = make_loop(ws, ScriptedLLM([
            resp("取数。", [tc("get_market_data", codes=["600519.SH"],
                              start_date="2023-01-01", end_date="2023-03-31")]),
            resp("完成。"),
        ]), max_iterations=4)
        r = loop.run("网络取数")
        assert r.finished_reason == "stop"
        tool_evidences = [m for m in r.messages if m.get("role") == "tool"]
        data = parse(tool_evidences[0]["content"])
        assert data["status"] == "ok"
        assert data["summary"]["600519.SH"]["rows"] > 0
