# -*- coding: utf-8 -*-
"""框架文档体量数字核对（v1.2 起）。

**为什么需要它**：`docs/框架与理念 · v1.1.html` 里 4 处体量快照数字（L1 108 /
L2 15 / L3 189 / L4 59 文件）在 3 周内全部被现实推翻，而**没有任何脚本或 CI
校验它** —— 没人发现。本脚本把 v1.2 的体量数字钉在代码上：数字漂了就红。

职责边界：
  - 只核对**体量类**数字（层文件数 / 层行数 / 总计），因为这些是自动可算的
  - W/R 完成度、理念检查栏这类**语义**断言不在本脚本范围（它们需要人判断）
  - 不修改任何文档；只报告偏差（与 sync_architecture_diagram 的 --write 分工不同）

退出码：0 = 一致 / 1 = 有偏差（与 scripts/arch/ 其余门禁一致）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import layer_matrix as lm  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "design" / "framework-and-principles-v1.2.md"
HTML = ROOT / "docs" / "框架与理念 · v1.2.html"

#: 层 → 目录名
LAYERS: dict[str, str] = {
    "L1": "interfaces",
    "L2": "apps",
    "L3": "kernel",
    "L4": "foundation",
}


def _layer_totals() -> dict[str, dict[str, int]]:
    """按 layer_matrix 的判定原语算每层文件数/行数（与架构图同一算法）。

    口径必须与 ``sync_architecture_diagram._layer_stats`` 完全一致：从 ``src/``
    起算（含 ``agentnodes`` 包前缀），``__init__.py`` 归到其父包。
    """
    out: dict[str, dict[str, int]] = {k: {"files": 0, "lines": 0} for k in LAYERS}
    src = ROOT / "src"
    for path in src.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(src).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        layer = lm.resolve_layer(".".join(parts))
        if layer is None or layer not in out:
            continue
        out[layer]["files"] += 1
        try:
            out[layer]["lines"] += len(
                path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
        except OSError:
            pass
    return out


def _doc_rows(text: str) -> dict[str, tuple[int, int]] | None:
    """从文档的层体量表里抽出 (文件, 行数)。

    表形态（见 v1.2 §2.1）：
        | L1 | `interfaces/` | 118 | 27,337 | ... |
    """
    rows: dict[str, tuple[int, int]] = {}
    pat = re.compile(
        r"^\|\s*(L[1-4])\s*\|\s*`([a-z_]+)/`\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|",
        re.M,
    )
    for m in pat.finditer(text):
        layer = m.group(1)
        files = int(m.group(3).replace(",", ""))
        lines = int(m.group(4).replace(",", ""))
        rows[layer] = (files, lines)
    return rows or None


def _html_rows(text: str) -> dict[str, tuple[int, int]]:
    """从 HTML 的 ``data-fw`` 锚点里抽出 (文件, 行数)。

    HTML 侧用 ``<span class="st" data-fw="L1">118 文件 · 27,337 行</span>``
    这类锚点承载数字 —— 与 md 的表格是同一批数字的两种呈现，两边都要核。
    """
    rows: dict[str, tuple[int, int]] = {}
    pat = re.compile(
        r'data-fw="(L[1-4]|total)"[^>]*>([\d,]+)\s*文件\s*·\s*([\d,]+)\s*行',
    )
    for m in pat.finditer(text):
        rows[m.group(1)] = (
            int(m.group(2).replace(",", "")),
            int(m.group(3).replace(",", "")),
        )
    return rows


def _check_html(actual: dict[str, dict[str, int]], total_files: int,
                total_lines: int) -> list[str]:
    """核对 HTML 的 data-fw 锚点（若 HTML 存在）。"""
    if not HTML.exists():
        return []
    text = HTML.read_text(encoding="utf-8", errors="replace")
    rows = _html_rows(text)
    if not rows:
        return ["HTML 里找不到 data-fw 锚点（期望 `data-fw=\"L1\"` + `N 文件 · M 行`）"]

    problems: list[str] = []
    expect = {k: (v["files"], v["lines"]) for k, v in actual.items()}
    expect["total"] = (total_files, total_lines)
    print()
    print(f"{'锚点':6s} {'HTML':>20s} {'实测':>20s}")
    for key in ["L1", "L2", "L3", "L4", "total"]:
        got = rows.get(key)
        want = expect[key]
        if got is None:
            problems.append(f'HTML 缺 data-fw="{key}" 锚点')
            print(f"{key:6s} {'(缺失)':>20s} {want[0]:8d} 文件 {want[1]:7,d} 行   [BAD]")
            continue
        mark = "OK " if got == want else "BAD"
        print(f"{key:6s} {got[0]:8d} 文件 {got[1]:7,d} 行 "
              f"{want[0]:8d} 文件 {want[1]:7,d} 行   [{mark}]")
        if got != want:
            problems.append(
                f'HTML data-fw="{key}"：写 {got[0]} 文件 / {got[1]:,} 行，'
                f"实测 {want[0]} 文件 / {want[1]:,} 行"
            )
    return problems


def main() -> int:
    if not DOC.exists():
        print(f"[X] 找不到 {DOC.relative_to(ROOT)}")
        return 1

    text = DOC.read_text(encoding="utf-8", errors="replace")
    doc_rows = _doc_rows(text)
    if doc_rows is None:
        print("[X] 文档里找不到层体量表（期望形如 `| L1 | `interfaces/` | 118 | 27,337 |`）")
        return 1

    actual = _layer_totals()
    problems: list[str] = []
    total_files = total_lines = 0

    print(f"{'层':4s} {'目录':12s} {'文档':>18s} {'实测':>18s}")
    for layer, dirname in LAYERS.items():
        doc_files, doc_lines = doc_rows.get(layer, (-1, -1))
        act = actual[layer]
        total_files += act["files"]
        total_lines += act["lines"]
        mark = "OK " if (doc_files, doc_lines) == (act["files"], act["lines"]) else "BAD"
        print(
            f"{layer:4s} {dirname:12s} "
            f"{doc_files:8d} 文件 {doc_lines:7,d} 行 "
            f"{act['files']:8d} 文件 {act['lines']:7,d} 行   [{mark}]"
        )
        if (doc_files, doc_lines) != (act["files"], act["lines"]):
            problems.append(
                f"{layer} {dirname}/: 文档写 {doc_files} 文件 / {doc_lines:,} 行，"
                f"实测 {act['files']} 文件 / {act['lines']:,} 行"
            )

    # 总计行（文档写 `| — | **合计** | **418** | **91,340** |`）
    m = re.search(r"\|\s*—\s*\|\s*\*\*合计\*\*\s*\|\s*\*\*([\d,]+)\*\*\s*\|\s*\*\*([\d,]+)\*\*",
                  text)
    if m:
        doc_tf = int(m.group(1).replace(",", ""))
        doc_tl = int(m.group(2).replace(",", ""))
        mark = "OK " if (doc_tf, doc_tl) == (total_files, total_lines) else "BAD"
        print(f"{'—':4s} {'合计':12s} {doc_tf:8d} 文件 {doc_tl:7,d} 行 "
              f"{total_files:8d} 文件 {total_lines:7,d} 行   [{mark}]")
        if (doc_tf, doc_tl) != (total_files, total_lines):
            problems.append(
                f"合计：文档写 {doc_tf} 文件 / {doc_tl:,} 行，"
                f"实测 {total_files} 文件 / {total_lines:,} 行"
            )
    else:
        problems.append("缺少「合计」行（期望 `| — | **合计** | **N** | **M** |`）")

    problems += _check_html(actual, total_files, total_lines)

    print()
    if problems:
        print(f"[X] {len(problems)} 处体量数字与代码不一致：")
        for p in problems:
            print(f"    · {p}")
        print()
        print("→ 更新 docs/design/framework-and-principles-v1.2.md 与 "
              "docs/框架与理念 · v1.2.html 后重跑")
        return 1

    print("[OK] 框架文档（md + HTML）体量数字与代码一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
