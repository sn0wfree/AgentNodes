"""领域中立算法（algo）——不含 agent/engine 语义，只有可独立验证的数学闭包。

首个成员：无前视调仓回测（``backtest``，自 templates/prepare.py 提取，
见 docs/design/prepare-algo-kernel-extract-20261114.md）。

**为什么在 apps/ 而不在 kernel/**：本模块的词表（``factor``/``backtest``/
``sharpe``/``calmar``/``drawdown``/``portfolio``）是量化域专属名词，
与 R-001 的「kernel 零业务名词」红线（``scripts/arch/check_kernel_nouns.py``）
直接冲突——2026-11-14 的 C 方案把它放进 ``kernel/algo`` 后，门禁即转红
（51 处命中）。2026-11-16 迁回业务层：算法本身领域中立，但**名词是业务的**。
"""

from .backtest import compute_simple_factors, evaluate

__all__ = ["evaluate", "compute_simple_factors"]
