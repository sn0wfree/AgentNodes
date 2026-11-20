# -*- coding: utf-8 -*-
"""第三轮复审候选 S3：可用动作矩阵单一纯策略的表驱动契约测试。

``kernel.session.service.available_actions`` 是 cancel/resume/send 矩阵与
goal lease 抑制 send（B2 裁决 2）的唯一定义点；L1 router 与（冻结保留的）
kernel 方法都经它产出。本文件直接钉住纯函数的四输入 → 输出矩阵。
"""
from __future__ import annotations

import pytest

from agentnodes.kernel.session.service import available_actions


def _names(actions: list[dict[str, str]]) -> list[str]:
    return [a["name"] for a in actions]


@pytest.mark.parametrize(
    "active,pending,paused,lease_holder,expected",
    [
        (False, False, False, None, ["send"]),
        (True, False, False, None, ["cancel"]),
        (False, True, False, None, ["cancel"]),
        (False, False, True, None, ["resume"]),
        (True, False, True, None, ["cancel", "resume"]),
        (False, False, False, "goal:g1", []),
        (False, False, False, "chat:r1", ["send"]),
        (True, False, False, "goal:g1", ["cancel"]),
    ],
)
def test_action_matrix(active, pending, paused, lease_holder, expected):
    actions = available_actions(
        active=active, pending=pending, paused=paused,
        lease_holder=lease_holder,
    )
    assert _names(actions) == expected
    for a in actions:
        assert set(a) == {"name", "label", "destructive"}
        assert a["destructive"] == "false"


def test_labels_match_ui_vocabulary():
    """label 与既有两个调用方逐字一致（改 label 会打断 UI 契约）。"""
    assert available_actions(active=True, pending=False, paused=False) == [
        {"name": "cancel", "label": "取消", "destructive": "false"},
    ]
    assert available_actions(active=False, pending=False, paused=True) == [
        {"name": "resume", "label": "恢复", "destructive": "false"},
    ]
    assert available_actions(active=False, pending=False, paused=False) == [
        {"name": "send", "label": "发送", "destructive": "false"},
    ]
