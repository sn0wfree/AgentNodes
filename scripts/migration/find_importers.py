# -*- coding: utf-8 -*-
"""find_importers — 查询「谁 import 了指定包」（绝对 + 相对，解析级）。

搬迁前的爆破面普查工具：对给定包尾名，列出 src/ tests/ 下所有指向它的
import 语句（绝对形态 + 按 PEP 328 解析后的相对形态），按文件聚合计数。
搬迁计划的数据底册（docs/physical-layer-migration-plan-20261103.md §2.1）
即由此类扫描 + graphify 图交叉生成。

用法（搬迁协议 §1.2 第 1 步「定位受影响文件」）：
    python scripts/migration/find_importers.py tools                 # 语句清单
    python scripts/migration/find_importers.py tools --by-file      # 按文件计数
    python scripts/migration/find_importers.py tools --by-file --root tests

注意：包尾名按完整模块段匹配——查 core/tools 不会误报 core/agent/tools
（历史上 `findstr "tools"` 造成的误判教训）。多尾名可一次查多个。

命名规范见 scripts/README.md（migration/ = 动词开头）。

来源：物理分层迁移 W3a 沉淀（tmp/find_imports_of.py 固化版，修复过两次
PEP 328 解析 bug：.py 后缀未剥、包路径错位）。
"""
from __future__ import annotations

import argparse
import ast
import io
import os
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKIP_DIRS = {"__pycache__", "attic", "graphify-out"}
PREFIX = "strategy_research.core."


def iter_py(root: str):
    base = os.path.join(ROOT, root)
    for dp, dn, fn in os.walk(base):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith(".py"):
                yield os.path.join(dp, f), base


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tails", nargs="+", help="包尾名（core 下），如 tools db")
    ap.add_argument("--by-file", action="store_true", help="按文件聚合计数")
    ap.add_argument("--root", choices=("src", "tests", "both"), default="both")
    args = ap.parse_args()
    roots = ("src", "tests") if args.root == "both" else (args.root,)

    stmts: dict[str, list] = defaultdict(list)
    for root in roots:
        for path, base in iter_py(root):
            try:
                tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
            except SyntaxError:
                continue
            pkg = os.path.relpath(path, base).replace(os.sep, ".").split(".")[:-2]
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level == 0:
                    m = node.module or ""
                else:
                    base = pkg[: len(pkg) - (node.level - 1)]
                    m = ".".join(base + ([node.module] if node.module else []))
                if not m:
                    continue
                for tail in args.tails:
                    full = PREFIX + tail
                    if m == full or m.startswith(full + "."):
                        stmts[tail].append(f"{path}:{node.lineno} lvl={node.level}: "
                                           f"{ast.unparse(node)[:90]}")
                        break

    for tail in args.tails:
        items = stmts.get(tail, [])
        print(f"== {PREFIX}{tail}: {len(items)} statements ==")
        if args.by_file:
            # Windows 盘符含冒号（C:\...），不能按第一个冒号切——
            # 行格式固定为 "<path>:<lineno> lvl=..."，从行尾剥掉
            # ": lvl..." 与 ":<lineno>" 两段才是路径。
            by_file: Counter = Counter(
                l.rsplit(":", 2)[0] if " lvl=" in l else l.split(":")[0]
                for l in items
            )
            for f, c in by_file.most_common():
                print(f"  {c:>3}  {f}")
        else:
            for l in items:
                print(f"  {l}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
