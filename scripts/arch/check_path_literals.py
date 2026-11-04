# -*- coding: utf-8 -*-
"""check_path_literals — 路径字面量门禁（import 改写工具的双盲区）。

扫描 src/ tests/ scripts/ 中的**非 import 语句**（Path 拼接、os.path.join、
路径常量），断言其中不含指向已消失/已搬迁目录的旧路径段。覆盖两种形态：

  A. "core" 段拼接     —— `Path(__file__).parent / "core" / "swarm" / "presets"`  # path-literal: ok
  B. 包内层段拼接      —— `"src" / "strategy_research" / "api" / ...`  # path-literal: ok
     （api/cli 等顶层包搬入 interfaces 后，层段名必须同步）

为什么存在：搬迁改写工具只处理 import 语句与字符串形式的模块前缀；
`Path 拼接` 形态两者都不经过。物理分层迁移期间本类问题共 11 处
（mcp/swarm/goal_workflow/dag_planner/study/cli 的 presets|skills|templates
目录定位），全部修为「跟随目标包 __file__」模式。2026-11-04 W7-3b 又抓出
test_study_event_emit 的 "api" 层段残留。

处理原则（写在这里防止后来者改错方向）：运行时资源目录**不要数 parent
层数**，改为 `Path(目标模块.__file__).parent / "资源"`——目标包再搬迁时自愈。

用法（发现问题时退出码 1）：
    python scripts/arch/check_path_literals.py
    python scripts/arch/check_path_literals.py --allow core   # 临时豁免某段

白名单：BANNED 段名即物理分层迁移前的旧结构（core/）。若未来某段确属
合法（如文档字符串示例），在该行行尾加 `# path-literal: ok` 豁免。

命名规范见 scripts/README.md（arch/ = check_*，发现即失败）。

来源：物理分层迁移 W4d-2 / W7-3b 沉淀（tmp/scan_path_literals.py +
tmp/scan_pkg_paths.py 合并固化版）。
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 形态 A：'"core"' 段参与路径拼接（旧物理结构，W7-1 已删除该目录）
PATTERN_CORE_SEG = re.compile(r'["\']core["\']\s*/\s*|os\.path\.join\([^)]*["\']core["\']')
# 形态 B：包内层段拼接（搬迁后层段名必须与 src/strategy_research 顶层一致）
PATTERN_PKG_SEG = re.compile(r'"strategy_research"\s*/\s*"(\w+)"')
VALID_LAYERS = {"apps", "kernel", "foundation", "interfaces", "templates"}
EXEMPT_MARK = "path-literal: ok"

# 形态 A 的合法豁免：一次性历史迁移脚本自身（它们操作的就是旧路径）
EXEMPT_FILES_A = ("migrate_layers.py",)


def scan(allow: set[str]) -> list[str]:
    bad: list[str] = []
    for root in ("src", "tests", "scripts"):
        base = os.path.join(ROOT, root)
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in ("__pycache__", "attic", "graphify-out")]
            for f in fn:
                if not f.endswith((".py", ".toml", ".cfg", ".ini")):
                    continue
                path = os.path.join(dp, f)
                rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                try:
                    lines = io.open(path, encoding="utf-8", errors="replace").read().splitlines()
                except OSError:
                    continue
                for i, line in enumerate(lines, 1):
                    s = line.strip()
                    if s.startswith("#") or EXEMPT_MARK in line:
                        continue
                    hit = None
                    if PATTERN_CORE_SEG.search(line) and not any(
                        x in rel for x in EXEMPT_FILES_A
                    ):
                        hit = "core 段拼接（旧结构已删除）"
                    else:
                        m = PATTERN_PKG_SEG.search(line)
                        if m and m.group(1) not in VALID_LAYERS and m.group(1) not in allow:
                            hit = f'层段 "{m.group(1)}" 不存在（合法: {sorted(VALID_LAYERS)}）'
                    if hit:
                        bad.append(f"{path}:{i}: [{hit}] {s[:110]}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--allow", action="append", default=[],
                    help="临时豁免的层段名（可多次）")
    ap.add_argument("--quiet", action="store_true", help="通过时不输出")
    args = ap.parse_args()
    bad = scan(set(args.allow))
    if bad:
        print(f"路径字面量问题：{len(bad)} 处", file=sys.stderr)
        for b in bad:
            print("  " + b, file=sys.stderr)
        print("\n修复方向：运行时资源目录改用 Path(目标模块.__file__).parent 定位，"
              "不要数 parent 层数。", file=sys.stderr)
        return 1
    if not args.quiet:
        print("check_path_literals: 0 问题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
