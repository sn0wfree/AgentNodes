# -*- coding: utf-8 -*-
"""rewrite_import_prefix — 包搬迁的全库前缀改写（文本级，三种形态）。

把对旧模块路径的引用改写为新路径，一次命中三种形态：
  1. module-form    `from agentnodes.core.X import y` / lazy import / 字符串字面量
  2. pkg-import-form `from agentnodes.core import X as y`
  3. path-form      docstring/注释/豁免表里的 `core/X.py` 路径键

边界（历史教训，2026-11-03 v1 大爆炸失败即源于越界）：
  - **只做文本前缀替换，绝不重组语句**——多行 import 被上一版 AST 重构
    压成一行造成 20 处语法损坏。
  - 覆盖不了相对导入（`from .X import`）——那些交给
    fix_relative_imports.py（解析级）；两把刷子必须先后都跑。
  - 同名前缀陷阱：core/tools vs core/agent/tools 靠完整前缀精确匹配规避。

用法（每步迁移协议 §1.2 第 4 步）：
    python scripts/migration/rewrite_import_prefix.py \
        agentnodes.core.tools agentnodes.apps.tools [file...]

配套（同一协议内，顺序执行）：
    fix_relative_imports.py <tail> <new_full_module>   # 解析级相对导入
    python -m compileall -q src/agentnodes tests -x attic
    python scripts/arch/check_dangling_imports.py       # 零悬空才准 commit

命名规范见 scripts/README.md（migration/ = 动词开头，搬迁 step 协议用）。

来源：物理分层迁移 W1-4 沉淀（tmp/rewrite_prefix.py 固化版，W1 期间
扩展出 pkg-import-form 第三模式）。
"""
from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKIP_DIRS = {"__pycache__", "attic", "graphify-out"}


def rewrite(old: str, new: str, files: list[str]) -> int:
    tail = old.rsplit(".", 1)[-1]
    old_pkg, new_pkg = old.rsplit(".", 1)[0], new.rsplit(".", 1)[0]
    old_layer = old.split(".")[1] if old.count(".") >= 1 else old
    new_layer = new.split(".")[1] if new.count(".") >= 1 else new
    path_old = f"{old_layer}/{tail}.py"

    if not files:
        files = []
        for root in ("src", "tests"):
            for dp, dn, fn in os.walk(os.path.join(ROOT, root)):
                dn[:] = [d for d in dn if d not in SKIP_DIRS]
                for f in fn:
                    if f.endswith(".py"):
                        files.append(os.path.join(dp, f))

    total = 0
    for p in files:
        full = p if os.path.isabs(p) else os.path.join(ROOT, p)
        try:
            s = io.open(full, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        n1 = s.count(old)
        pkgform_old = f"{old_pkg} import {tail}"
        n3 = s.count(pkgform_old)
        n2 = s.count(path_old)
        if n1 + n2 + n3 == 0:
            continue
        s2 = (s.replace(old, new)
               .replace(pkgform_old, f"{new_pkg} import {tail}")
               .replace(path_old, f"{new_layer}/{tail}.py"))
        io.open(full, "w", encoding="utf-8", newline="").write(s2)
        total += 1
        print(f"{p}: {n1} module-form + {n3} pkg-import-form + {n2} path-form")
    return total


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        print("\nusage: python rewrite_import_prefix.py <old_module> <new_module> [file...]")
        return 2
    total = rewrite(sys.argv[1], sys.argv[2], sys.argv[3:])
    print(f"total files rewritten: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
