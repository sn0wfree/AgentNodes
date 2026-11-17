# -*- coding: utf-8 -*-
"""相对导入禁用门禁：``src/agentnodes`` 内 ImportFrom 的 level 必须全为 0。

**为什么需要它**（docs/design/absolute-imports-migration-20261117.md）：
2026-11-17 把全仓 498 处相对导入改为绝对导入（6 批）。相对导入的问题不是风格：
`from .x import y` 的可读性依赖「读的人知道当前文件在哪个包」，重构搬文件时
报错信息只剩 `attempted relative import with no known parent package`，
且 IDE / 代码搜索难以从符号直接定位模块。绝对导入让「这个符号住在哪个模块」
在 import 行上一眼可见。

没有门禁，新代码会自然写回相对导入（IDE 自动补全默认偏好相对）—— 这是本任务
的持久收益所在。

**豁免**：无。tests/ 不在本门禁范围（tests 不是包，`tests/_fixtures` 用相对
导入是其包内相对形态，属既定选择）。

R-019：门禁的价值 = 注入违规后会不会红 —— `--selftest` 在临时目录注入验证。

用法：
    python check_no_relative_imports.py             # 检查（rc 0/1）
    python check_no_relative_imports.py --selftest  # 注入违规自检
"""
from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "agentnodes"


def _iter_py(root: Path):
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        yield p


def find_relative_imports(src_root: Path) -> list[tuple[str, int, str]]:
    """返回 [(相对路径, 行号, "from ... import ... 形态"), ...]。"""
    hits: list[tuple[str, int, str]] = []
    for p in _iter_py(src_root):
        rel = p.relative_to(src_root).as_posix()
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                hits.append(
                    (rel, node.lineno, f"{'.' * node.level}{node.module or '(包)'}")
                )
    return hits


def selftest() -> int:
    """R-019：注入相对导入必须被抓到；绝对导入必须不报。"""
    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td) / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "bad.py").write_text(
            "from .models import Thing\nfrom ..sibling import other\n", encoding="utf-8")
        (pkg / "clean.py").write_text(
            "from agentnodes.pkg.models import Thing\n", encoding="utf-8")
        hits = find_relative_imports(Path(td))
        if len(hits) != 2:
            print(f"[X] selftest 失败：期望 2 处相对导入，实得 {hits}")
            return 1
        if any(h[0] == "pkg/clean.py" for h in hits):
            print(f"[X] selftest 失败：绝对导入被误报 {hits}")
            return 1
        if {h[1] for h in hits} != {1, 2}:
            print(f"[X] selftest 失败：行号不全 {hits}")
            return 1
    print("[OK] selftest：单点/双点相对导入均报出，绝对导入零误报")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    hits = find_relative_imports(SRC)
    if hits:
        print(f"[X] src/agentnodes 出现 {len(hits)} 处相对导入 —— 必须改为绝对导入：")
        for rel, ln, form in hits:
            print(f"    {rel}:{ln}  from {form} import ...")
        print("    改法：from .models import X  →  from agentnodes.<pkg>.models import X")
        print("    （见 docs/design/absolute-imports-migration-20261117.md）")
        return 1
    print("[OK] 相对导入：src/agentnodes 内 0 处（全绝对导入）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
