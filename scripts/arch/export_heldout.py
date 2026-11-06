# -*- coding: utf-8 -*-
"""export_heldout — 把 held-out 集从仓库内源导出到**进程外**（agent 不可读）。

**为什么需要它**：held-out 集的全部价值在于「被测方看不到」。它必须住在
agent 的 ``read`` 工具够不到的地方：``read`` 以工作区为根做路径白名单，
仓库外的 ``~/.agentnodes/eval_heldout/`` 不在任何 ``read_roots`` 里。
仓库内只保留 held-in 明文。

**导出流程（三步，任一失败即非零退出）**：

1. **定位源**：``--source`` → 外部并集母本 → 仓库内 ``golden_tasks.jsonl``
2. **重新分桶**：用 ``sha256(task_id) % 100 < 70`` 判 held-in；余数是
   held-out。**不读** manifest、不信任仓库里的既有拆分——拆分规则是
   唯一事实源，manifest 只是它的产物。
3. **落盘 + 防止副本回流入库**：写到 ``AN_GOLDEN_HELDOUT_PATH`` 或
   ``ensure_data_dir()/eval_heldout/golden_tasks_heldout.jsonl``；然后
   扫仓库内是否出现 held-out 明文副本，发现即报错（**不写**，避免
   "导出了但同时也泄漏了"）。

**为什么导出要重算分桶而不是复制 held-in 之外的行**：如果仓库里的
``golden_tasks.jsonl`` 被手工改过（删了几条难例、加了几条水题），
"取补集"会把这些改动原样带进 held-out。重算分桶只依赖 id → 被改过的
任务仍按 id 落回它该在的一侧，不会因为"仓库里没有"而消失。源集变少
是另一件事，由 ``--expect-count`` 断言抓。

**泄漏审计**（``--audit``）：单独跑，检查
- held-out 文件确实**在仓库外**（``ROOT`` 不是它的父目录）
- 仓库内没有任何 ``*heldout*.jsonl``
- held-out 与 held-in 的 id 无交集
- 可选：``--expect-count N`` 断言条数

用法：

    python scripts/arch/export_heldout.py                 # 导出 + 审计
    python scripts/arch/export_heldout.py --dry-run       # 只报告，不写
    python scripts/arch/export_heldout.py --audit         # 只审计现网文件
    python scripts/arch/export_heldout.py --expect-count 23 --audit

退出码：0 通过 · 1 审计/断言失败 · 2 用法或输入错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent          # scripts/arch
ROOT = HERE.parents[1]                          # 仓库根
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from agentnodes.evals import golden_sets as gs  # noqa: E402


class AuditFailure(Exception):
    """审计失败（退出码 1）。"""


# ── 审计 ─────────────────────────────────────────────────────────────


def audit(
    *,
    heldout_file: Path | None = None,
    expect_count: int | None = None,
    min_tasks: int = gs.MIN_HELDOUT_TASKS,
) -> dict:
    """检查 held-out 的持有位置与隔离性。返回报告 dict，失败抛 ``AuditFailure``。"""
    target = heldout_file or gs.heldout_path()
    report: dict = {
        "path": str(target),
        "inside_repo": False,
        "count": 0,
        "sha256": "",
        "repo_copies": [],
        "overlap_with_heldin": [],
    }

    # 1. 位置：必须在仓库外。判据用 resolve() 后的相对路径——符号链接也挡。
    try:
        target.resolve().relative_to(ROOT.resolve())
        report["inside_repo"] = True
    except ValueError:
        report["inside_repo"] = False
    if report["inside_repo"]:
        raise AuditFailure(
            f"held-out lives INSIDE the repo ({target}) — the agent's read "
            "tool could reach it; move it to ensure_data_dir()/eval_heldout"
        )

    # 2. 存在性 + 可解析
    if not target.exists():
        raise AuditFailure(
            f"held-out not found at {target} — run without --audit to export"
        )
    try:
        lines = gs.read_lines(target)
        rows = [json.loads(x) for x in lines]
    except Exception as exc:  # noqa: BLE001
        raise AuditFailure(f"held-out unreadable ({target}): {exc}") from exc
    if not rows:
        raise AuditFailure(f"held-out is empty: {target}")
    if len(rows) < min_tasks:
        raise AuditFailure(
            f"held-out has {len(rows)} tasks < min {min_tasks}: too few for a "
            "degradation gate"
        )
    if expect_count is not None and len(rows) != expect_count:
        raise AuditFailure(
            f"held-out has {len(rows)} tasks, expected {expect_count}"
        )

    report["count"] = len(rows)
    report["sha256"] = gs.rows_digest(rows)

    # 3. 仓库内不得有 held-out 明文副本（名字含 heldout 的 jsonl）
    repo_copies = sorted((ROOT / "src").rglob("*heldout*.jsonl"))
    report["repo_copies"] = [str(p.relative_to(ROOT)) for p in repo_copies]
    if repo_copies:
        raise AuditFailure(
            "held-out plaintext found inside the repo: "
            + ", ".join(report["repo_copies"])
        )

    # 4. 与 held-in 无交集（防两侧被同一改动覆盖）
    heldin_file = gs.heldin_path()
    if heldin_file.exists():
        heldin_ids = {str(r.get("id")) for r in gs.read_rows(heldin_file)}
        overlap = sorted(
            heldin_ids & {str(r.get("id")) for r in rows}
        )
        report["overlap_with_heldin"] = overlap
        if overlap:
            raise AuditFailure(f"held-out overlaps held-in: {overlap}")

    return report


# ── 导出 ─────────────────────────────────────────────────────────────


def export(
    source: Path,
    *,
    dry_run: bool = False,
    expect_count: int | None = None,
) -> dict:
    """从仓库内源重算分桶并写 held-out 到外部路径。返回导出报告。"""
    lines = gs.read_lines(source)
    rows = [json.loads(x) for x in lines]
    if not rows:
        raise AuditFailure(f"source is empty: {source}")

    ids = [str(r.get("id", "")) for r in rows]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise AuditFailure(f"source has duplicate ids: {dupes}")

    heldout_rows = [r for r in rows if not gs.is_heldin(str(r["id"]))]
    if len(heldout_rows) < gs.MIN_HELDOUT_TASKS:
        raise AuditFailure(
            f"source yields only {len(heldout_rows)} held-out tasks "
            f"(min {gs.MIN_HELDOUT_TASKS}) — check the split rule / source"
        )
    if expect_count is not None and len(heldout_rows) != expect_count:
        raise AuditFailure(
            f"source yields {len(heldout_rows)} held-out tasks, "
            f"expected {expect_count}"
        )

    target = gs.heldout_path()
    report = {
        "source": str(source),
        "target": str(target),
        "count": len(heldout_rows),
        "sha256": gs.rows_digest(heldout_rows),
        "dry_run": dry_run,
        "written": False,
    }

    # 只保留源行原文（逐字节），与 split_golden_sets 口径一致
    heldout_lines = [
        line for line in lines
        if not gs.is_heldin(str(json.loads(line)["id"]))
    ]

    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(gs.render_lines(heldout_lines), encoding="utf-8")
        report["written"] = True
    return report


# ── main ─────────────────────────────────────────────────────────────


def _print_report(report: dict) -> None:
    print(f"held-out path : {report['path']}")
    print(f"  inside repo : {report['inside_repo']}")
    print(f"  tasks       : {report['count']}")
    print(f"  rows sha256 : {report['sha256']}")
    if report.get("repo_copies"):
        print(f"  repo copies : {report['repo_copies']}")
    if report.get("overlap_with_heldin"):
        print(f"  overlap     : {report['overlap_with_heldin']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Export held-out golden tasks outside the repo (agent-unreadable)",
    )
    ap.add_argument("--source", default=None,
                    help="source jsonl (default: external union master, "
                         "else in-repo golden_tasks.jsonl)")
    ap.add_argument("--out", default=None,
                    help="override the held-out destination path")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; do not write")
    ap.add_argument("--audit", action="store_true",
                    help="audit the existing held-out file only (no export)")
    ap.add_argument("--expect-count", type=int, default=None,
                    help="assert the held-out task count equals this")
    args = ap.parse_args(argv)

    if args.out:
        # 显式 --out 时临时改写环境变量：gs.heldout_path() 是唯一解析点，
        # 不要在脚本里另建一套路径逻辑（两套迟早不一致）。
        import os

        os.environ[gs.HELDOUT_PATH_ENV] = args.out

    try:
        if args.audit:
            report = audit(expect_count=args.expect_count)
            _print_report(report)
            print("audit: held-out is outside the repo, no in-repo copy, "
                  "no overlap with held-in")
            return 0

        source = gs.find_source_path(args.source)
        if source is None:
            print(
                "no source found: pass --source, or create the external union "
                f"master at {gs.union_master_path()}",
                file=sys.stderr,
            )
            return 2

        export_report = export(
            source, dry_run=args.dry_run, expect_count=args.expect_count,
        )
        print(
            f"exported {export_report['count']} held-out tasks "
            f"from {export_report['source']}"
            + (" (dry-run, nothing written)" if export_report["dry_run"] else "")
        )
        print(f"  -> {export_report['target']}")
        print(f"  rows sha256 {export_report['sha256']}")

        if export_report["written"]:
            # 导出后立刻审计：刚写出去就得证明它在仓库外、且仓库里没有副本。
            report = audit(expect_count=args.expect_count)
            _print_report(report)
            print("audit: held-out is outside the repo, no in-repo copy, "
                  "no overlap with held-in")
        return 0
    except AuditFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 —— 输入问题
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
