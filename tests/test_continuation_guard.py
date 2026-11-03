"""GoalContinuation JSON-guard 分支补充测试（现有 test_context_injector.py 未覆盖的路径）。

覆盖 docs/coverage-90-plan-20261030.md §3.2：完整 JSON 跳过 / 空 dict
候选 / 截断 JSON 候选 / 非字符串 content。
"""
from __future__ import annotations

from types import SimpleNamespace

from strategy_research.kernel.agent.context_injector import (
    GoalContinuationInjector,
)


def _loop(**kw):
    defaults = dict(session_id="s1", enable_goal_injection=True,
                    _trace=lambda *a, **k: None)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class Resp:
    def __init__(self, content):
        self.content = content


def test_complete_nonempty_json_is_final_answer():
    """完整非空 JSON = 结构化终答 → 不注入续跑（防 max_iter 吞产出）。"""
    inj = GoalContinuationInjector()
    result = SimpleNamespace(messages=[])
    resp = Resp('{"verdict": "keep", "score": 0.9}')
    assert inj.inject_post_response(_loop(), resp, [], result, 1) is False


def test_empty_json_is_continuation_candidate():
    """空 dict {} 无答案 → 仍是续跑候选（guard 放行后由下游 False 拦）。"""
    inj = GoalContinuationInjector()
    result = SimpleNamespace(messages=[])
    resp = Resp("{}")
    assert inj.inject_post_response(
        _loop(enable_goal_injection=False), resp, [], result, 1) is False


def test_truncated_json_is_continuation_candidate():
    """截断 JSON（ValueError）→ 仍是续跑候选。"""
    inj = GoalContinuationInjector()
    result = SimpleNamespace(messages=[])
    resp = Resp('{"verdict": "keep"')
    assert inj.inject_post_response(
        _loop(enable_goal_injection=False), resp, [], result, 1) is False


def test_non_str_content_tolerated():
    """content 非 str（测试替身）→ TypeError 路径 → 下游 False。"""
    inj = GoalContinuationInjector()
    result = SimpleNamespace(messages=[])
    resp = SimpleNamespace(content=12345)
    assert inj.inject_post_response(
        _loop(enable_goal_injection=False), resp, [], result, 1) is False


def test_json_array_content_not_final():
    """JSON 数组不是 dict → 不是终答 → 下游处理。"""
    inj = GoalContinuationInjector()
    result = SimpleNamespace(messages=[])
    resp = Resp("[1, 2]")
    assert inj.inject_post_response(
        _loop(enable_goal_injection=False), resp, [], result, 1) is False
