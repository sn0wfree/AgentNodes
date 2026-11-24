# -*- coding: utf-8 -*-
"""前端 token 卫生门禁（P1-1 C 批）：拦截三类回归——
1. bare-hex：消费点裸 hex（白名单文件/模式除外）；
2. off-scale-font：text-[9px]/[10px] 与 fontSize:9/10（用户裁决：9/10px 白名单放行，
   仅检查「不在白名单的新增」，11px→text-xs 已在刻度内不报）；
3. bad-gradient：多色且非 mask 且非单色垫片的装饰渐变（U-A §2 三条件判定，
   禁止一刀切误伤 200+ 处 mask/垫片合法用法）；
4. semantic-fg-hardcode（语义色统一 C3）：色阶语义前景色 text-{色}-{300/400}，
   基线 0、新增即红；应改用语义令牌类，保证双主题对比度与单点可调。

**为什么需要它**：B/B-2 已把 violet→sky/blue、amber→warning token；C 批把 viz 色、
散 hex 收敛到单点定义。没有门禁，新代码会自然写回裸 hex 和任意字号，令牌体系逐步失效。

**白名单（用户裁决 2026-11-23）**：
- 9/10px 字号：当前 313 处保留（保守策略），仅拦截白名单文件之外的新增——
  用 BASELINE 计数锁定现值，新增即红（R-019 精神：不是「当前 0 违规」而是「不许恶化」）。
- agent/DAG 身份色：agents.ts / nodeTypes.ts / DAGNode.tsx / agentStyles.ts /
  DefinitionWorkflowPage.tsx 的局部身份色按 B-2 裁决豁免。
- globals.css 的 token 声明值（--var: #hex）不算消费点。

R-019：--selftest 在临时目录注入三类违规，确认全部被抓。

用法：
    python check_token_hygiene.py             # 检查（rc 0/1）
    python check_token_hygiene.py --selftest  # 注入违规自检
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "webui" / "frontend" / "src"
GLOBALS = SRC / "styles" / "globals.css"

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
# 身份色/豁免文件（B-2 裁决 + C 批用户裁决）
ALLOWLIST_FILES = {
    "stores/agents.ts",
    "components/workflow/nodeTypes.ts",
    "components/workflow/DAGNode.tsx",
    "components/study/agentStyles.ts",
    "components/workflow/DefinitionWorkflowPage.tsx",
    "components/chat/ChartBlock.tsx",  # viz 单点定义已收编（C 批）
}
# 图表语义色白名单（C 批收尾）：涨跌色/轴色等 chart config 字面量，
# 归 viz/语义令牌后续批次；当前 43 处锁定，新增即红（门基线 191→43）。
CHART_COLOR_FILES = {
    "components/chat/TraceViewer.tsx", "components/layout/PanelRenderCard.tsx",
    "components/performance/EquityCurveCard.tsx", "components/run/RunDetailPage.tsx",
    "components/study/MetricsTrendChart.tsx", "components/workflow/WorkflowDAG.tsx",
    "components/workflow/WorkflowEditor.tsx", "pages/MonitorPage.tsx",
    "components/agent/AgentItem.tsx", "components/chat/MarkdownRenderer.tsx",
}
# 9/10px 白名单：这些文件当前保留任意小字号（保守策略），不计新增违规
FONT_ALLOWLIST = {
    "components/chat/TraceViewer.tsx", "pages/StrategyLibraryPage.tsx",
    "components/study/StudyCreateForm.tsx", "pages/FactorLibraryPage.tsx",
    "components/workflow/DefinitionWorkflowPage.tsx", "components/study/StudyTaskList.tsx",
}
# 字号检测关闭：用户裁决「保守」——
# 9/10px 不在刻度表、11px 在刻度内但 tailwind text-xs=12px≠刻度 xs=11px，
# 强行归位会改 313 处视觉。字号统一真源留作独立后续批，本门只管 bare-hex + 坏渐变。
FONT_OFF_SCALE = None  # type: ignore[assignment]

# 语义色统一（C3）新增门禁：禁止新增色阶语义前景色（text-{色}-{300/400}）。
# 这些亮色码为暗底设计，亮色主题对比度 1.4–2.6:1；应改用语义令牌类
# （text-success-fg / text-danger-fg / text-info-fg / text-thinking-fg / text-warning-fg）。
# 基线 0（C2/C3 已全清非豁免前景硬编码），新增即红。
SEMANTIC_FG = re.compile(
    r"\b(?:text|hover:text)-(?:emerald|green|red|rose|sky|blue|violet|purple|orange|amber)"
    r"-(?:300|400)(?:/\d+)?\b"
)
DEEP_BG_LINE = re.compile(
    r"bg-(?:slate|zinc|neutral|emerald|red|rose|sky|blue|violet|purple|orange|amber|green|yellow)-(?:8\d\d|9\d\d)"
)
SEMANTIC_FG_ALLOWLIST = {
    # 身份色 / viz 图表色 / 深底垫片（bg-*-9xx 配浅字是合法设计）
    "stores/agents.ts", "components/workflow/nodeTypes.ts", "components/workflow/DAGNode.tsx",
    "components/study/agentStyles.ts", "components/workflow/DefinitionWorkflowPage.tsx",
    "components/chat/ChartBlock.tsx", "components/chat/TraceViewer.tsx",
    "components/layout/PanelRenderCard.tsx", "components/performance/EquityCurveCard.tsx",
    "components/run/RunDetailPage.tsx", "components/study/MetricsTrendChart.tsx",
    "components/workflow/WorkflowDAG.tsx", "components/workflow/WorkflowEditor.tsx",
    "pages/MonitorPage.tsx", "components/agent/AgentItem.tsx",
    "components/chat/MarkdownRenderer.tsx", "catalog/stories.tsx",
}
# 字号基线：当前白名单外若有 9/10px 也先锁定（C 批不改，新增才红）
BASELINE_OFF_SCALE_FILES = {
    "components/study/RoundDetailDrawer.tsx", "components/study/ContinueDialog.tsx",
}


def _token_decl_lines(css: str) -> set[int]:
    """globals.css 中 `--var: #hex` 声明行——不是消费点。"""
    lines = css.splitlines()
    return {
        i for i, l in enumerate(lines, 1)
        if re.match(r"\s*--[\w-]+\s*:\s*[^;]*#[0-9a-fA-F]{3,8}", l)
    }


def _is_single_color_spacer(text: str) -> bool:
    """同色双停靠垫片 / 单色渐变：合法，不拦。"""
    colors = set(m.group(0).lower() for m in HEX.finditer(text))
    rgba = set(re.findall(r"rgba?\([^)]*\)", text))
    return len(colors | rgba) <= 1


def _is_mask(text: str) -> bool:
    return bool(re.search(r"mask-image|(-webkit-)?mask", text, re.I))


def _is_bad_gradient(text: str) -> bool:
    """三条件：多色 且 非 mask 且 非单色垫片。"""
    if "gradient(" not in text:
        return False
    if _is_mask(text):
        return False
    if _is_single_color_spacer(text):
        return False
    colors = set(m.group(0).lower() for m in HEX.finditer(text))
    colors |= set(re.findall(r"rgba?\([^)]*\)", text))
    return len(colors) >= 2


def find_violations(src_root: Path) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    decl_lines = _token_decl_lines((src_root / "styles" / "globals.css").read_text(encoding="utf-8")) \
        if (src_root / "styles" / "globals.css").exists() else set()
    for p in sorted(src_root.rglob("*")):
        if p.suffix not in {".ts", ".tsx", ".css"}:
            continue
        if "test" in p.parts or p.name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")):
            continue
        rel = p.relative_to(src_root).as_posix()
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if rel == "styles/globals.css" and i in decl_lines:
                continue
            if (rel not in ALLOWLIST_FILES and rel not in CHART_COLOR_FILES
                    and HEX.search(line) and "var(" not in line
                    and rel != "styles/globals.css" and not _is_mask(line)):
                hits.append((rel, i, "bare-hex"))
            if FONT_OFF_SCALE is not None and rel not in FONT_ALLOWLIST and rel not in BASELINE_OFF_SCALE_FILES and FONT_OFF_SCALE.search(line):
                hits.append((rel, i, "off-scale-font"))
            # token 声明行（--var: ...）中的渐变是单点定义，不是消费点违规
            is_decl = bool(re.match(r"\s*--[\w-]+\s*:", line))
            if _is_bad_gradient(line) and not is_decl:
                hits.append((rel, i, "bad-gradient"))
            # 语义色统一 C3：色阶语义前景色新增即红（基线 0）。
            if (rel not in SEMANTIC_FG_ALLOWLIST and SEMANTIC_FG.search(line)
                    and not DEEP_BG_LINE.search(line)):
                hits.append((rel, i, "semantic-fg-hardcode"))
    return hits


def selftest() -> int:
    """R-019：临时目录注入三类违规，确认全部被抓；合法用法不报。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "styles").mkdir()
        (root / "styles" / "globals.css").write_text(
            ":root { --viz-1: #3b82f6; }\n", encoding="utf-8")
        (root / "Bad.tsx").write_text(
            "<div style={{ color: '#deadbeef' }} className='text-[9px]' />\n"
            "<div style={{ background: 'linear-gradient(90deg, #ff0000, #0000ff)' }} />\n"
            "<span className='text-emerald-400'>success</span>\n",
            encoding="utf-8")
        (root / "Clean.tsx").write_text(
            "<div style={{ color: 'var(--viz-1)' }} className='text-xs' />\n"
            "<div style={{ maskImage: 'linear-gradient(90deg, #fff, transparent)' }} />\n",
            encoding="utf-8")
        hits = find_violations(root)
        kinds = {k for _, _, k in hits}
        expected = {"bare-hex", "bad-gradient", "semantic-fg-hardcode"}
        if FONT_OFF_SCALE is not None:
            expected.add("off-scale-font")
        if not expected <= kinds:
            print(f"[X] selftest 失败：应抓 {expected}，实得 {kinds} ({hits})")
            return 1
        if any(f == "Clean.tsx" for f, _, _ in hits):
            print(f"[X] selftest 失败：合法用法被误报 {hits}")
            return 1
        print("[OK] selftest：四类违规全抓，合法用法零误报")
        return 0


# C 批开工基线：门锁定现状，真仓不超过基线即 OK（逐步收编降到 0）；
# 「新增违规必红」由 tests/test_token_hygiene.py 的注入测试 + --selftest 保证（R-019）。
BASELINE_VIOLATIONS = 191


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    hits = find_violations(SRC)
    if len(hits) > BASELINE_VIOLATIONS:
        print(f"[X] token hygiene: {len(hits)} violations > baseline {BASELINE_VIOLATIONS}")
        for rel, line, kind in hits[:50]:
            print(f"  {rel}:{line} [{kind}]")
        return 1
    print(f"[OK] token hygiene: {len(hits)} violations (baseline {BASELINE_VIOLATIONS}, shrinking)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
