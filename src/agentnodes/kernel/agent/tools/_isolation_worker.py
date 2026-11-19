# -*- coding: utf-8 -*-
"""隔离执行 worker 入口（4c B2.2 子批 3b）。

以独立进程运行，执行一个同步工具调用并回传结果行。协议（JSON Lines，
每行 flush）：

  父 → worker（任务行）:
      {"tool": "<name>", "args": {...}, "ctx": {...}}
  worker → 父（事件行）:
      {"ev": "progress", ...}            进度/上下文回显
      {"ev": "permission_request", "id": ..., "tool": ...}
  父 → worker（审批响应行）:
      {"op": "permission_response", "id": ..., "action": "allow"|"deny"}
  worker → 父（结果行）:
      {"result": {"status": "ok"|"denied"|"error", ...}}

工具执行：worker 内按名导入 ``agentnodes.kernel.agent.builtin_tools`` 的
注册表并调用 ``invoke``——父进程只传工具名与参数（不传对象），因此
ToolContext 的进程内对象（permission gateway / data_store）不在 worker 内
可用；审批由父进程代理（见 isolation.py 的 ApprovalProxy）。

**不接生产路径**：本文件只被 3b 的 IPC 层与 3c 的试点脚本使用。
"""
from __future__ import annotations

import json
import sys
import time

#: 协议通道（原始 stdout）；worker 启动后 sys.stdout 改指 stderr
_PROTOCOL_OUT = None


def _configure_logging() -> None:
    """日志走 stderr——stdout 只能是协议帧（父进程按 JSON Lines 解析）。

    装配一律走 ``foundation.observability.configure_logging``（日志装配
    纪律：模块内不得自行装 handler）。
    """
    from agentnodes.foundation.observability.logging_setup import configure_logging

    configure_logging("script", stream=sys.stderr)


def _emit(obj: dict) -> None:
    out = _PROTOCOL_OUT or sys.stdout
    out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    out.flush()


def _install_stdout_guard() -> None:
    """把 stdout 上的第三方噪音（依赖 import 时的 print/警告）移出协议通道。

    某些依赖在 import 时向 stdout 打印弃用警告（例：``fitz``），会污染
    协议帧。做法：worker 启动后把 ``sys.stdout`` 换成 stderr，协议帧走
    保存的原始 stdout。
    """
    global _PROTOCOL_OUT

    if _PROTOCOL_OUT is None:
        _PROTOCOL_OUT = sys.stdout
    else:
        return
    sys.stdout = sys.stderr


def _read_line() -> str | None:
    line = sys.stdin.readline()
    return line.strip() if line else None


def _build_ctx(ctx: dict) -> Any:
    """从可序列化的 ctx dict 构造 worker 内的 ToolContext。

    只承载**数据字段**（§9.7 E2 清单：workspace / session_id /
    strategy_dir / results_tsv / write_roots / read_roots / message_id /
    tool_call_id）。回调与进程内对象（emit_* / permission_* / data_store /
    sandbox）不在 worker 内可用——回调由父进程代理（3b 审批）、其余留 3c
    之后评估。
    """
    from agentnodes.kernel.agent.tools.core import ToolContext

    return ToolContext(
        workspace=ctx.get("workspace"),
        session_id=ctx.get("session_id"),
        strategy_dir=ctx.get("strategy_dir"),
        results_tsv=ctx.get("results_tsv"),
        write_roots=ctx.get("write_roots"),
        read_roots=ctx.get("read_roots"),
        message_id=ctx.get("message_id"),
        tool_call_id=ctx.get("tool_call_id"),
    )


def _run_tool(name: str, args: dict, ctx: dict) -> dict:
    """在 worker 内解析工具并执行（注册表按名查找）。"""
    from agentnodes.kernel.agent.builtin_tools import build_default_registry

    registry = build_default_registry()
    tool = registry.get(name)
    if tool is None:
        return {"status": "error", "error": f"unknown tool: {name}"}
    try:
        # v2 入口：ctx 由框架注入 kwargs["ctx"]（见 BaseTool.invoke/_dispatch）
        output = tool.invoke({**dict(args), "ctx": _build_ctx(ctx)})
    except Exception as exc:  # noqa: BLE001 —— worker 内错误回传父进程
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {"status": "ok", "output": output}


def _worker_loop() -> int:
    _configure_logging()
    _install_stdout_guard()
    while True:
        line = _read_line()
        if line is None:  # stdin EOF → 父进程关闭
            return 0
        if not line:
            continue
        task = json.loads(line)
        op = task.get("op")
        if op == "permission_response":
            # 父进程对审批请求的裁决（worker 内不自行判定）
            continue
        tool = task.get("tool")
        args = task.get("args", {})
        ctx = task.get("ctx", {})
        if tool == "probe_context":
            _emit({"ev": "progress", "ctx_workspace": ctx.get("workspace"),
                   "ctx_session": ctx.get("session_id")})
            _emit({"result": {"status": "ok", "echo": args.get("payload")}})
            continue
        if tool == "probe_permission":
            _emit({"ev": "permission_request", "id": args.get("id"),
                   "tool": args.get("tool")})
            resp_line = _read_line()
            if resp_line is None:
                return 0
            resp = json.loads(resp_line)
            if resp.get("action") == "allow":
                _emit({"result": {"status": "ok", "approved": True}})
            else:
                _emit({"result": {"status": "denied", "approved": False}})
            continue
        if tool == "probe_sleep":
            # 仅测试用探针（3c 试点 S2/S3/S4）：阻塞 N 秒后回结果——
            # 驱动 deadline 触发与迟到结果场景。
            time.sleep(float(args.get("seconds", 1.0)))
            _emit({"result": {"status": "ok", "slept": args.get("seconds")}})
            continue
        _emit({"result": _run_tool(tool, args, ctx)})
    return 0


def main() -> int:
    if "--worker" in sys.argv:
        return _worker_loop()
    print("usage: python -m agentnodes.kernel.agent.tools._isolation_worker --worker", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
