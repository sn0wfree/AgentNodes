# -*- coding: utf-8 -*-
"""layer_matrix — 四层骨架的层归属与判定原语（**唯一事实源**）。

契约：``docs/layered-skeleton-design-20261103.md`` §2/§4。
层名（2026-11-03 拍板）：L1 interfaces / L2 apps / L3 kernel / L4 foundation
—— 物理目录不动，只有逻辑层名。导入只允许向下流动。

**为什么单独成模块**：两个门禁都要用同一份层归属表——
``tests/test_layer_matrix.py``（棘轮基线）与
``scripts/arch/check_dangling_imports.py --layers``（层方向输出）。
表若各存一份，两个门禁迟早漂移出不同的判定结果，届时"哪个数字是真的"
就无从判断。这里是唯一副本，两者都 import 它。

口径（ADR-000 物理口径，2026-11-03）：每条 import **语句**计 1 次，
同节点多 alias 指向同层不重复计数。
"""
from __future__ import annotations

import ast
import os
from collections import defaultdict
from pathlib import Path

PKG = "strategy_research"

ORDER = ["L1", "L2", "L3", "L4"]  # index 0 = highest

# ── layer membership (design doc §2, longest-prefix match) ──────────

LAYER_MEMBERSHIP: dict[str, str] = {
    # L1 interfaces
    "strategy_research.interfaces": "L1",  # 物理层目录（逐模块迁移中）
    "strategy_research.interfaces.api": "L1",
    "strategy_research.interfaces.cli": "L1",
    "strategy_research.interfaces.mcp": "L1",
    # L2 apps (research IP)
    "strategy_research.apps": "L2",  # 物理层目录（逐模块迁移中）
    "strategy_research.apps.alpha_zoo_yaml": "L2",
    "strategy_research.apps.study": "L2",
    "strategy_research.apps.goal": "L2",
    "strategy_research.apps.hypothesis": "L2",
    "strategy_research.apps.alpha_zoo": "L2",
    "strategy_research.apps.alpha_zoo_adapter": "L2",
    "strategy_research.apps.alpha_zoo_convert": "L2",
    "strategy_research.apps.alpha_zoo_ops": "L2",
    "strategy_research.apps.backtest": "L2",
    "strategy_research.apps.backtest_engine": "L2",
    "strategy_research.apps.backtest_models": "L2",
    "strategy_research.apps.validation": "L2",
    "strategy_research.apps.portfolio": "L2",
    "strategy_research.apps.data_source": "L2",
    "strategy_research.apps.data_import": "L2",
    "strategy_research.apps.data_readiness": "L2",
    "strategy_research.apps.db": "L2",
    "strategy_research.apps.compute_factor": "L2",
    "strategy_research.apps.factor_validate": "L2",
    "strategy_research.apps.config_runner": "L2",
    "strategy_research.apps.strategy_acceptance": "L2",
    "strategy_research.apps.autoresearch": "L2",
    "strategy_research.apps.engine": "L2",
    "strategy_research.apps.export": "L2",
    "strategy_research.apps.run_card": "L2",
    "strategy_research.apps.preflight": "L2",
    "strategy_research.apps.tools": "L2",
    "strategy_research.apps.capabilities": "L2",
    "strategy_research.apps.evolution": "L2",
    "strategy_research.templates": "L2",
    # L3 kernel (platform mechanisms) — three sub-bands (presentation only;
    # import rules unchanged: same-layer free, downward only)
    "strategy_research.kernel.gateway": "L3",
    "strategy_research.kernel.orchestration": "L3",
    "strategy_research.kernel.events": "L3",
    "strategy_research.kernel.session": "L3",
    "strategy_research.kernel.scheduled_research": "L3",
    "strategy_research.kernel.runtimes": "L3",
    "strategy_research.kernel.workflow": "L3",
    "strategy_research.kernel.swarm": "L3",
    "strategy_research.kernel.agent": "L3",
    "strategy_research.kernel.context": "L3",
    "strategy_research.kernel.hooks": "L3",
    "strategy_research.kernel.permission": "L3",
    "strategy_research.kernel.memory": "L3",
    "strategy_research.kernel.skills": "L3",
    "strategy_research.kernel": "L3",  # 物理层目录（逐模块迁移中）
    "strategy_research.kernel.seq_generator": "L3",  # 待退役（归 event_log 权威 seq）
    # L4 foundation
    "strategy_research.foundation.llm": "L4",
    "strategy_research.foundation.storage": "L4",
    "strategy_research.foundation.observability": "L4",
    "strategy_research.foundation.utils": "L4",
    "strategy_research.foundation.web": "L4",
    "strategy_research.foundation.registry": "L4",
    "strategy_research.foundation.config_loader": "L4",
    "strategy_research.foundation": "L4",  # W0 试点：层目录 __init__
    "strategy_research.foundation.errors": "L4",
    "strategy_research.foundation.git": "L4",
    "strategy_research.foundation.protocol": "L4",
    "strategy_research.foundation.compact_config": "L4",
    "strategy_research.foundation.structured_output": "L4",
}

# Facade __init__ modules excluded from direction assertions (re-exports only)
SKIP_MODULES = {"strategy_research"}


# ── 判定原语 ────────────────────────────────────────────────────────


def layer_of(module: str) -> str | None:
    """Longest-prefix match. Returns None when unregistered."""
    best, best_len = None, -1
    for prefix, layer in LAYER_MEMBERSHIP.items():
        if module == prefix or module.startswith(prefix + "."):
            if len(prefix) > best_len:
                best, best_len = layer, len(prefix)
    return best


def resolve_layer(module: str) -> str | None:
    """``layer_of`` with parent-prefix fallback (for submodules of a package)."""
    layer = layer_of(module)
    if layer is not None:
        return layer
    parts = module.split(".")
    while parts:
        parts.pop()
        layer = layer_of(".".join(parts))
        if layer is not None:
            return layer
    return None


def module_name(path: Path, src_root: Path) -> str:
    rel = path.relative_to(src_root).as_posix()[:-3]
    parts = [PKG] + [p for p in rel.split("/") if p != "__init__"]
    return ".".join(parts)


def resolve_targets(node: ast.AST, importer_mod: str, is_pkg: bool) -> list[str]:
    """Absolute module names referenced by an Import/ImportFrom node.

    ADR-000：物理口径——每条 import **语句**计 1 次。

    2026-11-04（W7 交叉验证修复）：level=0 的绝对导入必须**直接使用
    ``node.module``**，不得拼接 importer 包前缀——此前版本对绝对导入也
    执行 ``pkg + module`` 拼接，产生假路径并被判为同层，**漏报全部绝对
    形态的上行违规**。相对导入（level>0）才拼 pkg。

    对 ``from X import a, b`` 只产出模块 ``X``，不做 ``X.a`` 展开：
    展开会让绝对导入被重复计数，而相对导入因 target 相同被 set 去重
    只计 1 —— 两种形态计法不一致。
    """
    if isinstance(node, ast.Import):
        return sorted({a.name for a in node.names})
    if not isinstance(node, ast.ImportFrom):
        return []
    if node.level == 0:
        return [node.module] if node.module else []
    base = importer_mod.split(".")
    pkg = base if is_pkg else base[:-1]
    # PEP 328: strip level-1 components (NOT level)
    if node.level > 1:
        pkg = pkg[: len(pkg) - (node.level - 1)]
    target = ".".join(pkg + ([node.module] if node.module else []))
    if not node.module:
        return sorted({f"{target}.{a.name}" for a in node.names})
    return [target]


def collect(src_root: Path) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    """Walk once: returns (violations per cluster, cross-layer edges)."""
    viol: dict[str, int] = defaultdict(int)
    edges: dict[tuple[str, str], int] = defaultdict(int)
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = [
            d for d in dirnames if d not in ("__pycache__", "scripts", "graphify-out")
        ]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = Path(dirpath) / fn
            mod = module_name(path, src_root)
            if mod in SKIP_MODULES:
                continue
            src_layer = layer_of(mod)
            is_pkg = fn == "__init__.py"
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                for target in resolve_targets(node, mod, is_pkg):
                    tgt_layer = resolve_layer(target)
                    if tgt_layer is None or src_layer is None:
                        continue
                    edges[(src_layer, tgt_layer)] += 1
                    if ORDER.index(tgt_layer) < ORDER.index(src_layer):
                        viol[f"{src_layer}>{tgt_layer}"] += 1
    return viol, edges


def fmt_edges(edges: dict[tuple[str, str], int]) -> str:
    lines = ["      L1    L2    L3    L4"]
    for src in ORDER:
        row = [f"  {src} "]
        for dst in ORDER:
            row.append(f"{edges.get((src, dst), 0):>5} ")
        lines.append(" ".join(row))
    return "\n".join(lines)


__all__ = [
    "LAYER_MEMBERSHIP",
    "ORDER",
    "PKG",
    "SKIP_MODULES",
    "collect",
    "fmt_edges",
    "layer_of",
    "module_name",
    "resolve_layer",
    "resolve_targets",
]
