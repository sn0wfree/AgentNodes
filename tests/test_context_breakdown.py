# -*- coding: utf-8 -*-
"""上下文细拆单测（§9 复核批：后端细拆 10 段，前端配置聚合）。

归属是角色驱动（非位置切片）：
- 末条 user 手写文本计入 user_text（扣除注入前缀）→ 修复「当前轮缺位」
- tool_calls / tool_results 按角色归工具段 → 修复「迭代口径漂移」
- 历史里混入的工具流量不再污染对话段
"""
from __future__ import annotations

from agentnodes.kernel.context.breakdown import (
    BREAKDOWN_KEYS,
    MCP_TOOL_PREFIX,
    estimate_context_breakdown,
    estimate_tokens_cjk,
    estimate_tokens_text_cjk,
)


class _Item:
    def __init__(self, kind: str, content: str):
        self.kind = kind
        self.content = content


def test_all_fine_keys_present():
    result = estimate_context_breakdown([], [])
    assert set(result) == set(BREAKDOWN_KEYS)
    assert len(BREAKDOWN_KEYS) == 10


def test_system_prompt_and_dialogue_split():
    """对话按角色细拆：user_text / assistant_text 分开计数。"""
    msgs = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "上一轮"},
        {"role": "assistant", "content": "回答"},
        {"role": "user", "content": "当前问题"},
    ]
    r = estimate_context_breakdown(msgs, [])
    assert r["system_prompt"] > 0
    assert r["user_text"] > 0
    assert r["assistant_text"] > 0
    assert r["meta_user_context"] == 0  # 短末条 user 不算注入


def test_current_turn_user_text_is_counted():
    """§9 修复「当前轮缺位」：末条 user 的手写文本必须计入 user_text。"""
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "你好"},
    ]
    r = estimate_context_breakdown(msgs, [])
    assert r["user_text"] >= 2  # 2 个汉字 = 2 token（CJK 1:1）


def test_tool_calls_and_results_go_to_own_segments():
    """§9 修复「历史消息混入工具流量」：工具调用与返回值单独成段。"""
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "查一下"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "t1", "type": "function",
                "function": {"name": "search", "arguments": '{"query": "python"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "搜索结果" * 50},
        {"role": "assistant", "content": "结果是……"},
    ]
    r = estimate_context_breakdown(msgs, [])
    assert r["tool_calls"] > 0
    assert r["tool_results"] >= int(200 / 3)  # 200 汉字 CJK 计 200，远超 100 开销
    # 对话段不再包含工具流量
    assert r["user_text"] > 0 and r["assistant_text"] > 0


def test_last_user_position_is_irrelevant_mid_iteration():
    """§9 修复「迭代口径漂移」：末条是 tool_result 时，当前 user 仍归 user_text。"""
    base = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "问题"},
    ]
    with_user_last = base + [{"role": "assistant", "content": "答"}]
    with_tool_last = base + [
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "tool", "tool_call_id": "t", "content": "x"},
    ]
    r1 = estimate_context_breakdown(with_user_last, [])
    r2 = estimate_context_breakdown(with_tool_last, [])
    # 两条路径下 user 文本都完整计入（位置无关）
    assert r1["user_text"] == r2["user_text"] > 0


def test_injected_prefix_subtracted_from_user_text():
    """注入前缀已按 items 计入 meta/skills，user_text 只算手写部分（不重复）。"""
    prefix = "注入上下文" * 30  # 150 字
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": prefix + "\n\n" + "你好"},
    ]
    items = [_Item("memory", prefix)]
    r = estimate_context_breakdown(msgs, [], context_items=items)
    # meta 记注入的 150 字；user_text 只剩手写「你好」= 2
    assert r["meta_user_context"] == 150
    assert r["user_text"] == 2


def test_context_items_map_to_meta_and_skills():
    items = [
        _Item("skill", "技能内容" * 100),
        _Item("goal", "目标内容" * 100),
        _Item("memory", "记忆内容" * 100),
    ]
    r = estimate_context_breakdown(
        [{"role": "user", "content": "hi"}], [], context_items=items,
    )
    assert r["skills"] > 0
    assert r["meta_user_context"] > 0
    # skills 与 meta 分开归属
    assert r["skills"] != r["meta_user_context"]


def test_tools_split_description_and_schema():
    tools = [{
        "name": "search",
        "description": "搜索网页" * 50,
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
    }]
    r = estimate_context_breakdown([{"role": "user", "content": "hi"}], tools)
    assert r["tool_prompt"] > 0
    assert r["system_tool_schemas"] > 0
    assert r["mcp_tool_schemas"] == 0


def test_mcp_tools_go_to_mcp_bucket():
    tools = [
        {
            "name": f"{MCP_TOOL_PREFIX}github_search",
            "description": "MCP 工具" * 50,
            "parameters": {"type": "object"},
        },
        {
            "name": "custom",
            "server": "mcp-server",
            "description": "另一 MCP" * 50,
        },
    ]
    r = estimate_context_breakdown([{"role": "user", "content": "hi"}], tools)
    assert r["mcp_tool_schemas"] > 0
    assert r["tool_prompt"] == 0
    assert r["system_tool_schemas"] == 0


def test_segments_sum_is_reasonable():
    msgs = [
        {"role": "system", "content": "规则" * 100},
        {"role": "user", "content": "历史" * 200},
        {"role": "assistant", "content": "回答" * 200},
        {"role": "user", "content": "问题"},
    ]
    tools = [{"name": "t", "description": "desc" * 100, "parameters": {"a": 1}}]
    r = estimate_context_breakdown(msgs, tools, context_items=[_Item("skill", "s" * 100)])
    total = sum(r.values())
    assert total > 0
    assert all(v >= 0 for v in r.values())


# ── CJK 感知估算（§9 复核修复 3）─────────────────────────────────

def test_cjk_estimator_counts_ideographs_as_one_token_each():
    # 4 个汉字 = 4 token（旧 chars/3 只估 1）
    assert estimate_tokens_text_cjk("你好世界") == 4
    # 纯 ASCII：3 字符 ≈ 1 token
    assert estimate_tokens_text_cjk("abcdefghijkl") == 4


def test_cjk_estimator_handles_mixed_text():
    # "你好"=2 token + "hello"(5 chars≈1.67) + "世界"=2 token ≈ 5
    got = estimate_tokens_text_cjk("你好hello世界")
    assert got == 5, got


def test_public_estimate_tokens_unchanged():
    """公共函数不能被 CJK 改动污染 —— compact/loop 仍用 chars/3。"""
    from agentnodes.foundation.utils.token_utils import estimate_tokens, CHARS_PER_TOKEN

    msgs = [{"role": "user", "content": "你好世界"}]  # 4 字符
    assert estimate_tokens(msgs) == max(1, int(4 / CHARS_PER_TOKEN)) == 1


def test_breakdown_uses_cjk_for_chinese_history():
    """中文对话不再被 chars/3 低估：按角色拆到 user_text / assistant_text。"""
    msgs = [
        {"role": "system", "content": "规则" * 50},
        {"role": "user", "content": "上一轮" * 10},       # 30 汉字 = 30 token
        {"role": "assistant", "content": "回答" * 15},     # 30 汉字 = 30 token
        {"role": "user", "content": "当前问题"},
    ]
    r = estimate_context_breakdown(msgs, [])
    assert r["user_text"] == 34, r["user_text"]      # 30 + 「当前问题」4 字
    assert r["assistant_text"] == 30, r["assistant_text"]


def test_breakdown_tools_work_with_cjk_description():
    tools = [{
        "name": "search",
        "description": "搜索网页" * 25,  # 100 汉字 = 100 token
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
    }]
    r = estimate_context_breakdown([{"role": "user", "content": "hi"}], tools)
    assert r["tool_prompt"] == 100, r["tool_prompt"]
    assert r["system_tool_schemas"] > 0
