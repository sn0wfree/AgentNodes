"""Layer-matrix gate — the four-layer skeleton's machine check.

契约：docs/layered-skeleton-design-20261103.md §2/§4。
层名（2026-11-03 拍板，llmwikify 式）：L1 interfaces / L2 apps / L3 kernel /
L4 foundation —— 物理目录不动，只有逻辑层名。

Rules:
1. Layer membership table (§2) is the single source of truth.
2. Imports may only flow downward (L1 interfaces → L2 apps → L3 kernel
   → L4 foundation); same-layer imports are free.
3. Relative imports resolve per PEP 328: anchor = the importer's package,
   strip ``level - 1`` components (an off-by-one here fabricated both
   false violations and false "unknowns" in the v1 audit).
4. Ratchet: per-cluster upward-violation baselines may only go DOWN.
   Baseline = ADR-000 物理口径（2026-11-03，每语句计 1）：55 = 1+2+46+2+4；
   G2a（簇 B/C，9 语句）与 G2b（CapabilityRegistry 端口化，46 语句）落地时
   各自下调所在簇基线。
"""

from __future__ import annotations

import ast
import os
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "strategy_research"
PKG = "strategy_research"

ORDER = ["L1", "L2", "L3", "L4"]  # index 0 = highest

# ── layer membership (design doc §2, longest-prefix match) ──────────

LAYER_MEMBERSHIP: dict[str, str] = {
    # L1 interfaces
    "strategy_research.interfaces": "L1",  # 物理层目录（逐模块迁移中）
    "strategy_research.api": "L1",
    "strategy_research.cli": "L1",
    "strategy_research.interfaces.mcp": "L1",
    # L2 apps (research IP)
    "strategy_research.apps": "L2",  # 物理层目录（逐模块迁移中，见 docs/physical-layer-migration-plan-20261103.md）
    "strategy_research.apps.alpha_zoo_yaml": "L2",
    "strategy_research.core.study": "L2",
    "strategy_research.core.goal": "L2",
    "strategy_research.core.hypothesis": "L2",
    "strategy_research.apps.alpha_zoo": "L2",
    "strategy_research.apps.alpha_zoo_adapter": "L2",
    "strategy_research.apps.alpha_zoo_convert": "L2",
    "strategy_research.apps.alpha_zoo_ops": "L2",
    "strategy_research.core.backtest": "L2",
    "strategy_research.apps.backtest_engine": "L2",
    "strategy_research.apps.backtest_models": "L2",
    "strategy_research.core.validation": "L2",
    "strategy_research.apps.portfolio": "L2",
    "strategy_research.core.data_source": "L2",
    "strategy_research.apps.data_import": "L2",
    "strategy_research.apps.data_readiness": "L2",
    "strategy_research.core.db": "L2",
    "strategy_research.apps.compute_factor": "L2",
    "strategy_research.apps.factor_validate": "L2",
    "strategy_research.apps.config_runner": "L2",
    "strategy_research.core.strategy_acceptance": "L2",
    "strategy_research.core.autoresearch": "L2",
    "strategy_research.core.engine": "L2",
    "strategy_research.apps.export": "L2",
    "strategy_research.apps.run_card": "L2",
    "strategy_research.core.workspace_setup": "L2",
    "strategy_research.apps.preflight": "L2",
    "strategy_research.apps.tools": "L2",  # v7.2: 研究数据工具实现（data_clean/transforms），仅被 apps 侧引用
    "strategy_research.templates": "L2",
    # L3 kernel (platform mechanisms) — three sub-bands (v7.2, presentation only;
    # import rules unchanged: same-layer free, downward only)
    #   控制面 (~4.1k): gateway / orchestration / events / session / scheduled_research
    #   执行面 (~6.0k): runtimes / workflow / swarm
    #   能力面 (~24.6k): agent / context / hooks / permission / memory / skills
    "strategy_research.core.gateway": "L3",
    "strategy_research.core.orchestration": "L3",
    "strategy_research.core.events": "L3",
    "strategy_research.core.session": "L3",
    "strategy_research.core.scheduled_research": "L3",
    "strategy_research.core.runtimes": "L3",
    "strategy_research.core.workflow": "L3",
    "strategy_research.core.swarm": "L3",
    "strategy_research.core.agent": "L3",
    "strategy_research.core.context": "L3",
    "strategy_research.kernel.hooks": "L3",
    "strategy_research.kernel.permission": "L3",
    "strategy_research.core.memory": "L3",
    "strategy_research.kernel.skills": "L3",
    "strategy_research.core.seq_generator": "L3",  # 待退役（归 event_log 权威 seq）
    "strategy_research.kernel": "L3",  # 物理层目录（逐模块迁移中）
    "strategy_research.kernel.seq_generator": "L3",
    # L4 foundation
    "strategy_research.core.llm": "L4",
    "strategy_research.core.storage": "L4",
    "strategy_research.core.observability": "L4",
    "strategy_research.core.utils": "L4",
    "strategy_research.foundation.web": "L4",
    "strategy_research.foundation.registry": "L4",  # v7.2: 通用 typed Registry 基类（零语义原语）
    "strategy_research.foundation.config_loader": "L4",
    "strategy_research.foundation": "L4",  # W0 试点：层目录 __init__
    "strategy_research.foundation.errors": "L4",
    "strategy_research.foundation.git": "L4",
}

# Facade __init__ modules excluded from direction assertions (re-exports only)
SKIP_MODULES = {"strategy_research", "strategy_research.core"}

# ── ratchet baselines (ADR-000 物理口径 2026-11-03；只降不升) ────────

# 口径：每条 import 语句计 1（同节点多 alias 指向同层不重复）。
# 2026-11-03 W3a 复算：_resolve_targets 修正后（绝对导入不再按 alias 展开
# 重复计数），实测远低于 2026-11-03 首版基线（那版被 alias 展开虚增）：
#   L3>L1 2→1 · L3>L2 46→35 · L4>L2 2→0 · L4>L3 4→2
# 按“只降不升”同步下调，避免基线长期虚高掩盖真实回退。
# G2a pending：event_store→api.projector、memory_manager→api.web_session_db、
#   study/scheduler→api.routers.web_session、platform_store→orchestration.contracts、
#   llm/config→agent.compact、llm/parser→agent.structured_output 等
# G2b pending：L3→L2 = 35 条语句（builtin_tools 为主体 → CapabilityRegistry 端口化）
BASELINE: dict[str, int] = {
    "L2>L1": 1,   # G2a
    "L3>L1": 1,   # G2a
    "L3>L2": 35,  # G2b（工具层绑定领域能力）
    "L4>L2": 0,   # G2a（已清零）
    "L4>L3": 2,   # G2a
}


def _layer_of(module: str) -> str | None:
    best, best_len = None, -1
    for prefix, layer in LAYER_MEMBERSHIP.items():
        if module == prefix or module.startswith(prefix + "."):
            if len(prefix) > best_len:
                best, best_len = layer, len(prefix)
    return best


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC).as_posix()[:-3]
    parts = [PKG] + [p for p in rel.split("/") if p != "__init__"]
    return ".".join(parts)


def _resolve_targets(node: ast.AST, importer_mod: str, is_pkg: bool) -> list[str]:
    """Absolute module names referenced by an Import/ImportFrom node.

    ADR-000（2026-11-03）：物理口径——每条 import **语句**计 1 次。

    对 ``from X import a, b`` 只产出模块 ``X``（一个元素），不做
    ``X.a`` / ``X.b`` 展开：展开会让**绝对导入**被重复计数，而相对导入
    因为 target 相同被 set 去重只计 1 —— 两种形态计法不一致，会把
    L3>L2 从 46 虚增到 47（2026-11-03 W3a 发现）。符号级信息由
    graphify 图负责，本门禁只做层方向判定。
    """
    if isinstance(node, ast.Import):
        return sorted({a.name for a in node.names})
    if not isinstance(node, ast.ImportFrom):
        return []
    base = importer_mod.split(".")
    pkg = base if is_pkg else base[:-1]
    # PEP 328: strip level-1 components (NOT level)
    if node.level > 1:
        pkg = pkg[: len(pkg) - (node.level - 1)]
    if not node.module and not node.level:
        return []
    target = ".".join(pkg + ([node.module] if node.module else []))
    # `from . import a, b` (module is None): each name is its own module.
    if not node.module:
        return sorted({f"{target}.{a.name}" for a in node.names})
    return [target]


def _collect() -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    """Walk the tree once: returns (violations per cluster, cross-layer edges)."""
    viol: dict[str, int] = defaultdict(int)
    edges: dict[tuple[str, str], int] = defaultdict(int)
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "scripts", "graphify-out")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = Path(dirpath) / fn
            mod = _module_name(path)
            if mod in SKIP_MODULES:
                continue
            src_layer = _layer_of(mod)
            is_pkg = fn == "__init__.py"
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                for target in _resolve_targets(node, mod, is_pkg):
                    tgt_layer = _layer_of(target)
                    if tgt_layer is None:
                        parts = target.split(".")
                        while parts:
                            parts.pop()
                            tgt_layer = _layer_of(".".join(parts))
                            if tgt_layer is not None:
                                break
                    if tgt_layer is None or src_layer is None:
                        continue
                    edges[(src_layer, tgt_layer)] += 1
                    if ORDER.index(tgt_layer) < ORDER.index(src_layer):
                        viol[f"{src_layer}>{tgt_layer}"] += 1
    return viol, edges


def _fmt_edges(edges: dict[tuple[str, str], int]) -> str:
    lines = ["      L1    L2    L3    L4"]
    for src in ORDER:
        row = [f"  {src} "]
        for dst in ORDER:
            row.append(f"{edges.get((src, dst), 0):>5} ")
        lines.append(" ".join(row))
    return "\n".join(lines)


class TestLayerMatrix:
    def test_all_modules_have_a_layer(self):
        """Every scanned module must resolve in the membership table
        (except the two facade __init__ modules)."""
        unknown: list[str] = []
        for dirpath, dirnames, filenames in os.walk(SRC):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "scripts", "graphify-out")]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                path = Path(dirpath) / fn
                mod = _module_name(path)
                if mod in SKIP_MODULES:
                    continue
                if _layer_of(mod) is None:
                    unknown.append(mod)
        assert not unknown, (
            "成员表缺层（请在 LAYER_MEMBERSHIP 登记并注明归层理由）:\n"
            + "\n".join(sorted(unknown))
        )

    def test_no_upward_imports(self):
        """The ratchet: per-cluster upward-violation counts must stay at or
        below BASELINE. Fix violations and lower the baseline together."""
        viol, edges = _collect()
        print("\n== cross-layer import matrix ==")
        print(_fmt_edges(edges))
        print("== upward violations per cluster ==")
        for cluster in sorted(set(BASELINE) | set(viol)):
            print(f"  {cluster}: {viol.get(cluster, 0)} (baseline {BASELINE.get(cluster, 0)})")
        over = {
            c: (viol.get(c, 0), BASELINE.get(c, 0))
            for c in viol
            if viol.get(c, 0) > BASELINE.get(c, 0)
        }
        assert not over, (
            "上行 import 超基线（方向必须向下：L1→L2→L3→L4；修复后请同步下调基线）:\n"
            + "\n".join(f"  {c}: {n} > baseline {b}" for c, (n, b) in over.items())
            + "\n设计契约：docs/layered-skeleton-design-20261103.md"
        )
