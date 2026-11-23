"""W2 — merged CLI group: serve.

原文件同时承载 ``backtest`` 与 ``check`` 两个合并命令组（把
``run`` / ``evaluate`` / ``reproduce`` / ``validate`` / ``accept`` 重新包装成
``backtest run|rerun|replay`` 与 ``check factor|run|accept``）。

**2026-11-05 做减法批次 4**：``backtest`` / ``check`` 两组的全部目标
（``apps.backtest`` / ``apps.factor_validate`` / ``apps.strategy_acceptance``）
已随回测与验证域整体移除，归档 ``testcase/golden/apps/``。因此本文件只剩
``serve`` —— 它把 Web UI 与 API 两个服务合成一条命令，与业务无关。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _validate_frontend_dev(args: argparse.Namespace) -> bool:
    for flag, enabled in (("--api-only", args.api_only), ("--reload", args.reload)):
        if enabled:
            print(f"--frontend-dev 不能与 {flag} 同时使用", file=sys.stderr)
            return False
    if args.host != "127.0.0.1":
        print("--frontend-dev 仅支持后端绑定 127.0.0.1", file=sys.stderr)
        return False
    if not 1 <= args.port <= 65535 or not 1 <= args.frontend_port <= 65535:
        print("端口必须在 1 至 65535 之间", file=sys.stderr)
        return False
    if args.port == args.frontend_port:
        print("前端和后端不能使用相同端口", file=sys.stderr)
        return False
    return True


def _stop_dev_processes(processes: list[tuple[str, subprocess.Popen]]) -> bool:
    success = True
    for name, process in reversed(processes):
        if process.poll() is not None:
            continue
        try:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        except ProcessLookupError:
            continue
        except OSError as exc:
            print(f"停止{name}进程失败：{exc}", file=sys.stderr)
            success = False
    return success


def _serve_frontend_dev(args: argparse.Namespace) -> int:
    if not _validate_frontend_dev(args):
        return 2

    frontend = Path(__file__).resolve().parents[5] / "webui" / "frontend"
    vite = frontend / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite.is_file():
        print(f"未找到 {vite}；请在源码仓库的 webui/frontend 安装依赖", file=sys.stderr)
        return 1
    node = shutil.which("node")
    if node is None:
        print("未找到 Node.js；请安装项目要求的 Node.js 版本", file=sys.stderr)
        return 1

    backend_command = [
        sys.executable, "-m", "agentnodes.interfaces.cli", "serve",
        "--host", args.host, "--port", str(args.port),
        "--an-workspace", args.workspace,
    ]
    for name, flag in (
        ("goal_db", "--an-goal-db"),
        ("hypotheses_path", "--an-hypotheses-path"),
        ("static_dir", "--an-static-dir"),
    ):
        value = getattr(args, name, None)
        if value is not None:
            backend_command.extend([flag, str(value)])

    env = os.environ.copy()
    env["API_TARGET"] = f"http://127.0.0.1:{args.port}"
    frontend_command = [
        node, str(vite), "--host", "127.0.0.1",
        "--port", str(args.frontend_port), "--strictPort",
    ]
    processes: list[tuple[str, subprocess.Popen]] = []
    result = 0
    try:
        processes.append(("后端", subprocess.Popen(backend_command)))
        processes.append(("前端", subprocess.Popen(frontend_command, cwd=frontend, env=env)))
        print(f"正在启动前端开发服务：http://127.0.0.1:{args.frontend_port}/chat")
        while result == 0:
            for name, process in processes:
                status = process.poll()
                if status is not None:
                    print(f"{name}进程已退出（退出码 {status}）", file=sys.stderr)
                    result = status if status > 0 else 1
                    break
            if result == 0:
                time.sleep(0.2)
    except KeyboardInterrupt:
        print("正在停止本次开发服务")
    except OSError as exc:
        print(f"启动开发服务失败：{exc}", file=sys.stderr)
        result = 1
    finally:
        if not _stop_dev_processes(processes):
            result = 1
    return result


def dispatch_serve(args: argparse.Namespace) -> int:
    """serve: webui (default) or api-only."""
    from agentnodes.interfaces.cli.commands.server import cmd_api_serve, cmd_webui_serve

    if getattr(args, "frontend_dev", False):
        return _serve_frontend_dev(args)
    if getattr(args, "api_only", False):
        return cmd_api_serve(args)
    return cmd_webui_serve(args)


__all__ = ["dispatch_serve"]
