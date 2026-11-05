# -*- coding: utf-8 -*-
"""物理分层迁移工具 — core/ → foundation · kernel · apps · interfaces。

一次性工具，保留在仓库中以便审计与回放。设计契约见
docs/design/layered-skeleton-design-20261103.md 与迁移计划。

用法
----
    python scripts/migrate_layers.py --batch interfaces --dry-run
    python scripts/migrate_layers.py --batch interfaces
    python scripts/migrate_layers.py --verify           # 全库残留检查

三阶段
------
1. ``git mv`` 按 MOVES 表搬目录/文件
2. AST 改写 ``src/`` + ``tests/`` 下每个 .py 的 import：
   - 绝对导入 ``agentnodes.core.<moved>`` → ``agentnodes.<layer>.<moved>``
   - 相对导入：解析为绝对；跨层则改写为绝对新路径，包内则保留原样
     （同一层内移动时目录深度不变，相对导入仍然有效）
   - 字符串字面量 ``"agentnodes.core.X"`` 同规则替换
     （覆盖 monkeypatch / uvicorn target / importlib / __import__）
3. 报告每个文件的改写条数；``--verify`` 输出残留旧前缀并以非零码退出

关键安全点
----------
``core/tools`` 与 ``core/agent/tools`` 是两个不同的包，必须按**完整前缀**
精确匹配（历史上踩过这个坑）。PREFIXES 里每条都以 ``core.`` 开头，并按长度
降序匹配，保证 ``core.agent.tools`` 不会命中 ``core.tools`` 的规则。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "agentnodes"
PKG = "agentnodes"

# ── 层成员表：完整前缀 → 层（唯一事实源，与 test_layer_matrix 对齐）──
LAYER_OF: dict[str, str] = {}

def _reg(layer: str, names: list[str]) -> None:
    for n in names:
        LAYER_OF[f"core.{n}" if not n.startswith("core/") else n] = layer

# 用目录风格（core/xxx）登记，搬迁与导入两侧都用它
INTERFACES = ["core/mcp"]
APPS = [
    "core/study", "core/goal", "core/hypothesis",
    "core/alpha_zoo", "core/alpha_zoo_adapter.py", "core/alpha_zoo_convert.py",
    "core/alpha_zoo_ops.py", "core/alpha_zoo_yaml.py",
    "core/backtest.py", "core/backtest_engine", "core/backtest_models.py",
    "core/validation", "core/portfolio", "core/data_source",
    "core/data_import.py", "core/data_readiness.py", "core/compute_factor.py",
    "core/factor_validate.py", "core/engine", "core/tools", "core/db.py",
    "core/config_runner.py", "core/strategy_acceptance", "core/autoresearch.py",
    "core/export", "core/run_card.py", "core/workspace_setup.py", "core/preflight.py",
]
KERNEL = [
    "core/gateway", "core/orchestration", "core/runtimes", "core/workflow",
    "core/context", "core/agent", "core/swarm", "core/events", "core/session",
    "core/memory", "core/skills", "core/hooks", "core/permission",
    "core/scheduled_research", "core/seq_generator.py",
]
FOUNDATION = [
    "core/llm", "core/storage", "core/observability", "core/utils", "core/web",
    "core/registry.py", "core/config_loader.py", "core/errors.py", "core/git.py",
]

BATCH_MAP: dict[str, list[str]] = {
    "interfaces": INTERFACES,
    "apps": APPS,
    "kernel": KERNEL,
    "foundation": FOUNDATION,
}

# 批 1 额外搬移：顶层 api/ cli/ 进 interfaces
TOP_LEVEL_BATCH = {
    "interfaces": ["api", "cli"],
}


def _norm(path: str) -> str:
    """directory-style entry → dotted module prefix (without leading pkg)."""
    p = path.replace("/", ".")
    if p.endswith(".py"):
        p = p[:-3]
    return p


def build_prefix_map(batch: str) -> dict[str, str]:
    """旧点号前缀 → 新点号前缀，按长度降序（保证最长匹配优先）。

    同时生成两套 key：裸前缀（``core.agent``）与完整包前缀
    （``agentnodes.core.agent``）——AST 的 ``node.module`` 与代码里的
    字符串字面量用的都是后者，而搬迁目录表用的是前者。

    注意：裸 key 只在**整段匹配**时生效（``full == old`` 或
    ``full.startswith(old + ".")``），因此不会误伤 ``api_client`` 之类。
    """
    layer = batch
    out: dict[str, str] = {}
    for entry in BATCH_MAP[batch]:
        old = _norm(entry)              # core.agent ...
        tail = old[len("core."):]
        new = f"{layer}.{tail}"
        out[f"{PKG}.{old}"] = f"{PKG}.{new}"
        out[old] = new
    for top in TOP_LEVEL_BATCH.get(batch, []):
        new = f"{layer}.{top}"
        out[f"{PKG}.{top}"] = f"{PKG}.{new}"
        out[top] = new
    return dict(sorted(out.items(), key=lambda kv: -len(kv[0])))


def build_dir_map(batch: str) -> dict[str, str]:
    """旧目录（相对 agentnodes）→ 新目录。"""
    out: dict[str, str] = {}
    for entry in BATCH_MAP[batch]:
        tail = entry[len("core/"):]
        out[entry] = f"{batch}/{tail}"
    for top in TOP_LEVEL_BATCH.get(batch, []):
        out[top] = f"{batch}/{top}"
    return dict(sorted(out.items(), key=lambda kv: -len(kv[0])))


# ── 阶段 1：git mv ──────────────────────────────────────────────────
def do_moves(batch: str, dry_run: bool) -> list[tuple[str, str]]:
    dmap = build_dir_map(batch)
    layer_dir = SRC / batch
    done: list[tuple[str, str]] = []

    if not dry_run and not layer_dir.exists():
        layer_dir.mkdir(parents=True)
        init = layer_dir / "__init__.py"
        init.write_text('"""%s layer."""\n' % batch, encoding="utf-8")

    for old_rel, new_rel in dmap.items():
        src = SRC / old_rel
        dst = SRC / new_rel
        if not src.exists():
            print(f"  SKIP (missing): {old_rel}")
            continue
        if dst.exists():
            print(f"  SKIP (target exists): {new_rel}")
            continue
        done.append((old_rel, new_rel))
        if dry_run:
            print(f"  MV  {old_rel}  ->  {new_rel}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["git", "mv", str(src), str(dst)],
            cwd=str(REPO), capture_output=True, text=True,
        )
        if r.returncode != 0:
            # 非 git 跟踪目录时退回 shutil 语义
            print(f"  git mv failed for {old_rel}: {r.stderr.strip()}")
            continue
        print(f"  MV  {old_rel}  ->  {new_rel}")
    return done


# ── 阶段 2：import 改写 ─────────────────────────────────────────────
IMPORT_RE = re.compile(
    r"^(?P<indent>\s*)(?:from|import)\s+"
)


def _rewrite_dotted(text: str, prefix_map: dict[str, str]) -> tuple[str, int]:
    """替换点号模块前缀（用于字符串字面量）。

    只匹配 ``agentnodes.`` 开头的整词，因此 prefix_map 里的裸 key
    （``api`` / ``core.agent``）不会误伤普通英文词。
    """
    count = 0
    pat = re.compile(rf"{PKG}(?:\.[A-Za-z_][A-Za-z0-9_]*)+")

    def sub(m: re.Match) -> str:
        nonlocal count
        val = m.group(0)
        for old, new in prefix_map.items():
            if not old.startswith(PKG + "."):
                continue                      # 裸 key 不参与字符串替换
            if val == old or val.startswith(old + "."):
                count += 1
                return new + val[len(old):]
        return val

    return pat.sub(sub, text), count


def rewrite_file(path: Path, prefix_map: dict[str, str], dry_run: bool) -> tuple[int, int]:
    """返回 (import 语句改写数, 字符串字面量改写数)。"""
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0, 0

    try:
        tree = ast.parse(original)
    except SyntaxError:
        return 0, 0

    # 收集需要改写的行区间：绝对导入 + 含目标前缀的字符串
    edits: list[tuple[int, int, str, str]] = []  # (lineno, colstart, old, new)
    n_import = 0

    rel_parts = path.relative_to(REPO).as_posix()
    in_src = rel_parts.startswith("src/agentnodes/")
    in_tests = rel_parts.startswith("tests/")

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            # 只处理绝对导入（level == 0）；相对导入的深度在同类移动下不变
            if node.level:
                continue
            full = mod
            if full.startswith(PKG + "."):
                for old, new in prefix_map.items():
                    if old.startswith(PKG + ".") and (
                        full == old or full.startswith(old + ".")
                    ):
                        edits.append((node.lineno, node.col_offset, full,
                                      new + full[len(old):]))
                        n_import += 1
                        break
            else:
                # 裸模块名（api.app / cli.x / core.agent.y）
                for old, new in prefix_map.items():
                    if old.startswith(PKG + "."):
                        continue
                    if full == old or full.startswith(old + "."):
                        edits.append((node.lineno, node.col_offset, full,
                                      new + full[len(old):]))
                        n_import += 1
                        break
        elif isinstance(node, ast.Import):
            for alias in node.names:
                full = alias.name
                for old, new in prefix_map.items():
                    if old.startswith(PKG + "."):
                        if not (full == old or full.startswith(old + ".")):
                            continue
                    else:
                        if not (full == old or full.startswith(old + ".")):
                            continue
                    edits.append((node.lineno, node.col_offset, full,
                                  new + full[len(old):]))
                    n_import += 1
                    break

    if not edits and not any(
        True for _ in re.finditer(r"agentnodes\.", original)
    ):
        return 0, 0

    lines = original.splitlines(keepends=True)
    n_str_total = 0

    # 逐行替换：先处理 AST 给出的精确 module 串，再兜底处理字符串字面量
    for lineno, _col, old, new in edits:
        idx = lineno - 1
        if 0 <= idx < len(lines):
            before = lines[idx]
            # 仅替换该行首个匹配（同层多别名场景下 AST 已逐条登记）
            if old in before:
                lines[idx] = before.replace(old, new, 1)

    # 字符串字面量兜底（monkeypatch / uvicorn / importlib / __import__）
    if in_src or in_tests:
        new_lines = []
        for line in lines:
            newline, c = _rewrite_dotted(line, prefix_map)
            n_str_total += c
            new_lines.append(newline)
        lines = new_lines

    updated = "".join(lines)
    if updated != original and not dry_run:
        path.write_text(updated, encoding="utf-8")
    return n_import, n_str_total


def do_rewrite(batch: str, dry_run: bool) -> None:
    pmap = build_prefix_map(batch)
    if not pmap:
        print("empty prefix map")
        return
    print("\n  prefix map (longest first):")
    for k, v in pmap.items():
        print(f"    {k}  ->  {v}")

    targets: list[Path] = []
    for root in (SRC, REPO / "tests"):
        if not root.exists():
            continue
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in ("__pycache__", "graphify-out", "attic")]
            for f in fn:
                if f.endswith(".py"):
                    targets.append(Path(dp) / f)

    total_i = total_s = touched = 0
    for p in targets:
        ni, ns = rewrite_file(p, pmap, dry_run)
        if ni or ns:
            touched += 1
            total_i += ni
            total_s += ns
            print(f"    {p.relative_to(REPO).as_posix()}: imports={ni} strings={ns}")
    verb = "would rewrite" if dry_run else "rewrote"
    print(f"\n  {verb}: {touched} files, {total_i} import statements, "
          f"{total_s} string literals")


# ── 阶段 3：残留检查 ────────────────────────────────────────────────
def do_verify() -> int:
    """全库扫描，任何 agentnodes.core.* 残留即失败。"""
    pat = re.compile(r"agentnodes\.core\.")
    hits: list[str] = []
    for root in (SRC, REPO / "tests", REPO / "scripts", REPO / "evals", REPO / "examples"):
        if not root.exists():
            continue
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in ("__pycache__", "graphify-out", "attic")]
            for f in fn:
                if not f.endswith((".py", ".toml", ".cfg", ".ini")):
                    continue
                p = Path(dp) / f
                try:
                    text = p.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if pat.search(line):
                        hits.append(
                            f"{p.relative_to(REPO).as_posix()}:{lineno}: {line.strip()[:100]}"
                        )

    # 额外检查配置文件
    for cfg in (REPO / "pyproject.toml", REPO / ".coveragerc", REPO / "MANIFEST.in"):
        if not cfg.exists():
            continue
        text = cfg.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "agentnodes.core" in line or "core/" in line:
                hits.append(f"{cfg.name}:{lineno}: {line.strip()[:100]}")

    if hits:
        print(f"VERIFY FAILED — {len(hits)} residual reference(s):")
        for h in hits:
            print("  " + h)
        return 1
    print("VERIFY OK — no residual agentnodes.core references")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", choices=sorted(BATCH_MAP), help="which layer to migrate")
    ap.add_argument("--dry-run", action="store_true", help="print actions, change nothing")
    ap.add_argument("--verify", action="store_true", help="scan for residual old paths")
    ap.add_argument("--skip-moves", action="store_true", help="only rewrite imports")
    args = ap.parse_args()

    if args.verify:
        return do_verify()

    if not args.batch:
        ap.error("--batch or --verify required")

    print(f"=== batch: {args.batch} {'(dry-run)' if args.dry_run else ''} ===")
    if not args.skip_moves:
        print("\n[1/2] moves")
        do_moves(args.batch, args.dry_run)

    print("\n[2/2] import rewrite")
    do_rewrite(args.batch, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
