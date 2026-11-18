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

    from agentnodes.foundation.config import settings as S

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


def _migrate_to_yaml(*, dry_run: bool = False, force: bool = False,
                     src: str | None = None, dst: str | None = None) -> int:
    """把 llm.json 转成 config.yaml（批 D）。

    **不删除、不改写源文件**——只新增一个 config.yaml。用户可随时删掉它
    回到 llm.json（那样会走回退路径，行为不变）。

    `dry_run` 时只打印将要发生的转换，不落盘。
    """
    from pathlib import Path

    import yaml

    from agentnodes.foundation.llm.quantnodes_bridge import (
        CONFIG_PATH,
        load_quantnodes_llm_config,
    )

    src_path = Path(src).expanduser() if src else CONFIG_PATH
    dst_path = Path(dst).expanduser() if dst else src_path.parent / "config.yaml"

    if not src_path.exists():
        print(f"源文件不存在：{src_path}", file=sys.stderr)
        print("（提示：先跑 `agentnodes init` 或 `agentnodes llm --use <provider>`）",
              file=sys.stderr)
        return 1

    if dst_path.exists() and not force:
        print(f"目标已存在：{dst_path}", file=sys.stderr)
        print("（用 --force 覆盖，或先手动备份）", file=sys.stderr)
        return 1

    section = load_quantnodes_llm_config(src_path, resolve_api_key=False)
    if not section:
        print(f"{src_path} 里没有可迁移的 llm 段", file=sys.stderr)
        return 1

    payload = {"version": 2, "llm": section}

    if dry_run:
        print(f"# dry-run：将把 {src_path} 写成 {dst_path}")
        print(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
        return 0

    # 原子写（同目录 temp + replace），0600——里面可能有 api_key 字段
    # （虽然我们保留 env: 引用不动，但纵深防御）。
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst_path.with_name(dst_path.name + ".partial")
    tmp.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    try:
        tmp.chmod(0o600)
    except OSError:
        pass  # Windows 上 chmod 语义有限，失败不阻塞
    tmp.replace(dst_path)

    print(f"已生成 {dst_path}")
    print(f"源文件未改动：{src_path}")
    print("（config.yaml 优先于 llm.json；删掉它即可回到 llm.json）")
    return 0


@cli_command(
    "config",
    help="配置诊断与迁移（audit-legacy / audit-legacy-engine / migrate）",
    description=(
        "配置诊断工具组。\n"
        "  audit-legacy         只读：扫出仍在用的旧名环境变量\n"
        "  audit-legacy-engine  只读：扫出仍依赖 legacy loop 引擎的地方\n"
        "  migrate              把 llm.json 转成 config.yaml（不删源文件）"
    ),
    add=lambda p: (
        p.add_argument(
            "action", nargs="?", default="audit-legacy",
            choices=["audit-legacy", "audit-legacy-engine", "migrate"],
            help="要执行的动作（默认 audit-legacy）",
        ),
        p.add_argument(
            "--json", action="store_true",
            help="输出 JSON（机器可读，给 CI/脚本用；仅 audit-*）",
        ),
        p.add_argument(
            "--dry-run", action="store_true",
            help="只打印将写入的内容，不落盘（仅 migrate）",
        ),
        p.add_argument(
            "--force", action="store_true",
            help="目标已存在时覆盖（仅 migrate）",
        ),
        p.add_argument(
            "--src", default=None,
            help="源 llm.json 路径（默认 ~/.agentnodes/llm.json）",
        ),
        p.add_argument(
            "--dst", default=None,
            help="目标 config.yaml 路径（默认与源同目录）",
        ),
    ),
)
def cmd_config(args: argparse.Namespace) -> int:
    """配置诊断 / 迁移入口。"""
    if args.action == "migrate":
        try:
            return _migrate_to_yaml(
                dry_run=args.dry_run,
                force=args.force,
                src=args.src,
                dst=args.dst,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"迁移失败：{exc}", file=sys.stderr)
            return 2

    if args.action == "audit-legacy-engine":
        try:
            from agentnodes.kernel.agent.telemetry.legacy_audit import (
                audit_legacy_engine,
                audit_legacy_engine_text,
            )

            if args.json:
                result = audit_legacy_engine()
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
                return 1 if result.blocking else (2 if result.error else 0)
            text, code = audit_legacy_engine_text()
            print(text)
            return code
        except Exception as exc:  # noqa: BLE001 —— 审计失败要能被 CI 看见
            print(f"审计失败: {exc}", file=sys.stderr)
            return 2

    if args.action != "audit-legacy":
        print(f"未知动作: {args.action}", file=sys.stderr)
        return 2
    try:
        if args.json:
            from agentnodes.foundation.config import settings as S

            summary = S.legacy_usage_summary()
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1 if summary["total"] else 0
        text, code = _audit_legacy_text()
        print(text)
        return code
    except Exception as exc:  # noqa: BLE001 —— 审计失败要能被 CI 看见
        print(f"审计失败: {exc}", file=sys.stderr)
        return 2
