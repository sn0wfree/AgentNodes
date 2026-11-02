"""H 档补测 —— `core/autoresearch.py` 的输入编排辅助函数。

142 行未覆盖。选这批**纯函数**（无 LLM、无子进程）：
- ``parse_agent_output``：五级 JSON 提取降级梯（直解 → ```json → ``` →
  {…} → […] → parse_failed）。LLM 输出格式漂移时全靠它兜底。
- ``build_agent_prompt``：角色定义 + 状态 + 上游输出 + 输出格式的拼装。
- ``save_agent_record`` / ``read_agent_history``：agents/*.json 的读写与
  「近期读详细 / 远期读摘要」的两级策略。
- ``get_cooldown_seconds`` / ``should_run_lazy_detection``：调度阈值。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core import autoresearch as ar


# ── parse_agent_output 降级梯 ───────────────────────────────


def test_parse_empty_returns_empty_output_error():
    assert ar.parse_agent_output("") == {"error": "empty_output"}
    assert ar.parse_agent_output("   \n ") == {"error": "empty_output"}


def test_parse_direct_json():
    assert ar.parse_agent_output('{"a": 1}') == {"a": 1}


def test_parse_fenced_json_with_lang():
    raw = 'Some prose\n```json\n{"a": 2}\n```\nmore prose'
    assert ar.parse_agent_output(raw) == {"a": 2}


def test_parse_fenced_code_without_lang():
    raw = "```\n{\"a\": 3}\n```"
    assert ar.parse_agent_output(raw) == {"a": 3}


def test_parse_bare_object_in_prose():
    assert ar.parse_agent_output('result: {"a": 4} done') == {"a": 4}


def test_parse_bare_array_in_prose():
    assert ar.parse_agent_output("here: [1, 2, 3]") == [1, 2, 3]


def test_parse_unrecoverable_returns_parse_failed():
    out = ar.parse_agent_output("完全不是 JSON")
    assert out["error"] == "parse_failed"
    assert out["raw"] == "完全不是 JSON"


def test_parse_truncates_raw_to_1000_chars():
    """parse_failed 的 raw 字段截断到 1000 字符（防事件负载爆掉）。"""
    out = ar.parse_agent_output("x" * 5000)
    assert len(out["raw"]) == 1000


# ── build_agent_prompt ─────────────────────────────────────


@pytest.fixture
def prompts_dir(tmp_path) -> Path:
    d = tmp_path / ".prompts"
    d.mkdir()
    (d / "researcher.md").write_text("# 角色：研究员\n提出假设。", encoding="utf-8")
    return d


def test_build_prompt_includes_role_and_state(prompts_dir):
    p = ar.build_agent_prompt("researcher", prompts_dir, {
        "strategy_py": "def compute(): pass",
        "best_calmar": 1.5,
        "current_calmar": 1.2,
        "total_runs": 7,
    })
    assert "角色：研究员" in p
    assert "def compute(): pass" in p
    assert "1.5" in p and "1.2" in p and "7" in p


def test_build_prompt_states_json_output_requirement(prompts_dir):
    p = ar.build_agent_prompt("researcher", prompts_dir, {})
    assert "必须返回纯 JSON" in p


def test_build_prompt_includes_last_previous_output(prompts_dir):
    prev = [{"round": 1}, {"round": 2, "note": "latest"}]
    p = ar.build_agent_prompt("researcher", prompts_dir, {},
                              previous_outputs=prev)
    assert '"note": "latest"' in p
    assert '"round": 1' not in p     # 只取最后一个


def test_build_prompt_missing_role_file_raises(prompts_dir):
    with pytest.raises(FileNotFoundError, match="Prompt file not found"):
        ar.build_agent_prompt("ghost", prompts_dir, {})


def test_build_prompt_defaults_for_missing_state_keys(prompts_dir):
    """缺 state 键时用 0 / 空串，不抛 KeyError。"""
    p = ar.build_agent_prompt("researcher", prompts_dir, {"best_calmar": 2.0})
    assert "最佳 Calmar: 2.0" in p
    assert "当前 Calmar: 0" in p
    assert "总轮数: 0" in p


# ── save_agent_record / read_agent_history ─────────────────


def test_save_agent_record_writes_json(tmp_path):
    path = ar.save_agent_record(tmp_path, "researcher", 1,
                                {"in": 1}, {"out": 2}, duration_ms=42)
    assert path == tmp_path / "agents" / "researcher.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["agent"] == "researcher"
    assert rec["step"] == 1
    assert rec["input"] == {"in": 1}
    assert rec["output"] == {"out": 2}
    assert rec["duration_ms"] == 42


def test_read_agent_history_detailed_source(tmp_path):
    run_dir = tmp_path / "run_0001"
    ar.save_agent_record(run_dir, "researcher", 1, {}, {"verdict": "keep"})
    hist = ar.read_agent_history(tmp_path, "researcher")
    assert len(hist) == 1
    assert hist[0]["round"] == 1
    assert hist[0]["source"] == "detailed"
    assert hist[0]["output"] == {"verdict": "keep"}


def test_read_agent_history_skips_unparseable_run_dirs(tmp_path):
    (tmp_path / "run_bad").mkdir()
    (tmp_path / "not_a_run").mkdir()
    assert ar.read_agent_history(tmp_path, "researcher") == []


def test_read_agent_history_threshold_limits_rounds(tmp_path):
    for i in range(1, 6):
        ar.save_agent_record(tmp_path / f"run_{i:04d}", "researcher", i, {},
                             {"n": i})
    hist = ar.read_agent_history(tmp_path, "researcher", threshold=2)
    assert len(hist) == 2
    assert [h["round"] for h in hist] == [4, 5]


def test_read_agent_history_missing_agent_file_falls_back(tmp_path):
    """agent json 不存在 → 不回退到 summary 时该轮跳过（不抛）。"""
    (tmp_path / "run_0001").mkdir()
    assert ar.read_agent_history(tmp_path, "nobody") == []


# ── 调度阈值 ────────────────────────────────────────────────


def test_cooldown_within_jitter_band():
    for _ in range(20):
        v = ar.get_cooldown_seconds(base_cooldown=30.0, jitter=10.0)
        assert 20.0 <= v <= 40.0


def test_cooldown_respects_minimum():
    """jitter 大于 base 时被 min_cooldown 兜住。"""
    for _ in range(20):
        v = ar.get_cooldown_seconds(base_cooldown=1.0, jitter=100.0,
                                    min_cooldown=1.0)
        assert v >= 1.0


@pytest.mark.parametrize("round_num,expected", [
    (0, False), (1, False), (9, False), (10, True), (20, True), (21, False),
])
def test_should_run_lazy_detection(round_num, expected):
    assert ar.should_run_lazy_detection(round_num, interval=10) is expected


def test_should_read_detailed_recent_rounds():
    """最近 keep_recent 轮读详细。"""
    assert ar.should_read_detailed(10, current_round=10, keep_recent=10) is True


def test_should_read_detailed_old_rounds():
    assert ar.should_read_detailed(1, current_round=20, keep_recent=10) is False
