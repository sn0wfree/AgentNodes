"""领域中立算法（algo）——不含 agent/engine 语义，只有可独立验证的数学闭包。

首个成员：无前视调仓回测（``backtest``，自 templates/prepare.py 提取，
见 docs/design/prepare-algo-kernel-extract-20261114.md）。
"""

from .backtest import compute_simple_factors, evaluate

__all__ = ["evaluate", "compute_simple_factors"]
