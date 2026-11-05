"""Kernel layer (L3) - platform mechanisms (gateway/orchestration/events/session...).

三个子带（仅呈现维度，import 规则不变：同层自由、只许向下）：
  控制面 gateway/orchestration/events/session/scheduled_research
  执行面 runtimes/workflow/swarm
  能力面 agent/context/hooks/permission/memory/skills

依赖方向：kernel → foundation（见 tests/test_layer_matrix.py）。
"""

