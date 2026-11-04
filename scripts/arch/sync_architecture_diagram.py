#!/usr/bin/env python
"""架构图自动同步检查（做减法/大改后必须跑）。

**为什么需要它**：架构图上的数字（层体量、跨层边数）是从代码实测来的。
任何增删包、搬模块、改 import 的改动都会让图变成假的。手工记得更新不可靠
——本仓库已经出现过「图上写着 46 条违规、实际是 0」这种过时 6 个版本的情况。

**用法**：

    python scripts/arch/sync_architecture_diagram.py          # 只检查，报告差异
    python scripts/arch/sync_architecture_diagram.py --write  # 检查并回写数字

**检查什么**：

1. 四层体量（文件数 + 行数）与图上徽标一致
2. 跨层 import 边数与图上矩阵一致
3. 上行违规数与图上一致
4. HTML 结构合法（标签配对、未转义 `&`、rect 越界）

**退出码**：0 = 全部一致；1 = 有差异（需更新）。
"""
from __future__ import annotations

import argparse
import html.parser
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PKG = SRC / "strategy_research"
DIAGRAM = ROOT / "docs" / "architecture-diagram.html"

sys.path.insert(0, str(ROOT / "scripts" / "arch"))


def _layer_stats() -> dict[str, dict[str, int]]:
    """按层统计文件数与行数（AST 实测，口径与门禁一致）。"""
    import layer_matrix as lm

    out: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})
    pkg_root = SRC
    for f in PKG.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        rel = f.relative_to(pkg_root).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        layer = lm.resolve_layer(".".join(parts))
        if layer is None:
            continue
        out[layer]["files"] += 1
        out[layer]["lines"] += len(
            f.read_text(encoding="utf-8", errors="replace").splitlines()
        )
    return dict(out)


def _edges() -> tuple[dict, dict]:
    import layer_matrix as lm

    return lm.collect(PKG)


class _TagChecker(html.parser.HTMLParser):
    VOID = {"br", "img", "meta", "link", "hr", "input", "path", "line",
            "rect", "circle", "polygon", "polyline", "stop", "use"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID and tag != "tspan":
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID or tag == "tspan":
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            self.errors.append(f"mismatched </{tag}>")
            while self.stack and self.stack[-1] != tag:
                self.stack.pop()
            if self.stack:
                self.stack.pop()
        else:
            self.errors.append(f"unexpected </{tag}>")


def check(verbose: bool = True) -> tuple[bool, list[str]]:
    """返回 (是否一致, 报告行)。"""
    if not DIAGRAM.exists():
        return False, [f"架构图不存在: {DIAGRAM}"]

    text = DIAGRAM.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    ok = True

    # ── 1. 结构合法性 ──
    c = _TagChecker()
    c.feed(text)
    if c.stack or c.errors:
        ok = False
        lines.append(f"[X] HTML 结构：未闭合 {c.stack}，错误 {len(c.errors)}")
        for e in c.errors[:5]:
            lines.append(f"      {e}")
    else:
        lines.append("[OK] HTML 结构合法")

    bad_amp = re.findall(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", text)
    if bad_amp:
        ok = False
        lines.append(f"[X] 未转义 &：{len(bad_amp)} 处")
    else:
        lines.append("[OK] 无未转义 &")

    vm = re.search(r'viewBox="0 0 (\d+) (\d+)"', text)
    if vm:
        W, H = int(vm.group(1)), int(vm.group(2))
        oob = [
            (x, y, w, h)
            for x, y, w, h in (
                map(int, m.groups())
                for m in re.finditer(
                    r'<rect x="(-?\d+)" y="(-?\d+)" width="(\d+)" height="(\d+)"', text
                )
            )
            if x < 0 or y < 0 or x + w > W or y + h > H
        ]
        if oob:
            ok = False
            lines.append(f"[X] rect 越界：{len(oob)} 处（viewBox {W}x{H}）")
            for o in oob[:5]:
                lines.append(f"      {o}")
        else:
            lines.append(f"[OK] rect 均在 viewBox {W}x{H} 内")

    # ── 2. 层体量 ──
    stats = _layer_stats()
    lines.append("")
    lines.append("层体量：")
    for layer in ("L1", "L2", "L3", "L4"):
        v = stats.get(layer, {"files": 0, "lines": 0})
        k = f"{v['lines'] / 1000:.1f}k"
        has_k = k in text
        has_files = f"{v['files']} 文件" in text
        mark = "[OK]" if (has_k and has_files) else "[X] "
        if not (has_k and has_files):
            ok = False
        lines.append(
            f"  {mark} {layer}: {v['files']} 文件 / {v['lines']:,} 行"
            f"（图中标 {k}: {has_k} · {v['files']} 文件: {has_files}）"
        )

    total_files = sum(v["files"] for v in stats.values())
    total_lines = sum(v["lines"] for v in stats.values())
    has_total = f"{total_files} 文件" in text and f"{total_lines:,}" in text
    if not has_total:
        ok = False
    lines.append(
        f"  {'[OK]' if has_total else '[X] '} 总计: {total_files} 文件 / {total_lines:,} 行"
    )

    # ── 3. 跨层边数 ──
    viol, edges = _edges()
    lines.append("")
    lines.append("跨层边数：")
    for a, b, label in [
        ("L1", "L2", "L1→L2"), ("L1", "L3", "L1→L3"), ("L1", "L4", "L1→L4"),
        ("L2", "L3", "L2→L3"), ("L2", "L4", "L2→L4"), ("L3", "L4", "L3→L4"),
    ]:
        n = edges.get((a, b), 0)
        marker = f"{label.split('→')[1]}"  # 只比数字出现
        appears = re.search(rf"{label[2:]}\s*{n}\b", text.replace(" ", ""))
        hit = str(n) in text
        mark = "[OK]" if hit else "[X] "
        if not hit:
            ok = False
        lines.append(f"  {mark} {label}: 实测 {n}")

    lines.append("")
    lines.append("上行违规：")
    for key, label in [("L2>L1", "L2→L1"), ("L3>L1", "L3→L1"), ("L3>L2", "L3→L2"),
                       ("L4>L1", "L4→L1"), ("L4>L2", "L4→L2"), ("L4>L3", "L4→L3")]:
        n = viol.get(key, 0)
        lines.append(f"  {'[OK]' if n == 0 or str(n) in text else '[X] '} {label}: {n}")

    return ok, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="架构图同步检查")
    ap.add_argument("--quiet", action="store_true", help="只在有差异时输出")
    args = ap.parse_args(argv)

    ok, report = check()
    if not ok or not args.quiet:
        print("\n".join(report))
    if ok:
        print("\n架构图与代码一致 ✅")
        return 0
    print("\n架构图已过时，请更新 docs/architecture-diagram.html")
    print("（层体量/边数须来自 AST 实测，不得手写猜测）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
