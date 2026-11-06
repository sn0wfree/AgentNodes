"""``rsi`` CLI command — RSI 闭环的手动驱动入口（W4 批次 2）。

Usage::

    agentnodes rsi run --seed 0        # 跑一轮（过 halt / 预算 / 证据门）
    agentnodes rsi run --seed 0 --force  # 跳过预算闸（仍过 halt 与证据门）
    agentnodes rsi status              # 打印预算/证据/halt 状态

**为什么是薄命令**：真正的驱动逻辑在 ``interfaces/rsi_cli.py``（L1 组装
根）。这里只做参数解析与打印——命令模块若承担逻辑，就没法被非 CLI 的
宿主（API / 定时任务）复用，而 RSI 的驱动恰恰需要多种宿主。

**退出码约定**：0 = 跑完或正常跳过（跳过是预期行为，不是错误）；
1 = 门挡下的异常态（halt / 装配失败 / 崩溃），便于运维脚本判定。
"""

from __future__ import annotations

import argparse
import json

from agentnodes.interfaces.cli.commands.registry import cli_command


def _print_outcome(outcome) -> int:
    data = outcome.to_dict()
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    if outcome.reason == "halted":
        print("\nHALT 生效中：先清除 kill switch（`/resume` 或删除 halt.json）。")
        return 1
    if outcome.reason in ("unknown", "cycle_crashed", "gateway_unavailable",
                          "wiring_skipped"):
        return 1
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from agentnodes.interfaces.rsi_cli import run_once

    outcome = run_once(
        seed=int(args.seed),
        session_id=getattr(args, "session_id", None) or None,
        force=bool(getattr(args, "force", False)),
    )
    return _print_outcome(outcome)


def _cmd_status(args: argparse.Namespace) -> int:
    from agentnodes.interfaces.rsi_cli import (
        daily_limit,
        load_state,
        per_session_limit,
        state_path,
    )

    payload: dict = {
        "state_path": str(state_path()),
        "state": load_state(),
        "limits": {
            "per_session": per_session_limit(),
            "daily": daily_limit(),
        },
    }
    try:
        from agentnodes.interfaces.cli.halt import halt_status

        payload["halt"] = halt_status()
    except Exception:  # noqa: BLE001 —— 状态打印不该因 halt 不可用而失败
        payload["halt"] = {"halted": None}
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def _dispatch_rsi(args: argparse.Namespace) -> int:
    sub = getattr(args, "rsi_command", None)
    if sub == "run":
        return _cmd_run(args)
    if sub == "status":
        return _cmd_status(args)
    print("usage: agentnodes rsi {run,status} [options]")
    return 0


@cli_command(
    "rsi",
    help="RSI 闭环驱动（run / status）",
    description=(
        "手动驱动一次 RSI Tier 1 cycle，或查看预算/证据/halt 状态。\n"
        "run 会先过三道门：halt → 预算（每 session 1 次 / 每日 3 次）"
        "→ 证据增量。跳过是正常路径，不是错误。"
    ),
    add=lambda p: (
        p.add_argument(
            "rsi_command", nargs="?", choices=("run", "status"),
            default="status", help="子命令（默认 status）",
        ),
        p.add_argument(
            "--seed", type=int, default=0,
            help="holdout / golden eval 抽样种子（附录 C：显式给，禁止隐式随机）",
        ),
        p.add_argument(
            "--session-id", default=None,
            help="预算口径的会话 id（缺省每次调用即新会话）",
        ),
        p.add_argument(
            "--force", action="store_true",
            help="跳过预算闸（不跳 halt 与证据门）",
        ),
    ),
)
def cmd_rsi(args: argparse.Namespace) -> int:
    """RSI: run a cycle or print status."""
    return _dispatch_rsi(args)


__all__ = ["cmd_rsi"]
