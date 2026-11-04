# -*- coding: utf-8 -*-
"""check_dangling_imports — 悬空 import 门禁（解析级，非文本级）。

扫描 src/ 与 tests/ 全部 .py，按 PEP 328 解析每条 import 的**真实目标**
（绝对路径直接取 module；相对导入按 importer 包深度剥离 level-1），
断言目标模块在 strategy_research/ 下真实存在（.py 文件或目录）。

为什么存在：文本前缀替换（搬迁改写工具的主模式）对相对导入天然盲区——
`from ...tools.data_clean import` 这类语句在搬迁后解析到不存在的模块，
但字面上不含任何旧前缀。2026-11-03 物理分层迁移期间，本检查先后抓出
8 处悬空（7 处为迁移前既有的静默功能失效：408 个 YAML alpha 走不通、
LLM DAG 规划从未启用等）。

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
PKG_DIR = os.path.join(ROOT, "src", "strategy_research")


def module_exists(mod: str) -> bool:
    """strategy_research 相对模块路径是否真实存在（.py 或目录）。"""
    if not mod.startswith("strategy_research"):
        return True  # 第三方 / 标准库，不在本门禁范围
    parts = mod.split(".")[1:]
    if not parts:
        return True
    p = os.path.join(PKG_DIR, *parts)
    return os.path.isfile(p + ".py") or os.path.isdir(p)


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
                # 相对基准必须用绝对 base（CWD 不在仓库根时 relpath 会跨盘报错）
                pkg = os.path.relpath(path, base).replace(os.sep, ".").split(".")[:-2]
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    if node.level == 0:
                        mod = node.module or ""
                    else:
                        base_pkg = pkg[: len(pkg) - (node.level - 1)]
                        mod = ".".join(base_pkg + ([node.module] if node.module else []))
                    if not mod.startswith("strategy_research"):
                        continue
                    if not module_exists(mod):
                        bad.append(f"{path}:{node.lineno}: "
                                   f"{'.' * node.level}{node.module or ''} -> {mod}")
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
