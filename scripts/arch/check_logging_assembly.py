# -*- coding: utf-8 -*-
"""日志装配纪律门禁：装配只许在 observability，模块 logger 只许是空壳。

**为什么需要它**（终局裁决 docs/design/logging-unified-hub-design-20261117.md §12）：
本仓日志形态 = 「stdlib 直用 + 装配统一」—— 每模块 `logging.getLogger(__name__)`
领零配置空壳，全部配置（格式/级别/流向/trace 字段）100% 收拢在 root 上的一次
`configure_logging`（`foundation/observability/logging_setup.py`）。这套统一依赖两条
不变量，任何一条被破坏，该模块的日志就会脱离中央管理：

  ① **装配泄漏 = 0**：`basicConfig` / `dictConfig` / `addHandler` / `removeHandler` /
     `setLevel` / `Formatter` / `StreamHandler` / `FileHandler` / `RotatingFileHandler`
     只许出现在 `foundation/observability/`（装配层的本职）。
     —— 有人在角落里自己挂 handler，就是装配散点的开端（2026-11-02 之前本仓正是
        4 处 basicConfig 各自为政，才有了统一入口设计）。

  ② **root 误用 = 0**：无参 `getLogger()` 返回 root logger，在它上面任何读写都是
     对全局配置的越权改动 —— 只许装配层使用。

**R-019**：门禁的价值 = 注入违规后会不会红。`--selftest` 在临时目录注入两类违规，
必须被准确报出（不碰 src/）。

用法：
    python check_logging_assembly.py             # 检查（rc 0/1）
    python check_logging_assembly.py --selftest  # 注入违规自检
"""
from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "agentnodes"

#: 豁免前缀（相对 src/agentnodes 的 posix 路径）—— 装配层本体
EXEMPT_PREFIXES = ("foundation/observability/",)

#: ①装配泄漏：这些调用只许在豁免区出现
ASSEMBLY_CALLS = {
    "basicConfig",
    "dictConfig",
    "addHandler",
    "removeHandler",
    "setLevel",
    "Formatter",
    "StreamHandler",
    "FileHandler",
    "RotatingFileHandler",
}


def _iter_py(root: Path):
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        yield p


def find_violations(src_root: Path) -> list[tuple[str, int, str]]:
    """返回 [(相对路径, 行号, 违规类型), ...]。

    兼容两种调用形态：``logging.basicConfig(...)``（Attribute）与
    ``from logging import basicConfig; basicConfig(...)``（Name）——
    后者是首批注入自检抓出的盲区。
    """
    violations: list[tuple[str, int, str]] = []
    for p in _iter_py(src_root):
        rel = p.relative_to(src_root).as_posix()
        if rel.startswith(EXEMPT_PREFIXES):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                attr = func.attr
                if attr in ASSEMBLY_CALLS:
                    violations.append((rel, node.lineno, f"assembly-leak: .{attr}()"))
                elif attr == "getLogger" and not node.args:
                    violations.append((rel, node.lineno, "root-misuse: getLogger() 无参"))
            elif isinstance(func, ast.Name) and func.id in ASSEMBLY_CALLS:
                violations.append((rel, node.lineno, f"assembly-leak: {func.id}()"))
    return violations


def selftest() -> int:
    """R-019：注入两类违规必须被抓到；合规文件必须不报（在临时目录做，不碰 src/）。"""
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "pkg" / "bad.py"
        bad.parent.mkdir()
        bad.write_text(
            "import logging\n"
            "logging.basicConfig(level=1)\n"
            "ROOT = logging.getLogger()\n"
            "h = logging.StreamHandler()\n"
            "ROOT.addHandler(h)\n"
            "from logging import FileHandler\n"
            "FileHandler('x.log')\n",
            encoding="utf-8",
        )
        clean = Path(td) / "pkg" / "clean.py"
        clean.write_text(
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "logger.info('fine')\n",
            encoding="utf-8",
        )
        hits = find_violations(Path(td))
        kinds = {v[2].split(":")[0] for v in hits}
        bad_lines = {v[1] for v in hits if v[0] == "pkg/bad.py"}
        clean_hits = [v for v in hits if v[0] == "pkg/clean.py"]
        if "assembly-leak" not in kinds or "root-misuse" not in kinds:
            print(f"[X] selftest 失败：两类违规未全部报出，实得 {hits}")
            return 1
        if clean_hits:
            print(f"[X] selftest 失败：合规文件被误报 {clean_hits}")
            return 1
        # 2 basicConfig · 3 getLogger() · 5 addHandler · 7 from-import 裸名 FileHandler
        if not {2, 3, 5, 7} <= bad_lines:
            print(f"[X] selftest 失败：违规行号不全，实得 {sorted(bad_lines)}")
            return 1
    print("[OK] selftest：Attribute/Name 两形态违规全报出（2/3/5/7 行），合规文件零误报")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()

    violations = find_violations(SRC)
    if violations:
        print(f"[X] 日志装配纪律违规 {len(violations)} 处 —— 装配只许在 foundation/observability/：")
        for rel, ln, kind in violations:
            print(f"    {rel}:{ln}  {kind}")
        print("    模块 logger 必须是零配置空壳（getLogger(__name__) + 打日志）；")
        print("    配置一律走 configure_logging（foundation/observability/logging_setup.py）。")
        return 1
    print("[OK] 日志装配纪律：装配泄漏 0 / root 误用 0（豁免 foundation/observability/）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
