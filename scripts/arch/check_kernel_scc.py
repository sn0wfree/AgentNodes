# -*- coding: utf-8 -*-
"""check_kernel_scc — kernel 子包级 import 环门禁（模块级硬拦 / lazy 棘轮）。

为什么存在（20261118 计划 §11.4-K2 第 4 条）：kernel 子包间的**模块级
import 环**会在 import 期产生初始化顺序耦合（谁先谁后脆弱）；**函数内
lazy import 边**是运行时协作（GoalWorkflow×Gateway、evolution 按需解析
等已裁决保留），不该按静态边数机械拆分，但新出现的环必须先记录 owner。

两级判定：
1. **模块级 import 边**（顶层语句）构成的 SCC（强连通分量）→ 硬失败
   ——模块级环 = import 期初始化风险，发现即失败；
2. **全量边（含函数内 lazy）**构成的 SCC → 与 ``KNOWN_LAZY_SCCS`` 对照：
   未登记的新 lazy 环 → 失败（须先记录 owner 与理由）；已登记 → 报告通过。

用法：
    python scripts/arch/check_kernel_scc.py            # 判定 + 报告
    python scripts/arch/check_kernel_scc.py --quiet    # 只在失败时输出

命名规范见 scripts/README.md（arch/ = check_*，发现即失败）。

来源：20261118 架构计划 §11.4-K2 / §12.1 顺序 4 沉淀。
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / "src" / "agentnodes" / "kernel"

SUBS = sorted(p.name for p in KERNEL.iterdir()
              if p.is_dir() and (p / "__init__.py").exists() and p.name != "__pycache__")

#: 已登记 owner 的 lazy 环（成员集合 → owner/理由）。
#: 新出现的 lazy SCC 必须先在此登记（记录 owner 与理由）才算通过。
KNOWN_LAZY_SCCS: dict[frozenset, str] = {
    # GoalWorkflow×Gateway 会话协调 + evolution 按需解析 + swarm/workflow
    # 共享契约——全部为**函数内 lazy 协作边**（模块级无环，见 B2/B3/顺序④）。
    frozenset({"agent", "evolution", "orchestration", "runtimes",
               "swarm", "workflow"}):
        "运行时协作闭环（全函数内 lazy）：goal×chat 经 Gateway lease 协调、"
        "evolution 按需解析 skill_memory、swarm/workflow 经 swarm.types "
        "共享契约；owner=20261118 计划 §8.3 裁决 2/3 + §11.3 各行判定",
}


def collect(module_level_only: bool) -> dict[tuple[str, str], list[str]]:
    """扫描 kernel 子包间 import 边。module_level_only=True 只收顶层语句。"""
    edges: dict[tuple[str, str], list[str]] = {}
    for sub in SUBS:
        for p in sorted((KERNEL / sub).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            rel = p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) \
                else p.as_posix()
            is_init = p.name == "__init__.py"
            base_parts = ["agentnodes", "kernel", sub]
            if not is_init:
                base_parts.append(p.stem)

            def resolve(stmt):
                if stmt.level == 0:
                    return stmt.module or ""
                depth = stmt.level
                parts = base_parts[: len(base_parts) - depth] if is_init else \
                    base_parts[: max(0, len(base_parts) - depth + 1)]
                return ".".join(parts + ([stmt.module] if stmt.module else []))

            stmts = tree.body if module_level_only else list(ast.walk(tree))
            for stmt in stmts:
                if not isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    continue
                mods = ([a.name for a in stmt.names] if isinstance(stmt, ast.Import)
                        else [resolve(stmt) if stmt.level else (stmt.module or "")])
                for m in mods:
                    parts = m.split(".")[2:] if m.startswith("agentnodes.kernel") else []
                    if parts and parts[0] in SUBS and parts[0] != sub:
                        edges.setdefault((sub, parts[0]), []).append(f"{rel}:{stmt.lineno}")
    return edges


def tarjan_sccs(adj: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: list[list[str]] = []
    counter = [0]

    def strong(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in sorted(adj.get(v, ())):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            out.append(comp)

    for v in sorted(adj):
        if v not in index:
            strong(v)
    return [c for c in out if len(c) > 1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quiet", action="store_true", help="只在失败时输出")
    args = ap.parse_args()
    rc = 0

    module_edges = collect(module_level_only=True)
    all_edges = collect(module_level_only=False)

    adj_m: dict[str, set[str]] = {}
    adj_a: dict[str, set[str]] = {}
    for (a, b) in module_edges:
        adj_m.setdefault(a, set()).add(b)
        adj_m.setdefault(b, set())
    for (a, b) in all_edges:
        adj_a.setdefault(a, set()).add(b)
        adj_a.setdefault(b, set())

    m_sccs = tarjan_sccs(adj_m)
    if m_sccs:
        print(f"[X] kernel 模块级 import 环：{len(m_sccs)} 个（import 期初始化风险）",
              file=sys.stderr)
        for comp in m_sccs:
            print(f"    {' <-> '.join(sorted(comp))}", file=sys.stderr)
        rc = 1
    elif not args.quiet:
        print("[OK] kernel 模块级 import 图无环")

    a_sccs = tarjan_sccs(adj_a)
    known = [c for c in a_sccs if frozenset(c) in KNOWN_LAZY_SCCS]
    unknown = [c for c in a_sccs if frozenset(c) not in KNOWN_LAZY_SCCS]
    if unknown and not args.quiet:
        print(f"[报告] 全量（含函数内 lazy 协作）SCC：{len(unknown)} 个未登记——"
              f"新环须先记录 owner 与理由（加入 KNOWN_LAZY_SCCS）")
        for comp in unknown:
            print(f"    {' <-> '.join(sorted(comp))}")
        rc = max(rc, 1)
    if known and not args.quiet:
        for comp in known:
            reason = KNOWN_LAZY_SCCS[frozenset(comp)]
            print(f"[报告] 已登记 lazy 环：{' <-> '.join(sorted(comp))} — {reason[:60]}…")

    if not args.quiet and rc == 0:
        print("check_kernel_scc: 模块级无环；lazy 环全部已登记")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
