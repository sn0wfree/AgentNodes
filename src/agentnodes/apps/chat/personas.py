# -*- coding: utf-8 -*-
"""可选对话角色（persona）清单 —— 产品策略，不是平台机制。

**为什么在 L2**：哪些角色可以被用户选中、每个角色的中文展示名与一句话
说明，是**产品决策**。它随版本调整（新增一个「合规审查」角色、下线一个
「反过拟合」角色），但不影响任何平台机制。

**与 L3 的关系**：角色本体（prompt 正文、``PromptBuilderFactory``）在
``kernel/agent``；本模块只提供「面向用户的元数据」。L1 把两者拼起来
返回给前端。方向：L1 → L2（本模块）+ L1 → L3（registry），互不依赖。
"""
from __future__ import annotations

#: (id, 中文名, 一句话说明)。缺失的 id 回退为 (id, "")。
#: ``workflow_orchestrator`` 不出现在通用选择器里——它固定绑定
#: ``dag:{name}`` 会话，由后端自动指派。
PERSONA_LABELS: dict[str, tuple[str, str]] = {
    "chat": ("通用助手", "默认聊天助手，适合日常问答与策略讨论"),
    "researcher": ("研究员", "深度资料调研与信息检索"),
    "strategist": ("策略师", "策略设计与回测分析"),
    "factor_analyst": ("因子分析师", "因子计算与有效性分析"),
    "data_quality": ("数据质量", "数据质量与来源核查"),
    "portfolio_construction": ("组合构建", "组合权重与配置优化"),
    "risk_controller": ("风控", "风险度量与回撤控制"),
    "attribution_analyst": ("归因分析", "收益与风险归因"),
    "anti_overfit_analyst": ("反过拟合", "过拟合诊断与稳健性检验"),
    "backtest_diagnostics": ("回测诊断", "回测结果核查与诊断"),
    "critic": ("评审", "对结论与策略进行批判性审查"),
    "workflow_orchestrator": (
        "编排助手",
        "DAG 编排专用：把任务拆解为节点并通过 submit_dag_step 提交；"
        "固定用于 dag:{name} 会话",
    ),
}

#: 默认角色（未指定时使用）。
DEFAULT_PERSONA = "chat"


def describe(persona_id: str) -> dict[str, str]:
    """Return ``{"id", "name", "description"}`` for one persona.

    未登记的 id 原样返回 id，描述留空——**不隐藏**未知角色，因为角色
    清单由 L3 的 registry 决定，本模块只是元数据补充。
    """
    name, desc = PERSONA_LABELS.get(persona_id, (persona_id, ""))
    return {"id": persona_id, "name": name, "description": desc}


def list_for(role_ids: list[str]) -> list[dict[str, str]]:
    """按给定顺序为每个 role id 产出展示元数据。

    顺序由调用方（L1）从 registry 拿到的顺序决定——本模块不做排序，
    避免与 L3 的角色发现机制各自维护一份顺序。
    """
    return [describe(rid) for rid in role_ids]


__all__ = ["DEFAULT_PERSONA", "PERSONA_LABELS", "describe", "list_for"]
