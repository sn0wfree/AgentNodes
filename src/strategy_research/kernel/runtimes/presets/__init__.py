"""presets —— 执行形状预设（纯平台件）。

chat  = 单 llm/react 节点退化图（Stage 1a/1b 直接构造）
research = 方法论阶段图（域件，落位 core/study/graph_preset.py——闭包持 BranchContext）
fanout = 拆分 → N 路并行子代理 → 汇合（本包）
"""
from __future__ import annotations

from .fanout import FAILURE_POLICIES, build_fanout_preset

__all__ = ["FAILURE_POLICIES", "build_fanout_preset"]
