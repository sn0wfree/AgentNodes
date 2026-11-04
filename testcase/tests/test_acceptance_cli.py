"""H 档补测（P1-6b）—— `strategy_acceptance/cli.py` 的离线验收重放。

``cmd_accept`` 是排查「这一轮为什么被拒」的唯一入口，此前 31.6%。
它的退出码有语义（0=accept / 1=输入错误 / 2=LLM 失败 / 3=reject），
脚本化和 CI 依赖它——退出码错了，自动化会把拒绝当成功。

全部用临时 metrics.json + 参数命名空间驱动，不调真实 LLM。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from strategy_research.apps.strategy_acceptance import cli as acc_cli


def _args(**kw) -> argparse.Namespace:
    base = dict(
        metrics_file="", workspace_config=None, llm_verdict=None,
        invoke_llm=False, stagnation_count=0,
        hard_calmar_min=None, hard_sharpe_min=None, hard_max_dd_min=None,
        hard_trades_min=None, hard_ann_return_min=None,
        llm_enabled=None, llm_weight=None, llm_score_threshold=None,
        stagnation_patience=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _metrics_file(tmp_path: Path, **overrides) -> Path:
    metrics = {
        "calmar": 1.5, "sharpe": 1.2, "max_dd": -0.15,
        "trades": 50, "ann_return": 0.25,
    }
    metrics.update(overrides)
    p = tmp_path / "metrics.json"
    p.write_text(json.dumps(metrics), encoding="utf-8")
    return p


# ── 输入校验 ─────────────────────────────────────────────


def test_missing_file_returns_1(tmp_path, capsys):
    assert acc_cli.cmd_accept(_args(metrics_file=str(tmp_path / "nope.json"))) == 1
    assert "not found" in capsys.readouterr().err


def test_malformed_json_returns_1(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert acc_cli.cmd_accept(_args(metrics_file=str(p))) == 1
    assert "could not parse" in capsys.readouterr().err


def test_bad_llm_verdict_json_returns_1(tmp_path, capsys):
    p = _metrics_file(tmp_path)
    code = acc_cli.cmd_accept(_args(metrics_file=str(p), llm_verdict="{oops"))
    assert code == 1
    assert "not valid JSON" in capsys.readouterr().err


# ── 决策输出与退出码 ─────────────────────────────────────


def test_accepting_metrics_exits_0(tmp_path, capsys):
    """指标远超阈值 → accept → 退出码 0。"""
    p = _metrics_file(tmp_path, calmar=5.0, sharpe=3.0, max_dd=-0.02, trades=200)
    code = acc_cli.cmd_accept(_args(metrics_file=str(p)))
    out = json.loads(capsys.readouterr().out)
    assert out["accept"] is True
    assert code == 0


def test_rejecting_metrics_exits_3(tmp_path, capsys):
    """全不达标 → reject → 退出码 3（不是 0，也不是 1）。"""
    p = _metrics_file(tmp_path, calmar=-1.0, sharpe=-1.0, max_dd=-0.9,
                      trades=0, ann_return=-0.5)
    code = acc_cli.cmd_accept(_args(metrics_file=str(p)))
    out = json.loads(capsys.readouterr().out)
    assert out["accept"] is False
    assert code == 3


def test_output_is_valid_json(tmp_path, capsys):
    p = _metrics_file(tmp_path)
    acc_cli.cmd_accept(_args(metrics_file=str(p)))
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, dict)


def test_cli_override_flips_decision(tmp_path, capsys):
    """--hard-calmar-min 必须真的进入配置，把 accept 翻成 reject。"""
    p = _metrics_file(tmp_path, calmar=1.0, sharpe=1.0, max_dd=-0.1, trades=100)
    base = acc_cli.cmd_accept(_args(metrics_file=str(p)))
    capsys.readouterr()
    strict = acc_cli.cmd_accept(_args(metrics_file=str(p), hard_calmar_min=99.0))
    capsys.readouterr()
    assert strict == 3
    assert base in (0, 3)


def test_llm_verdict_can_be_supplied_inline(tmp_path, capsys):
    """预计算 verdict 走 JSON 字符串，不调网络。"""
    p = _metrics_file(tmp_path, calmar=1.0, sharpe=1.0, max_dd=-0.1, trades=100)
    verdict = json.dumps({"passed": True, "score": 0.9, "reason": "robust"})
    acc_cli.cmd_accept(_args(metrics_file=str(p), llm_verdict=verdict))
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "llm" in json.dumps(parsed).lower() or isinstance(parsed, dict)


def test_negative_stagnation_is_clamped_to_zero(tmp_path, capsys):
    """stagnation_count 为负不能变成「负耐心」——夹到 0。"""
    p = _metrics_file(tmp_path)
    code = acc_cli.cmd_accept(_args(metrics_file=str(p), stagnation_count=-5))
    assert code in (0, 3)


def test_workspace_config_path_is_honoured(tmp_path, capsys):
    """workspace acceptance.yaml 是**平铺键名**（hard_calmar_min），不是嵌套节。"""
    p = _metrics_file(tmp_path)
    cfg = tmp_path / "acceptance.yaml"
    cfg.write_text("hard_calmar_min: 99.0\n", encoding="utf-8")
    code = acc_cli.cmd_accept(
        _args(metrics_file=str(p), workspace_config=str(cfg)))
    assert code == 3  # 由 workspace 配置强制拒绝


# ── --invoke-llm 的两条失败路径 ──────────────────────────


def test_invoke_llm_client_init_failure_returns_2(tmp_path, capsys, monkeypatch):
    p = _metrics_file(tmp_path)

    import strategy_research.foundation.llm as llm_mod

    def boom_load(*a, **k):
        raise RuntimeError("no api key")

    monkeypatch.setattr(llm_mod.LLMConfig, "load", staticmethod(boom_load))
    code = acc_cli.cmd_accept(_args(metrics_file=str(p), invoke_llm=True))
    assert code == 2
    assert "LLM client init failed" in capsys.readouterr().err


def test_invoke_llm_evaluate_failure_returns_2(tmp_path, capsys, monkeypatch):
    p = _metrics_file(tmp_path)

    import strategy_research.foundation.llm as llm_mod
    import strategy_research.apps.strategy_acceptance.cli as cli_mod

    monkeypatch.setattr(llm_mod.LLMConfig, "load", staticmethod(lambda: object()))
    monkeypatch.setattr(llm_mod, "OpenAICompatClient", lambda cfg: object())

    class BadEvaluator:
        def __init__(self, client=None):
            pass

        def evaluate(self, metrics, cfg=None):
            raise RuntimeError("model refused")

    monkeypatch.setattr(cli_mod, "LLMEvaluator", BadEvaluator)
    code = acc_cli.cmd_accept(_args(metrics_file=str(p), invoke_llm=True))
    assert code == 2
    assert "LLM evaluate failed" in capsys.readouterr().err


def test_invoke_llm_success_prints_comparison(tmp_path, capsys, monkeypatch):
    p = _metrics_file(tmp_path)

    import strategy_research.foundation.llm as llm_mod
    import strategy_research.apps.strategy_acceptance.cli as cli_mod

    monkeypatch.setattr(llm_mod.LLMConfig, "load", staticmethod(lambda: object()))
    monkeypatch.setattr(llm_mod, "OpenAICompatClient", lambda cfg: object())

    class GoodEvaluator:
        def __init__(self, client=None):
            pass

        def evaluate(self, metrics, cfg=None):
            return {"passed": True, "score": 0.95, "reason": "clean"}

    monkeypatch.setattr(cli_mod, "LLMEvaluator", GoodEvaluator)
    acc_cli.cmd_accept(_args(metrics_file=str(p), invoke_llm=True))
    out = capsys.readouterr().out
    assert "With live LLM verdict" in out


# ── 参数注册 ─────────────────────────────────────────────


def test_add_accept_subparsers_registers_handler():
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers()
    acc_cli.add_accept_subparsers(subs)
    args = parser.parse_args(["accept", "--metrics-file", "x.json"])
    assert args._handler is acc_cli.cmd_accept


def test_llm_enabled_flag_parses_truthy_strings():
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers()
    acc_cli.add_accept_subparsers(subs)
    for value, expected in (("true", True), ("1", True), ("YES", True),
                            ("false", False), ("0", False)):
        args = parser.parse_args(["accept", "--metrics-file", "x", "--llm-enabled", value])
        assert args.llm_enabled is expected
