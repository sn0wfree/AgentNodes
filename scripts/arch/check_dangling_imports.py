# -*- coding: utf-8 -*-
"""check_dangling_imports — 悬空 import 门禁（解析级，非文本级）。

扫描 src/ 与 tests/ 全部 .py，按 PEP 328 解析每条 import 的**真实目标**
（绝对路径直接取 module；相对导入按 importer 包深度剥离 level-1），
断言目标模块在 agentnodes/ 下真实存在（.py 文件或目录）。

为什么存在：文本前缀替换（搬迁改写工具的主模式）对相对导入天然盲区——
`from ...tools.data_clean import` 这类语句在搬迁后解析到不存在的模块，
但字面上不含任何旧前缀。2026-11-03 物理分层迁移期间，本检查先后抓出
8 处悬空（7 处为迁移前既有的静默功能失效：408 个 YAML alpha 走不通、
LLM DAG 规划从未启用等）。

覆盖两种 import 形态（缺一不可）：
    from agentnodes.apps.db import get_conn   -> ast.ImportFrom
    import agentnodes.apps.db as db           -> ast.Import
2026-11-05 批次 2 教训：本脚本曾只遍历 ast.ImportFrom，漏掉 ast.Import ——
删掉 interfaces/api/routers/workflow.py 后，两个测试文件用
`import ...routers.workflow as wf` 继续引用它，门禁显示 0 悬空，
而 24 个用例在 fixture setup 期才炸（收集期不报错）。

用法（发现问题时退出码 1，可直接接 CI / pre-commit）：
    python scripts/arch/check_dangling_imports.py            # 扫 src + tests
    python scripts/arch/check_dangling_imports.py --quiet    # 只在失败时输出
    python scripts/arch/check_dangling_imports.py --layers   # 附带层方向计数

``--layers`` 复用 ``scripts/arch/layer_matrix.py``（与 test_layer_matrix
同一张层归属表、同一套判定算法），因此它报出的 L3→L2 与棘轮门禁**必然
一致**。默认不带该开关，保持本脚本原有的单一职责（悬空检查）。

命名规范见 scripts/README.md（arch/ = check_*，发现即失败）。

来源：物理分层迁移 W2-12a 沉淀（tmp/verify_residue.py 固化版）。
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import pathlib
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PKG_DIR = os.path.join(ROOT, "src", "agentnodes")


def module_exists(mod: str) -> bool:
    """agentnodes 相对模块路径是否真实存在（.py 或目录）。"""
    if not mod.startswith("agentnodes"):
        return True  # 第三方 / 标准库，不在本门禁范围
    parts = mod.split(".")[1:]
    if not parts:
        return True
    p = os.path.join(PKG_DIR, *parts)
    return os.path.isfile(p + ".py") or os.path.isdir(p)


def package_dir(mod: str) -> str | None:
    """mod 若是**包**（目录且含 __init__.py）返回其目录，否则 None。

    只有包才需要检查 `from pkg import name` 的 name 是否存在——包的
    `__init__.py` 是命名空间；普通模块的 `from mod import name` 取的是
    模块属性，静态不可判定，一律放过。
    """
    if not mod.startswith("agentnodes"):
        return None
    parts = mod.split(".")[1:]
    if not parts:
        return None
    d = os.path.join(PKG_DIR, *parts)
    if os.path.isdir(d) and os.path.isfile(os.path.join(d, "__init__.py")):
        return d
    return None


def _bound_names(stmts: list) -> set[str]:
    """一批顶层语句在模块命名空间里绑定的名字。"""
    names: set[str] = set()
    for node in stmts:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.Try):
            # try: from .x import y / except ImportError: y = None —— 两种分支都绑定
            names |= _bound_names(node.body)
            for h in node.handlers:
                names |= _bound_names(h.body)
            names |= _bound_names(node.orelse)
            names |= _bound_names(node.finalbody)
    return names


def package_exports(init_path: str) -> set[str] | None:
    """包 `__init__.py` 顶层绑定的名字集合。

    返回 None 表示**无法静态判定**（有 PEP 562 的 `__getattr__` 兜底），
    此时该包的 from-import 一律放过 —— 宁可漏报也不误报。
    """
    try:
        src = io.open(init_path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return None
    names = _bound_names(tree.body)
    if "__getattr__" in names:
        return None
    return names


EXEMPT_MARK = "dangling-import: ok"


def _exempt(src_lines: list[str], node, alias) -> bool:
    """该 import 是否带 ``# dangling-import: ok`` 豁免标记。

    豁免的正当场景：**断言 import 会失败**的测试。
    例如 ``tests/test_prompt_builder_integration.py`` 里
    ``with pytest.raises(ImportError): from ... import _CHAT_PROMPT_PATH``
    —— 那个名字本就不该存在，门禁若报它就是误报。
    标记写在 import 语句行尾（与 ``check_path_literals.py`` 的
    ``# path-literal: ok`` 同款约定）。
    """
    for lineno in {getattr(alias, "lineno", None), node.lineno}:
        if not lineno:
            continue
        if 1 <= lineno <= len(src_lines) and EXEMPT_MARK in src_lines[lineno - 1]:
            return True
    return False


def scan(roots: list[str]) -> list[str]:
    bad: list[str] = []
    for root in roots:
        base = os.path.join(ROOT, root)
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in ("__pycache__", "attic", "graphify-out")]
            for f in fn:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(dp, f)
                try:
                    src = io.open(path, encoding="utf-8", errors="replace").read()
                    tree = ast.parse(src)
                except SyntaxError:
                    continue  # 语法错误由编译门禁负责
                src_lines = src.splitlines()
                # 相对基准必须用绝对 base（CWD 不在仓库根时 relpath 会跨盘报错）
                pkg = os.path.relpath(path, base).replace(os.sep, ".").split(".")[:-2]
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        # `import a.b.c as x` —— 别名形式此前被漏检：
                        # 2026-11-05 批次 2 删掉 interfaces/api/routers/workflow.py 后，
                        # 两个测试文件用 `import ...routers.workflow as wf` 继续引用它，
                        # 收集期不报错、fixture setup 期才炸 —— 门禁当时是绿的。
                        for alias in node.names:
                            mod = alias.name
                            if not mod.startswith("agentnodes"):
                                continue
                            if not module_exists(mod):
                                if _exempt(src_lines, node, alias):
                                    continue
                                bad.append(f"{path}:{node.lineno}: "
                                           f"import {alias.name} -> {mod}")
                        continue
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    if node.level == 0:
                        mod = node.module or ""
                    else:
                        base_pkg = pkg[: len(pkg) - (node.level - 1)]
                        mod = ".".join(base_pkg + ([node.module] if node.module else []))
                    if not mod.startswith("agentnodes"):
                        continue
                    if not module_exists(mod):
                        if not _exempt(src_lines, node, None):
                            bad.append(f"{path}:{node.lineno}: "
                                       f"{'.' * node.level}{node.module or ''} -> {mod}")
                        continue
                    # 第三种形态：`from <包> import <名字>`
                    # 模块本身存在不代表被 import 的名字存在 —— 删掉
                    # interfaces/api/routers/workflow.py 后，
                    # `from agentnodes.interfaces.api.routers import workflow`
                    # 的宿主包还在，门禁看不出名字已消失。
                    pkg_dir = package_dir(mod)
                    if pkg_dir is None:
                        continue
                    exports = package_exports(os.path.join(pkg_dir, "__init__.py"))
                    if exports is None:
                        continue  # PEP 562 __getattr__ 兜底 → 无法静态判定，放过
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        if os.path.isfile(os.path.join(pkg_dir, alias.name + ".py")):
                            continue
                        if os.path.isdir(os.path.join(pkg_dir, alias.name)):
                            continue
                        if alias.name in exports:
                            continue
                        if _exempt(src_lines, node, alias):
                            continue
                        bad.append(
                            f"{path}:{node.lineno}: "
                            f"from {mod} import {alias.name} -> {mod}.{alias.name}")
    return bad


def report_layers() -> int:
    """报告层方向违规计数（``--layers``）。

    层归属与判定原语来自 ``scripts/arch/layer_matrix.py`` —— 与
    ``tests/test_layer_matrix.py`` **同一张表、同一套算法**。两者若各存
    一份，迟早漂移出不同结论，"哪个数字是真的"就无从判断。

    退出码：0 = 无上行违规；1 = 有（与悬空检查同型，可直接接 CI）。
    注意本函数只报**计数**，是否超棘轮基线由 test_layer_matrix 裁决。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import layer_matrix as lm  # noqa: E402

    src_root = pathlib.Path(PKG_DIR)
    viol, edges = lm.collect(src_root)

    print("== cross-layer import matrix ==")
    print(lm.fmt_edges(edges))
    print("== upward violations ==")
    total = 0
    for l3 in lm.ORDER:
        for l2 in lm.ORDER:
            if lm.ORDER.index(l2) < lm.ORDER.index(l3):
                key = f"{l3}>{l2}"
                n = viol.get(key, 0)
                total += n
                print(f"  {key}: {n}")
    print(f"\ncheck_dangling_imports --layers: L3→L2 = {viol.get('L3>L2', 0)}")
    print(f"  （L3→L2 是 goal 关心的那个数；上行违规总数 = {total}）")
    return 1 if total else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quiet", action="store_true", help="通过时不输出")
    ap.add_argument(
        "--layers",
        action="store_true",
        help="额外报告层方向违规计数（L3→L2 等），复用 layer_matrix 算法",
    )
    args = ap.parse_args()

    rc = 0
    if args.layers:
        rc = report_layers()
        print()

    bad = scan(["src", "tests"])
    if bad:
        print(f"悬空 import：{len(bad)} 处（目标模块不存在）", file=sys.stderr)
        for b in bad:
            print("  " + b, file=sys.stderr)
        return 1
    if not args.quiet:
        print("check_dangling_imports: 0 悬空")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
