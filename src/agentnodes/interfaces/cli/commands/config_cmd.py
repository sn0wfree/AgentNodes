"""``config`` 命令组 —— 配置诊断（批 E-1）。

**目前只有一个子命令**：``audit-legacy`` —— 扫出当前环境里**仍在用的
旧名环境变量**，并给出行动指引。

设计见 ``docs/design/legacy-env-audit-command-20261110.md``。

**只读**：不改任何环境变量、不动 llm.json。
退出码 0=无旧名在用 / 1=有旧名在用（该改名）/ 2=审计失败。

**批 E-2 之后的语义变化**：兼容层已经删掉了，所以旧名分两类——
- **已失效**：值被**静默忽略**（读规范名时回落默认）→ 必须改名；
- **仍生效**：:data:`settings.LEGACY_LIVE_ENVS` 里的那几个（指向"用户数据
  在哪"）→ 仍能用，但已废弃，建议改名。
"""
from __future__ import annotations

import argparse
import json
import sys

from agentnodes.interfaces.cli.commands.registry import cli_command

#: 风险档 → 显示用标签。
_RISK_LABEL = {
    "high": "高危",
    "medium": "中危",
    "low": "低危",
}


def _mask_value(value: str) -> str:
    """脱敏（密钥类）。显示首尾少量字符，中间打码。"""
    if not value:
        return value
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-2:]} (len={len(value)})"


def _audit_legacy_text(environ=None) -> tuple[str, int]:
    """人读版审计报告。返回 (报告文本, 退出码)。"""
    import os

    from agentnodes.foundation import settings as S

    summary = S.legacy_usage_summary(environ)
    items = summary["items"]
    counts = summary["counts"]

    lines: list[str] = []
    lines.append("=== 1. 本进程环境中的旧名 ===")
    if not items:
        lines.append("  （无）")
    else:
        for it in items:
            risk = it["risk"]
            value = it["value"]
            setting = S.ENV_TABLE.get(it["canonical"])
            if (setting is not None and setting.secret) or it["canonical"].endswith(
                ("PASSWORD", "SECRET", "TOKEN", "KEY")
            ):
                value = _mask_value(value)
            lines.append(f"  {it['old_name']} -> {it['canonical']}")
            lines.append(f"    当前值: {value}")
            lines.append(
                f"    风险: {risk} —— {summary['notes'].get(risk, '')}"
            )
            if it["honored"]:
                lines.append(
                    "    状态: 仍生效（已废弃）—— 指向用户数据所在位置，"
                    "过渡期保留，但建议改名"
                )
            else:
                lines.append(
                    "    状态: **已失效** —— 兼容层已删除，这个值被忽略"
                    "（读取时回落默认），必须改名"
                )

    lines.append("")
    lines.append("=== 2. 本进程环境中的 canonical（已改名）===")
    env = environ if environ is not None else os.environ
    canon_set = {it["canonical"] for it in items}
    shown = 0
    for name in sorted(S.ENV_TABLE):
        if name in canon_set:
            continue
        if name in env:
            setting = S.ENV_TABLE.get(name)
            value = env[name]
            if setting is not None and setting.secret:
                value = _mask_value(value)
            lines.append(f"  {name} = {value}")
            shown += 1
    if not shown:
        lines.append("  （无）")

    lines.append("")
    lines.append("=== 3. 外部变量（不归本表管，不受影响）===")
    ext = sorted(S.EXTERNAL_ENV_VARS)
    lines.append("  " + ", ".join(ext))

    lines.append("")
    lines.append("=== 结论 ===")
    total = summary["total"]
    if total == 0:
        lines.append(
            "  未发现旧名环境变量 —— 当前环境与规范名完全一致。"
        )
        code = 0
    else:
        lines.append(
            f"  发现 {total} 个旧名仍在使用"
            f"（高危 {counts.get('high', 0)} / "
            f"中危 {counts.get('medium', 0)} / "
            f"低危 {counts.get('low', 0)}）"
        )
        ignored = summary.get("ignored", 0)
        honored = summary.get("honored", 0)
        if ignored:
            lines.append(
                f"  其中 {ignored} 个**已经失效**（值被忽略）—— 必须改名，"
                "否则相关配置会静默回落默认。"
            )
        if honored:
            lines.append(
                f"  其中 {honored} 个仍生效（已废弃）—— 过渡期保留，"
                "建议尽快改名。"
            )
        for it in items:
            lines.append(
                f"    建议: 把 {it['old_name']} 改为 {it['canonical']}"
            )
        code = 1
    return "\n".join(lines), code


@cli_command(
    "config",
    help="配置诊断（audit-legacy：扫出仍在用的旧名环境变量）",
    description=(
        "配置诊断工具组。audit-legacy 是**只读**的：扫出当前环境里仍在用"
        "的旧名环境变量（批 B' 改名时的兼容层已在批 E-2 删除），标出哪些"
        "已失效（值被忽略）、哪些仍生效（已废弃），并给出改名指引。"
        "退出码 0=无旧名在用，1=有旧名在用。"
    ),
    add=lambda p: (
        p.add_argument(
            "action", nargs="?", default="audit-legacy",
            choices=["audit-legacy"],
            help="要执行的动作（默认 audit-legacy）",
        ),
        p.add_argument(
            "--json", action="store_true",
            help="输出 JSON（机器可读，给 CI/脚本用）",
        ),
    ),
)
def cmd_config(args: argparse.Namespace) -> int:
    """配置诊断入口。"""
    if args.action != "audit-legacy":
        print(f"未知动作: {args.action}", file=sys.stderr)
        return 2
    try:
        if args.json:
            from agentnodes.foundation import settings as S

            summary = S.legacy_usage_summary()
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1 if summary["total"] else 0
        text, code = _audit_legacy_text()
        print(text)
        return code
    except Exception as exc:  # noqa: BLE001 —— 审计失败要能被 CI 看见
        print(f"审计失败: {exc}", file=sys.stderr)
        return 2
