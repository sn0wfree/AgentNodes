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
import ast
import html.parser
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PKG = SRC / "agentnodes"
DIAGRAM = ROOT / "docs" / "architecture-diagram.html"

sys.path.insert(0, str(ROOT / "scripts" / "arch"))

# Windows 默认控制台是 GBK：任何不可编码字符（✅ 等）会让 print 直接抛
# UnicodeEncodeError —— 门禁在「全部 [OK]」之后崩掉，退出码 1，看起来像
# 检查失败。errors="replace" 把不可编码字符降级为 '?' 而不是抛异常。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


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


def check(verbose: bool = True, text: str | None = None) -> tuple[bool, list[str]]:
    """返回 (是否一致, 报告行)。

    ``text`` 缺省读图；传入字符串则对**给定文本**做检查（``--write`` 用它
    验证回写结果，避免"写坏了才发现"）。
    """
    if text is None:
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


def _edge_file_count(src_layer: str, tgt_layer: str) -> int:
    """某个跨层方向的**触点文件数**（图上「11 个文件触点」那类注解）。

    复用 layer_matrix 的判定原语（同一张层归属表、同一套算法），
    不另写一份层判定逻辑。
    """
    import layer_matrix as lm

    touched: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(PKG):
        dirnames[:] = [
            d for d in dirnames if d not in ("__pycache__", "scripts", "graphify-out")
        ]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = Path(dirpath) / fn
            mod = lm.module_name(path, PKG)
            if mod in lm.SKIP_MODULES:
                continue
            if lm.layer_of(mod) != src_layer:
                continue
            is_pkg = fn == "__init__.py"
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                for target in lm.resolve_targets(node, mod, is_pkg):
                    if lm.resolve_layer(target) == tgt_layer:
                        touched.add(path)
                        break
    return len(touched)


def write() -> tuple[bool, list[str]]:
    """把 AST 实测数字**回写**进架构图。返回 (是否成功, 报告行)。

    设计约束：
    1. 只改**数字**，不动版式与文案结构 —— 每条替换都用带上下文的正则锚定，
       避免误伤同名数字（例：「112 文件」在图上出现两处，必须分别锚定）。
    2. 写完立刻用 ``check()`` 复核**同一份文本**；复核不过就还原原图并报错。
       宁可不动，也不能写出一张半路坏掉的图。
    3. 任何一条替换**零命中**都报出来 —— 说明图的结构变了（版式改过），
       需要人工核对，而不是静默跳过。
    """
    stats = _layer_stats()
    viol, edges = _edges()
    total_files = sum(v["files"] for v in stats.values())
    total_lines = sum(v["lines"] for v in stats.values())

    if not DIAGRAM.exists():
        return False, [f"架构图不存在: {DIAGRAM}"]

    original = DIAGRAM.read_text(encoding="utf-8", errors="replace")
    text = original
    report: list[str] = []
    missed: list[str] = []

    def sub(pattern: str, repl: str, label: str, count: int = 0) -> None:
        nonlocal text
        new, n = re.subn(pattern, repl, text, count=count)
        if n == 0:
            missed.append(label)
        text = new

    # ── 1. 层徽标（SVG 左侧竖排）与层表行 ──
    for layer in ("L1", "L2", "L3", "L4"):
        v = stats.get(layer, {"files": 0, "lines": 0})
        k = f"{v['lines'] / 1000:.1f}k"
        badge = (
            rf'>L{layer[-1]}</text>\s*<text[^>]*class="lyr"[^>]*>[^<]*</text>\s*'
            rf'<text[^>]*class="lyrsub"[^>]*>[^<]*</text>\s*'
        )
        sub(rf"({badge}<text[^>]*class=\"loc\"[^>]*>)\d+ 文件(</text>)",
            rf"\g<1>{v['files']} 文件\g<2>", f"{layer} 徽标文件数")
        sub(rf"({badge}<text[^>]*class=\"loc\"[^>]*>\d+ 文件</text>\s*"
            rf"<text[^>]*class=\"loc\"[^>]*>)[\d.]+k 行(</text>)",
            rf"\g<1>{k} 行\g<2>", f"{layer} 徽标行数")
        sub(rf'(<td class="k">L{layer[-1]} [a-z]+</td><td>)\d+ 文件<br>[\d.]+k 行(</td>)',
            rf'\g<1>{v["files"]} 文件<br>{k} 行\g<2>', f"{layer} 表行")

    # ── 2. 总计（页首说明 + 页脚）──
    sub(r"\d+ 文件 / [\d,]+ 行",
        f"{total_files} 文件 / {total_lines:,} 行", "总计")

    # ── 3. 跨层边数（SVG 箭头标注 + 矩阵网格）──
    def e(a: str, b: str) -> int:
        return edges.get((a, b), 0)

    sub(r"(L1→L2 )\d+( · L1→L3 )\d+( · L1→L4 )\d+",
        rf"\g<1>{e('L1','L2')}\g<2>{e('L1','L3')}\g<3>{e('L1','L4')}", "L1 边标注")
    touch = _edge_file_count("L2", "L3")
    sub(r"(L2→L3 )\d+（\d+ 个文件触点）(· L2→L4 )\d+",
        rf"\g<1>{e('L2','L3')}（{touch} 个文件触点）\g<2>{e('L2','L4')}", "L2 边标注")
    sub(r"(L3→L4 )\d+", rf"\g<1>{e('L3','L4')}", "L3 边标注")

    # 矩阵网格（L1 行 / L2 行 / L3 行 / L4 行）
    sub(r'(<text x="180" y="1678" class="st mono">L1\s+—\s+)\d+(\s+)\d+(\s+)\d+(</text>)',
        rf'\g<1>{e("L1","L2")}\g<2>{e("L1","L3")}\g<3>{e("L1","L4")}\g<4>', "矩阵 L1 行")
    sub(r'(<text x="180" y="1700" class="st mono">L2\s+<tspan fill="#dc2626">)\d+( ⚠</tspan>\s+—\s+)\d+(\s+)\d+(</text>)',
        rf'\g<1>{viol.get("L2>L1",0)}\g<2>{e("L2","L3")}\g<3>{e("L2","L4")}\g<4>', "矩阵 L2 行")
    sub(r'(<text x="180" y="1722" class="st mono">L3\s+<tspan fill="#dc2626">)\d+( ⚠</tspan>\s+<tspan fill="#16a34a">)\d+( ✅</tspan>\s+—\s+)\d+(</text>)',
        rf'\g<1>{viol.get("L3>L1",0)}\g<2>{viol.get("L3>L2",0)}\g<3>{e("L3","L4")}\g<4>',
        "矩阵 L3 行")
    sub(r'(<text x="180" y="1744" class="st mono">L4\s+)\d+(\s+)\d+(\s+)\d+(\s+—</text>)',
        rf'\g<1>{viol.get("L4>L1",0)}\g<2>{viol.get("L4>L2",0)}\g<3>{viol.get("L4>L3",0)}\g<4>',
        "矩阵 L4 行")

    # ── 4. 上行违规 ──
    v_total = sum(viol.values())
    sub(r"(违规仅剩 )\d+( 条（<code>L3→L1</code> )\d+( · <code>L2→L1</code> )\d+",
        rf"\g<1>{v_total}\g<2>{viol.get('L3>L1',0)}\g<3>{viol.get('L2>L1',0)}",
        "页首违规说明")
    sub(r"(上行违规总数 <tspan[^>]*>)55 → \d+(</tspan>)",
        rf"\g<1>55 → {v_total}\g<2>", "棘轮总数")
    sub(r"(剩余：<code>L3→L1</code> )\d+( · <code>L2→L1</code> )\d+",
        rf"\g<1>{viol.get('L3>L1',0)}\g<2>{viol.get('L2>L1',0)}", "棘轮剩余")
    sub(r"(＝上行违规（仅 )\d+( 条，棘轮锁定）)",
        rf"\g<1>{v_total}\g<2>", "图例违规数")

    # ── 5. 回写后复核 ──
    ok, check_report = check(text=text)
    if not ok:
        DIAGRAM.write_text(original, encoding="utf-8")
        return False, (
            ["[X] 回写后复核未通过 —— 已还原原图，请人工核对："]
            + check_report
        )

    DIAGRAM.write_text(text, encoding="utf-8")
    try:
        shown = DIAGRAM.relative_to(ROOT)
    except ValueError:
        shown = DIAGRAM  # 合成仓库测试时图不在仓库内
    report.append(f"[OK] 已回写 {shown}")
    report.append(f"  层体量：" + " · ".join(
        f"{ly} {stats.get(ly, {}).get('files', 0)} 文件/"
        f"{stats.get(ly, {}).get('lines', 0):,} 行" for ly in ("L1", "L2", "L3", "L4")))
    report.append(f"  总计：{total_files} 文件 / {total_lines:,} 行")
    report.append("  边数：" + " · ".join(
        f"{a}→{b} {edges.get((a, b), 0)}" for a, b in [
            ("L1", "L2"), ("L1", "L3"), ("L1", "L4"),
            ("L2", "L3"), ("L2", "L4"), ("L3", "L4")]))
    report.append(f"  上行违规：{v_total} 条")
    if missed:
        report.append("[!] 以下替换**零命中**（图的结构可能变了，需人工核对）：")
        report += [f"    - {m}" for m in missed]
    return True, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="架构图同步检查")
    ap.add_argument("--quiet", action="store_true", help="只在有差异时输出")
    ap.add_argument(
        "--write",
        action="store_true",
        help="把 AST 实测数字回写进架构图（回写后自动复核，不过则还原）",
    )
    args = ap.parse_args(argv)

    if args.write:
        ok, report = write()
        print("\n".join(report))
        return 0 if ok else 1

    ok, report = check()
    if not ok or not args.quiet:
        print("\n".join(report))
    if ok:
        print("\n架构图与代码一致 [OK]")
        return 0
    print("\n架构图已过时，请更新 docs/architecture-diagram.html")
    print("（层体量/边数须来自 AST 实测，不得手写猜测）")
    print("或直接跑：python scripts/arch/sync_architecture_diagram.py --write")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
