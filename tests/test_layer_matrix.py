"""Layer-matrix gate — the four-layer skeleton's machine check.

契约：docs/design/layered-skeleton-design-20261103.md §2/§4。
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

import os
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "strategy_research"
PKG = "strategy_research"

# 层归属与判定原语来自唯一事实源（scripts/arch/layer_matrix.py）——
# 与 scripts/arch/check_dangling_imports.py --layers 共用同一张表，
# 避免两个门禁漂移出不同的判定结果。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "arch"))
import layer_matrix as _lm  # noqa: E402

ORDER = _lm.ORDER
LAYER_MEMBERSHIP = _lm.LAYER_MEMBERSHIP
SKIP_MODULES = _lm.SKIP_MODULES

_layer_of = _lm.layer_of
_module_name = lambda path: _lm.module_name(path, SRC)  # noqa: E731
_resolve_targets = _lm.resolve_targets
_fmt_edges = _lm.fmt_edges

# ── ratchet baselines (ADR-000 物理口径 2026-11-03；只降不升) ────────

# 口径：每条 import 语句计 1（同节点多 alias 指向同层不重复）。
# 2026-11-03 W3a 复算：_resolve_targets 修正后（绝对导入不再按 alias 展开
# 重复计数），实测远低于 2026-11-03 首版基线（那版被 alias 展开虚增）：
#   L3>L1 2→1 · L3>L2 46→35 · L4>L2 2→0 · L4>L3 4→2
# 2026-11-04（W7 交叉验证二修）：graphify 重扫对照发现 _resolve_targets 对
# level=0 绝对导入也拼 importer 前缀（W3a 引入），**全部绝对形态上行被
# 漏报**——此前的「全 0」是假象。修复后真实基线 49 条，按其性质如实登记：
#   L3>L2 = 46：ADR-003 处置保留（kernel 工具/编排绑定研究域能力，lazy
#     import 为主体；reopen 条件：第三消费者出现）。迁移未加重。
#   L3>L1 = 2 + L2>L1 = 1：seam 契约登记的降级回调触点（G2a 台账原三件：
#     event_store→projector、memory_manager→web_session_db、
#     scheduler→web_session）。迁移前即存在。
#   L4>L2 = 0：graphify 暴露的 3 条已就地修复（backtest_models×2 同层化、
#     duckdb_store/registry 适配件搬 apps——适配器依赖被适配者属 L2 语义）。
BASELINE: dict[str, int] = {
    "L2>L1": 1,   # seam 触点：study/scheduler → api.routers.web_session
    "L3>L1": 2,   # seam 触点：event_store→projector · memory_manager→web_session_db
    # 2026-11-04 W15（阶段二）：ADR-003 CapabilityRegistry 端口化落地。
    # 46 条 L3→L2 全部改为 capability resolve（apps 侧主动注册，import
    # 方向仍是 apps → kernel）。基线由 46 下调至 0 —— 这是 goal H2
    # 「只降不升」要求的下调动作，不是放宽。
    "L3>L2": 0,
    "L4>L2": 0,   # W7 清零（data store 适配件归位 apps）
    "L4>L3": 0,
}


def _collect() -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    """委托给共享实现（scripts/arch/layer_matrix.py）——单一事实源。"""
    return _lm.collect(SRC)


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
            + "\n设计契约：docs/design/layered-skeleton-design-20261103.md"
        )
