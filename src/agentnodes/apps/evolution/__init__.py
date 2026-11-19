# -*- coding: utf-8 -*-
"""apps.evolution —— RSI 自我进化的领域逻辑（WP-8 批 3b 逐批落位）。

落位裁决：RSI 与 goal 是不同领域，独立成簇、**不并入 apps/goal**
（l1-three-frontends-one-core-design-20261119.md 裁决 3 / roadmap WP-8）。
本包只收领域策略与驱动；闭环装配根留在 ``interfaces/rsi_wiring.py``
（批 3b.3 收敛为只剩构造）。

零 re-export（R-014 纪律）：消费方直接 import 具体模块路径。

批次（roadmap WP-8 建议步骤）：
- 3b.1 ``baseline_policy.BaselinePolicy``（滚动中位数基线，自 rsi_wiring 迁入）
- 3b.2 预算闸 + 状态 + 证据门 + ``run_once``（halt 改 callable 注入）
- 3b.3 wiring 收敛
"""
