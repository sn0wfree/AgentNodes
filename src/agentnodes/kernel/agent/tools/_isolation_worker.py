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

#: 当前任务的 call_id（B2.4 4b）——worker 单线程串行协议，一次只处理一个
#: 任务；_emit 自动把它注入每条回传行，父侧按 id 精确关联/丢弃迟到行。
_CURRENT_CALL_ID: str = ""

#: 任务行信封版本——**必须与 isolation.py 的 TASK_V 同值**（B2.4 4b 契约）。
#: worker 作为独立子进程，不 import isolation.py（避免拉整个 seam 依赖），
#: 故在此镜像常量；契约测试钉住两者一致。
TASK_V = 1


def _configure_logging() -> None:
    """日志走 stderr——stdout 只能是协议帧（父进程按 JSON Lines 解析）。

    装配一律走 ``foundation.observability.configure_logging``（日志装配
    纪律：模块内不得自行装 handler）。
    """
    from agentnodes.foundation.observability.logging_setup import configure_logging

    configure_logging("script", stream=sys.stderr)


def _emit(obj: dict) -> None:
    out = _PROTOCOL_OUT or sys.stdout
    # B2.4 4b：所有回传行回显 call_id（父侧按 id 过滤迟到结果）
    frame = dict(obj)
    frame["call_id"] = _CURRENT_CALL_ID
    out.write(json.dumps(frame, ensure_ascii=False) + "\n")
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


def _decode_ctx(ctx: dict) -> tuple[Any | None, str]:
    """wire dict → ToolContext（B2.3 3b：唯一解码入口）。

    返回 ``(tool_context, error)``——解码失败**不让 worker 崩溃/退出**：
    错误由调用方回结构化结果行，worker 保持健康可复用（不毒化，
    B2.3 §2.3 / 3c D2 边界：毒化只属 deadline/EOF）。
    """
    from agentnodes.kernel.agent.tools._isolation_context import (
        IsolationContextError,
        from_wire,
    )

    try:
        return from_wire(ctx), ""
    except IsolationContextError as exc:
        return None, f"ctx schema reject: {exc}"
    except Exception as exc:  # noqa: BLE001 —— 兜底也走结构化错误行
        return None, f"ctx decode failed: {type(exc).__name__}: {exc}"


def _run_tool(name: str, args: dict, tool_ctx: Any) -> dict:
    """在 worker 内解析工具并执行（注册表按名查找）。"""
    from agentnodes.kernel.agent.builtin_tools import build_default_registry

    registry = build_default_registry()
    tool = registry.get(name)
    if tool is None:
        return {"status": "error", "error": f"unknown tool: {name}"}
    try:
        # v2 入口：ctx 由框架注入 kwargs["ctx"]（见 BaseTool.invoke/_dispatch）
        output = tool.invoke({**dict(args), "ctx": tool_ctx})
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
        # B2.4 4b：信封版本校验（与 ctx_v 正交）——缺失/不兼容显式报错。
        global _CURRENT_CALL_ID
        _CURRENT_CALL_ID = str(task.get("call_id", ""))
        if task.get("v") != TASK_V:
            _emit({"result": {
                "status": "error",
                "error": f"task envelope v={task.get('v')!r} incompatible "
                         f"(expected {TASK_V})",
            }})
            continue
        if not _CURRENT_CALL_ID:
            _emit({"result": {
                "status": "error", "error": "missing task call_id",
            }})
            continue
        tool = task.get("tool")
        args = task.get("args", {})
        ctx = task.get("ctx", {})
        tool_ctx, ctx_error = _decode_ctx(ctx)
        if ctx_error:
            # B2.3 3b：解码失败 → 结构化错误行；worker 保持健康（不毒化）。
            _emit({"result": {"status": "error", "error": ctx_error}})
            continue
        if tool == "probe_context":
            _emit({"ev": "progress", "ctx_workspace": (
                str(tool_ctx.workspace) if tool_ctx.workspace else None
            ), "ctx_session": tool_ctx.session_id})
            _emit({"result": {"status": "ok", "echo": args.get("payload")}})
            continue
        if tool == "probe_ctx_echo":
            # B2.3 3c 契约探针（仅测试用）：回显**收到的 wire ctx 原始键集合**。
            # 注意用原始 `ctx`（解码前）——白名单外键已被 from_wire 拒，探针
            # 需在解码成功路径上断言「实际跨界的就是白名单那几把钥匙」，
            # 与 to_wire 的构造级 impossibility 形成两端夹击。
            _emit({"result": {
                "status": "ok",
                "wire_keys": sorted(ctx.keys()) if isinstance(ctx, dict) else [],
                "ctx_v": ctx.get("ctx_v") if isinstance(ctx, dict) else None,
            }})
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
        _emit({"result": _run_tool(tool, args, tool_ctx)})
    return 0


def main() -> int:
    if "--worker" in sys.argv:
        return _worker_loop()
    print("usage: python -m agentnodes.kernel.agent.tools._isolation_worker --worker", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
