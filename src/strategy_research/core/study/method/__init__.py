"""method —— 研究方法论域件（假设→证伪→AEGIS 门→评审循环）。

边界（docs/d1-method-relocation-design-20261030.md）：
- 本包是方法论的**词汇与规则**（AEGIS 门、claims、指标目标、引导、评审），
  被执行机制（runner/scheduler/graph_preset）调用；
- **不得 import 执行侧**（runner/scheduler/graph_preset/scenario_router/
  store/phase_engine）——门禁
  ``test_method_never_imports_engine`` 强制。
"""
