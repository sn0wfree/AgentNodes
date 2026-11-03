"""Alpha Zoo 因子库。

包含 5 个因子库:
- alpha101: Kakushadze 101 个公式化因子
- gtja191: 国泰君安 191 个 A 股截面因子
- qlib158: 微软 Qlib 158 个 ML 因子
- academic: 11 个学术因子 (Fama-French, Carhart 等)
- fundamental: 4 个基本面因子

加载优先级: YAML 优先，.py 作为 fallback。

实现已迁移至 ``loader.py`` (Phase 1.2 重构)。本文件保留向后兼容的
re-export，使得 ``from strategy_research.apps.alpha_zoo import compute_alpha``
等调用无需修改。
"""

from .loader import (
    ALPHA_ZOOS,
    AlphaLoader,
    compute_alpha,
    list_alphas,
    parse_alpha_id,
)

# Backward-compat alias — historically ``AlphaZooAdapter`` lived in
# ``apps/alpha_zoo_adapter.py``; new code should import ``AlphaLoader``
# from ``core.alpha_zoo.loader`` directly.
AlphaZooAdapter = AlphaLoader

__all__ = [
    "ALPHA_ZOOS",
    "AlphaLoader",
    "AlphaZooAdapter",
    "compute_alpha",
    "list_alphas",
    "parse_alpha_id",
]
