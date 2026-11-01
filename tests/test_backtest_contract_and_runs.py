"""H 档补测 —— `core/backtest.py` 的指标契约与 run 目录管理。

这个文件此前 47%，缺口集中在一组**纯函数**上。它们承载的是信任边界
（见 backtest.py:105-115 的信任模型注释）：LLM 生成的 strategy.py 能
打印任意数字，所以验收指标以框架写出的结构化契约文件为第一来源，
run.log 的正则只作降级与交叉校验。

这些逻辑全是本地读写，没有回测引擎、没有子进程、没有网络——此前没测
不是因为难测，是因为被「回测=重活」的印象挡住了。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core import backtest as bt


# ── parse_run_log ───────────────────────────────────────────


def test_parse_run_log_missing_file_returns_empty(tmp_path):
    assert bt.parse_run_log(tmp_path / "nope.log") == {}


def test_parse_run_log_extracts_known_metrics(tmp_path):
    """用 METRIC_PATTERNS 里真实存在的格式（见模块常量），而非臆测措辞。"""
    log = tmp_path / "run.log"
    # 从模块自身的模式表取第一个 pattern 的样例，保证与实现同步
    pattern, name, _conv = bt.METRIC_PATTERNS[0]
    import re as _re

    probe = None
    for cand in ("Calmar: 1.23", "calmar 1.23", "Calmar Ratio: 1.23"):
        if _re.match(pattern, cand, _re.IGNORECASE):
            probe = cand
            break
    assert probe is not None, f"无法为模式 {pattern!r} 构造样例"

    log.write_text(probe + "\n", encoding="utf-8")
    metrics = bt.parse_run_log(log)
    assert name in metrics
    assert isinstance(metrics[name], (int, float))


def test_parse_run_log_ignores_unparseable_lines(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("随便一行\n=== 分隔线 ===\n", encoding="utf-8")
    assert bt.parse_run_log(log) == {}


# ── read_metrics_contract（信任边界） ───────────────────────


def test_contract_missing_file_returns_none(tmp_path):
    assert bt.read_metrics_contract(tmp_path / "metrics_raw.json") is None


def test_contract_happy_path(tmp_path):
    p = tmp_path / "metrics_raw.json"
    p.write_text(json.dumps({"protocol": 1,
                             "metrics": {"sharpe": 1.2, "calmar": 0.9}}),
                 encoding="utf-8")
    out = bt.read_metrics_contract(p)
    assert out == {"sharpe": 1.2, "calmar": 0.9}
    assert all(isinstance(v, float) for v in out.values())


def test_contract_rejects_unknown_protocol(tmp_path):
    p = tmp_path / "metrics_raw.json"
    p.write_text(json.dumps({"protocol": 99, "metrics": {"a": 1}}),
                 encoding="utf-8")
    assert bt.read_metrics_contract(p) is None


def test_contract_rejects_missing_metrics_dict(tmp_path):
    p = tmp_path / "metrics_raw.json"
    p.write_text(json.dumps({"protocol": 1, "metrics": "not a dict"}),
                 encoding="utf-8")
    assert bt.read_metrics_contract(p) is None


def test_contract_rejects_non_numeric_values(tmp_path):
    """LLM 可能写出字符串「N/A」——契约必须拒绝而不是猜测。"""
    p = tmp_path / "metrics_raw.json"
    p.write_text(json.dumps({"protocol": 1,
                             "metrics": {"sharpe": "N/A"}}),
                 encoding="utf-8")
    assert bt.read_metrics_contract(p) is None


def test_contract_rejects_bool_values(tmp_path):
    """bool 是 int 的子类，但把 True 当指标值是错的。"""
    p = tmp_path / "metrics_raw.json"
    p.write_text(json.dumps({"protocol": 1, "metrics": {"flag": True}}),
                 encoding="utf-8")
    assert bt.read_metrics_contract(p) is None


def test_contract_rejects_nan_and_infinity(tmp_path):
    """非有限值一律拒绝（parse_constant 钩子）。"""
    for token in ("NaN", "Infinity", "-Infinity"):
        p = tmp_path / "metrics_raw.json"
        p.write_text('{"protocol": 1, "metrics": {"x": %s}}' % token,
                     encoding="utf-8")
        assert bt.read_metrics_contract(p) is None, token


def test_contract_rejects_corrupt_json(tmp_path):
    p = tmp_path / "metrics_raw.json"
    p.write_text("{broken", encoding="utf-8")
    assert bt.read_metrics_contract(p) is None


# ── _cross_check_contract ───────────────────────────────────


def test_cross_check_passes_within_tolerance():
    assert bt._cross_check_contract({"sharpe": 1.2}, {"sharpe": 1.2}) == []


def test_cross_check_flags_divergence():
    problems = bt._cross_check_contract({"sharpe": 1.2}, {"sharpe": 9.9})
    assert problems and "sharpe" in problems[0]


def test_cross_check_ignores_keys_absent_from_contract():
    """run.log 多打印的指标不参与校验（契约是来源）。"""
    assert bt._cross_check_contract({"sharpe": 1.2},
                                    {"sharpe": 1.2, "extra": 5.0}) == []


def test_cross_check_tolerates_non_numeric_log_value():
    assert bt._cross_check_contract({"a": 1.0}, {"a": "garbage"}) == []


def test_cross_check_tolerance_scales_with_magnitude():
    """容差是相对值：小数值严格，大数值宽松。"""
    assert bt._cross_check_contract({"x": 1.0}, {"x": 1.0 + 1e-9}) == []
    assert bt._cross_check_contract({"x": 1e6}, {"x": 1e6 + 1}) == []


# ── run 目录管理 ────────────────────────────────────────────


def test_next_run_name_on_fresh_strategy(tmp_path):
    assert bt.get_next_run_name(tmp_path) == "run_0001"


def test_next_run_name_increments(tmp_path):
    (tmp_path / "runs" / "run_0001").mkdir(parents=True)
    assert bt.get_next_run_name(tmp_path) == "run_0002"


def test_next_run_name_takes_max_plus_one(tmp_path):
    for n in (1, 3, 7):
        (tmp_path / "runs" / f"run_{n:04d}").mkdir(parents=True)
    assert bt.get_next_run_name(tmp_path) == "run_0008"


def test_next_run_name_ignores_unparseable_dirs(tmp_path):
    (tmp_path / "runs" / "run_0002").mkdir(parents=True)
    (tmp_path / "runs" / "run_abc").mkdir(parents=True)
    (tmp_path / "runs" / "notes.txt").write_text("x", encoding="utf-8")
    assert bt.get_next_run_name(tmp_path) == "run_0003"


def test_create_run_dir_is_idempotent(tmp_path):
    a = bt.create_run_dir(tmp_path, "run_0001")
    b = bt.create_run_dir(tmp_path, "run_0001")
    assert a == b and a.is_dir()


# ── _clean_nan / save_run_metrics ───────────────────────────


def test_clean_nan_handles_nan_and_inf():
    assert bt._clean_nan(float("nan")) is None
    assert bt._clean_nan(float("inf")) is None
    assert bt._clean_nan(1.5) == 1.5


def test_clean_nan_passes_through_non_numeric():
    assert bt._clean_nan("text") == "text"
    assert bt._clean_nan(None) is None


def test_save_run_metrics_writes_json(tmp_path):
    bt.save_run_metrics(tmp_path, {"sharpe": 1.2, "nan_metric": float("nan")})
    out = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert out["sharpe"] == 1.2
    assert out["nan_metric"] is None      # NaN 不能进 JSON


# ── update_results_tsv ──────────────────────────────────────


def test_update_results_tsv_creates_header_then_appends(tmp_path):
    """第一个参数是 strategy_dir；文件落在 <dir>/runs/results.tsv。"""
    bt.update_results_tsv(tmp_path, "run_0001", {"sharpe": 1.2, "calmar": 0.8})
    tsv = tmp_path / "runs" / "results.tsv"
    assert tsv.exists()
    assert len(tsv.read_text(encoding="utf-8").strip().splitlines()) == 2

    bt.update_results_tsv(tmp_path, "run_0002", {"sharpe": 0.9, "calmar": 0.7})
    assert len(tsv.read_text(encoding="utf-8").strip().splitlines()) == 3


def test_update_results_tsv_schema_is_fixed(tmp_path):
    """列由函数写死（v2 契约），不随传入 metrics 变化。

    关键约束（见 docstring）：round 列固定在最后（index 13），
    以保证 calmar=3 / status=11 等既有列号不漂移。
    """
    bt.update_results_tsv(tmp_path, "run_0001", {"sharpe": 1.0})
    bt.update_results_tsv(tmp_path, "run_0002", {"totally_new_key": 9.0})
    header = (tmp_path / "runs" / "results.tsv").read_text(
        encoding="utf-8").splitlines()[0].split("\t")
    assert header[0] == "run"
    assert header[3] == "calmar"
    assert header[11] == "status"
    assert header[13] == "round"
    assert "totally_new_key" not in header


def test_update_results_tsv_round_column(tmp_path):
    bt.update_results_tsv(tmp_path, "run_0001", {}, round_num=3)
    row = (tmp_path / "runs" / "results.tsv").read_text(
        encoding="utf-8").splitlines()[1].split("\t")
    assert row[13] == "3"


# ── _extract_warnings ───────────────────────────────────────


def test_extract_warnings_returns_list():
    """契约：返回 list[str]（内容来源随实现而定，但类型稳定）。"""
    out = bt._extract_warnings({"warnings": ["w1", "w2"]}, "")
    assert isinstance(out, list)
    assert all(isinstance(x, str) for x in out)


def test_extract_warnings_handles_empty_input():
    assert bt._extract_warnings({}, "") == []
