#!/usr/bin/env python3
"""E2E: 真实 AgentLoop → 真实工具 → 真实落库全链路。

LLM 用脚本化决策序列（MockLLM 惯例），工具真实执行：
get_market_data(真实网络取数→DuckDB) → compute_factor → run_backtest → Goal 生命周期。

产物验证：
- workspace/data.duckdb: price_data / factor 数据
- runs/rotation_demo/ 回测产物
- trace.jsonl（AgentLoop trace_dir 接线验证 —— P6 待接线点）
- goals.db（默认路径）
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO = Path("/home/ll/Public/strategy-research")
sys.path.insert(0, str(REPO / "src"))

from agentnodes.core.agent.builtin_tools import build_default_registry
from agentnodes.core.agent.loop import AgentLoop
from agentnodes.core.llm import LLMConfig, LLMResponse, ToolCall

WS = Path("/tmp/opencode/e2e_ws")
SESSION = "e2e-20260805"
CODES = ["600519.SH", "000858.SZ", "000001.SZ"]


def tc(name: str, **args) -> ToolCall:
    return ToolCall(
        id=f"call_{name}", name=name,
        arguments=dict(args),
    )


def resp(content: str, calls: list[ToolCall] | None = None) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=calls or [],
        finish_reason="tool_calls" if calls else "stop",
    )


class ScriptedLLM:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[int] = []

    def chat(self, messages, **kwargs):
        self.calls.append(len(messages))
        return self.responses.pop(0)


def setup_workspace() -> None:
    if WS.exists():
        shutil.rmtree(WS)
    for d in ("strategies", "templates", "memory", "logs", "data", "tools/combo"):
        (WS / d).mkdir(parents=True, exist_ok=True)
    strat = WS / "strategies" / "rotation_demo"
    strat.mkdir(parents=True)
    cfg = (REPO / "templates" / "config.yaml").read_text()
    cfg = cfg.replace("{strategy_name}", "rotation_demo").replace("{strategy_type}", "rotation")
    (strat / "config.yaml").write_text(cfg)
    shutil.copy(REPO / "templates" / "strategy.py", strat / "strategy.py")
    shutil.copy(REPO / "templates" / "prepare.py", strat / "prepare.py")
    print(f"[ws] workspace ready: {WS}")


def verify() -> None:
    print("\n" + "=" * 60)
    print("产物验证")
    print("=" * 60)
    db = WS / "data.duckdb"
    if db.exists():
        import duckdb
        con = duckdb.connect(str(db), read_only=True)
        for t, note in (("price_data", ""), ("ohlcv", "视图(同 price_data)")):
            n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f"[db] {t}: {n} 行 {note}")
        n = con.execute("SELECT count(*) FROM factor_data").fetchone()[0]
        print(f"[db] factor_data: {n} 行 (compute_factor 只读采样, 不落库; 符合设计)")
        n = con.execute("SELECT count(*) FROM backtest_results").fetchone()[0]
        print(f"[db] backtest_results: {n} 行")
        con.close()
    else:
        print("[db] data.duckdb 缺失！")

    runs = sorted(
        (WS / "strategies" / "rotation_demo" / "runs").glob("run_*")
    ) if (WS / "strategies" / "rotation_demo" / "runs").exists() else []
    print(f"[runs] 产物: {len(runs)} 个")
    if runs:
        for f in sorted(Path(runs[0]).iterdir()):
            print(f"       {runs[0].name}/{f.name} ({f.stat().st_size}B)")

    trace = WS / "logs" / "trace" / "trace.jsonl"
    if trace.exists():
        lines = trace.read_text().strip().splitlines()
        kinds = {}
        for ln in lines:
            kinds[json.loads(ln).get("type")] = kinds.get(json.loads(ln).get("type"), 0) + 1
        print(f"[trace] {trace.name}: {len(lines)} 行, 事件类型 {kinds}")
    else:
        print(f"[trace] 缺失: {trace}")

    import sqlite3
    goal_db = Path(
        "~/.quantnodes-research/goals.db"
    ).expanduser()
    if goal_db.exists():
        con = sqlite3.connect(str(goal_db))
        rows = con.execute(
            "SELECT session_id, status, objective FROM goals "
            "WHERE session_id=? ORDER BY created_at DESC LIMIT 1", (SESSION,)
        ).fetchall()
        for r in rows:
            print(f"[goals.db] session={r[0]} status={r[1]} objective={r[2][:40]}")
        ev = con.execute(
            "SELECT count(*) FROM goal_evidence WHERE session_id=?", (SESSION,)
        ).fetchone()[0]
        print(f"[goals.db] 本会话 evidence: {ev} 条")
        con.close()
    else:
        print(f"[goals.db] 缺失: {goal_db}")


def main() -> int:
    setup_workspace()

    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-e2e"),
        registry=build_default_registry(WS),
        workspace=WS,
        max_iterations=12,
        trace_dir=WS / "logs" / "trace",
        session_id=SESSION,
    )
    script = ScriptedLLM([
        resp("先取三只股票近三年行情。", [
            tc("get_market_data", codes=CODES, start_date="2022-01-01", end_date="2024-12-31"),
        ]),
        resp("数据已入库，计算动量因子。", [
            tc("compute_factor",
               factor_code="ts_mean(close, 20) / ts_mean(close, 60) - 1",
               factor_name="momentum_20_60", asset="600519.SH"),
        ]),
        resp("因子正常，运行回测。", [
            tc("run_backtest", strategy_name="rotation_demo", description="E2E 全链路验证"),
        ]),
        resp("建立研究目标。", [
            tc("create_goal",
               objective="验证 A 股动量因子有效性（600519/000858/000001）",
               criteria=["回测完成", "数据入库"]),
        ]),
        resp("补充证据：回测已运行。", [
            tc("add_evidence", text="rotation_demo 回测已通过 run_backtest 完成，写入 runs/ 与 DuckDB。",
               source_type="backtest"),
        ]),
        resp("证据充足，完成目标。", [
            tc("complete_goal", recap="E2E 全链路验证通过：取数→因子→回测→目标闭环。"),
        ]),
        resp("全部完成。总结：行情已入库，动量因子可算，回测已产出，研究目标闭环。"),
    ])
    loop.client.chat = script.chat

    r = loop.run("请验证完整研究流程：行情取数 → 因子计算 → 策略回测 → 研究目标管理。")
    print(f"\n[loop] iterations={r.iterations} tool_calls={r.tool_calls_made} "
          f"finished={r.finished_reason}")
    print(f"[loop] answer={r.answer[:200]}")

    verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
