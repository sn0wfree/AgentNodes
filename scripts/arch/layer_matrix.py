# -*- coding: utf-8 -*-
"""layer_matrix — 四层骨架的层归属与判定原语（**唯一事实源**）。

契约：``docs/design/layered-skeleton-design-20261103.md`` §2/§4。
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

PKG = "agentnodes"

ORDER = ["L1", "L2", "L3", "L4"]  # index 0 = highest

# ── layer membership (design doc §2, longest-prefix match) ──────────

LAYER_MEMBERSHIP: dict[str, str] = {
    # ── L1 interfaces ─────────────────────────────────────────────────
    "agentnodes.interfaces": "L1",  # 物理层目录
    "agentnodes.interfaces.api": "L1",
    "agentnodes.interfaces.cli": "L1",
    "agentnodes.interfaces.mcp": "L1",
    # ── L2 apps ───────────────────────────────────────────────────────
    # 2026-11-05 批次 8 后只剩业务插件面（回测/因子/数据/研究模板都已移除）：
    #   builtin_tools  6 个业务工具模块（批次 6 从 L3 归位）
    #   capabilities   注册门面（tool.* kind）
    #   data_store_registry / workspace_setup  两个小件
    "agentnodes.apps": "L2",  # 物理层目录
    "agentnodes.apps.capabilities": "L2",
    "agentnodes.apps.builtin_tools": "L2",
    "agentnodes.apps.data_store_registry": "L2",
    "agentnodes.apps.workspace_setup": "L2",
    "agentnodes.templates": "L2",
    # ── L3 kernel (平台机制) ──────────────────────────────────────────
    # 2026-11-05 批次 8：evolution 从 L2 移入（RSI 自我进化是内核机制）。
    "agentnodes.kernel.gateway": "L3",
    "agentnodes.kernel.orchestration": "L3",
    "agentnodes.kernel.events": "L3",
    "agentnodes.kernel.session": "L3",
    "agentnodes.kernel.scheduled_research": "L3",
    "agentnodes.kernel.runtimes": "L3",
    "agentnodes.kernel.workflow": "L3",
    "agentnodes.kernel.swarm": "L3",
    "agentnodes.kernel.agent": "L3",
    "agentnodes.kernel.context": "L3",
    "agentnodes.kernel.hooks": "L3",
    "agentnodes.kernel.permission": "L3",
    "agentnodes.kernel.memory": "L3",
    "agentnodes.kernel.skills": "L3",
    "agentnodes.kernel.evolution": "L3",  # 批次 8：RSI 自我进化（原 apps.evolution）
    "agentnodes.kernel": "L3",  # 物理层目录
    "agentnodes.kernel.seq_generator": "L3",  # 待退役（归 event_log 权威 seq）
    # evals —— 金标准评测集，RSI 的信号源（2026-11-05 从仓库根收编进包）
    "agentnodes.evals": "L3",
    # L4 foundation
    "agentnodes.foundation.llm": "L4",
    "agentnodes.foundation.storage": "L4",
    "agentnodes.foundation.observability": "L4",
    "agentnodes.foundation.utils": "L4",
    "agentnodes.foundation.web": "L4",
    "agentnodes.foundation.registry": "L4",
    "agentnodes.foundation.config_loader": "L4",
    "agentnodes.foundation": "L4",  # W0 试点：层目录 __init__
    "agentnodes.foundation.errors": "L4",
    "agentnodes.foundation.git": "L4",
    "agentnodes.foundation.protocol": "L4",
    "agentnodes.foundation.compact_config": "L4",
    "agentnodes.foundation.structured_output": "L4",
}

# Facade __init__ modules excluded from direction assertions (re-exports only)
SKIP_MODULES = {"agentnodes"}


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
