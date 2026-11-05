# -*- coding: utf-8 -*-
"""fix_relative_imports — 相对导入的解析级改写（文本替换的盲区补丁）。

按 PEP 328 解析每条相对导入的**真实目标**；凡解析到
``agentnodes.core.<tail>...`` 的语句，改写为指定的新绝对路径。

为什么存在：rewrite_import_prefix.py 只做文本前缀替换，而
`from ...tools.data_clean import X` 这类相对导入字面上不含
`agentnodes.core.tools`——文本工具永远看不见它。物理分层迁移期间
本工具（及其前身逐处手工裁决）处理了 100+ 处；两次重大遗漏
（factor_tools 的 level-3、data_clean_tools 同形）都是先靠它同类化才收口。

两种运行模式：
  --check      只报告（解析到旧目标的相对导入清单），不改写——
               即「扫描器」用法，等价于迁移协议第 6 步的残留检查
  （默认）     解析到 <old> 的语句改写为 <new>

用法（每步迁移协议 §1.2 第 4.5 步，紧跟 rewrite_import_prefix）：
    python scripts/migration/fix_relative_imports.py --check tools
    python scripts/migration/fix_relative_imports.py tools agentnodes.apps.tools

注意：改写含多行 import 时按语句整体替换（AST 定位 + 源文替换），
不做语句重排——v1 教训同 rewrite_import_prefix。

命名规范见 scripts/README.md（migration/ = 动词开头）。

来源：物理分层迁移 W3a 沉淀（tmp/fix_rel_residue.py 固化版）。
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKIP_DIRS = {"__pycache__", "attic", "graphify-out"}


def iter_py():
    for root in ("src", "tests"):
        base = os.path.join(ROOT, root)
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for f in fn:
                if f.endswith(".py"):
                    # yield 绝对 base——relpath 的基准，CWD 无关
                    yield os.path.join(dp, f), base


def run(tail: str, new_mod: str | None) -> tuple[int, int]:
    old_prefix = "agentnodes.core." + tail
    statements = 0
    files_changed = 0
    for path, base in iter_py():
        src = io.open(path, encoding="utf-8", errors="replace").read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        pkg = os.path.relpath(path, base).replace(os.sep, ".").split(".")[:-2]
        edits: list[tuple[int, str, str]] = []  # (lineno, old_stmt, new_stmt)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level == 0:
                continue
            anchor = pkg[: len(pkg) - (node.level - 1)]  # 不覆盖外层 base（绝对路径）
            resolved = ".".join(anchor + ([node.module] if node.module else []))
            if not (resolved == old_prefix or resolved.startswith(old_prefix + ".")):
                continue
            suffix = resolved[len(old_prefix):]
            stmt = ast.get_source_segment(src, node)
            if stmt is None:
                continue
            if new_mod is None:
                print(f"{path}:{node.lineno}: {'.' * node.level}{node.module} -> {resolved}")
                statements += 1
                continue
            new_stmt = stmt.replace("." * node.level + (node.module or ""),
                                    new_mod + suffix, 1)
            edits.append((node.lineno, stmt, new_stmt))
        if edits and new_mod:
            joined = src
            for _ln, old_stmt, new_stmt in edits:
                joined = joined.replace(old_stmt, new_stmt, 1)
            io.open(path, "w", encoding="utf-8", newline="").write(joined)
            statements += len(edits)
            files_changed += 1
            print(f"{path}: {len(edits)} rewritten")
    return statements, files_changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tail", help="旧包尾名，如 tools")
    ap.add_argument("new_mod", nargs="?", default=None,
                    help="新绝对模块路径；缺省 = --check 只报告")
    args = ap.parse_args()
    statements, files = run(args.tail, args.new_mod)
    verb = "reported" if args.new_mod is None else "rewritten"
    print(f"{verb}: {statements} statements in {files} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
