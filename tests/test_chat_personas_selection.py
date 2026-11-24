# -*- coding: utf-8 -*-
"""F1 · 角色下拉过滤（composer-toolbar-p0p1-fix-20261124）。

问题：``GET /api/chat/personas`` 此前直接返回 ``list_roles()``，把插件内部
执行角色（planner / evaluator / study_reviewer / study_collector）以裸 id
混入下拉、把固定绑定 dag 会话的 ``workflow_orchestrator`` 也暴露出来，且
registry 的 ``set()`` 合并导致顺序不稳定。

本测试锁定修复后的契约：只返回面向用户的角色 + 固定顺序 + 每项都有中文名。
"""
from __future__ import annotations

from agentnodes.apps.chat.personas import (
    DEFAULT_PERSONA,
    PERSONA_LABELS,
    PUBLIC_PERSONA_IDS,
    describe,
    list_public,
)


def test_public_ids_exclude_internal_and_orchestrator():
    """内部执行角色与编排助手不进公开清单。"""
    assert "workflow_orchestrator" not in PUBLIC_PERSONA_IDS
    for internal in ("planner", "evaluator", "orchestrator",
                     "study_reviewer", "study_collector"):
        assert internal not in PUBLIC_PERSONA_IDS


def test_public_ids_are_all_registered():
    """公开清单里的每个 id 都要有中文名与说明（不能是裸 id）。"""
    for pid in PUBLIC_PERSONA_IDS:
        assert pid in PERSONA_LABELS, f"{pid} 未登记展示元数据"
        name, desc = PERSONA_LABELS[pid]
        assert name and desc, f"{pid} 缺中文名/说明"


def test_list_public_filters_internal_roles():
    """registry 里混入内部角色时，输出只含公开角色。"""
    roles = [
        "planner", "evaluator", "study_reviewer", "study_collector",
        "orchestrator", "workflow_orchestrator",
        "chat", "researcher", "strategist", "factor_analyst", "data_quality",
        "portfolio_construction", "risk_controller", "attribution_analyst",
        "anti_overfit_analyst", "backtest_diagnostics", "critic",
    ]
    out = list_public(roles)
    ids = [p["id"] for p in out]
    assert ids == list(PUBLIC_PERSONA_IDS)
    for internal in ("planner", "evaluator", "study_reviewer",
                     "study_collector", "orchestrator", "workflow_orchestrator"):
        assert internal not in ids


def test_list_public_order_is_stable_regardless_of_registry_order():
    """registry 的 set() 顺序不同，输出顺序必须一致。"""
    a = ["critic", "chat", "researcher", "strategist"]
    b = ["researcher", "critic", "chat", "strategist"]
    assert [p["id"] for p in list_public(a)] == [p["id"] for p in list_public(b)]


def test_list_public_skips_unregistered_public_roles():
    """公开清单里但 registry 未注册的角色不出现（registry 是存在性事实源）。"""
    out = list_public(["chat", "researcher"])
    assert [p["id"] for p in out] == ["chat", "researcher"]
    assert "strategist" not in [p["id"] for p in out]


def test_list_public_items_have_chinese_name_and_description():
    """每一项都带中文名 + 一句话说明，前端不再出现裸 id。"""
    out = list_public(list(PUBLIC_PERSONA_IDS))
    for item in out:
        assert item["name"]
        assert item["description"]
        assert item["name"] != item["id"], f"{item['id']} 显示为裸 id"


def test_default_persona_is_public_and_first():
    """默认角色在公开清单内且排第一。"""
    assert DEFAULT_PERSONA in PUBLIC_PERSONA_IDS
    assert PUBLIC_PERSONA_IDS[0] == DEFAULT_PERSONA


def test_describe_still_supports_unknown_ids():
    """describe 保持原契约：未知 id 原样返回（元数据补充层不隐藏）。"""
    assert describe("mystery") == {"id": "mystery", "name": "mystery", "description": ""}
