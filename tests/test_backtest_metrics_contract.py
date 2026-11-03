"""结构化指标契约(SR_RUN_DIR / metrics_raw.json)回归测试。

信任模型:LLM 生成的 strategy.py 可以打印任意数字,run.log 正则解析
不能作为验收指标的唯一来源。框架拥有的 prepare.py 在子进程内把
evaluate() 结果写入 <SR_RUN_DIR>/metrics_raw.json;框架以契约为第一
来源,run.log 仅作降级 + 交叉校验,不一致即 fail-closed。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
# W1: root templates/ copy removed — use the packaged templates tree.
TEMPLATES = REPO / "src" / "strategy_research" / "templates"


def _load_prepare():
    spec = importlib.util.spec_from_file_location(
        "prepare_metrics_contract_fixture", TEMPLATES / "prepare.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── prepare.save_metrics(契约写入端) ──────────────────────────


class TestSaveMetrics:
    def test_writes_contract_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SR_RUN_DIR", str(tmp_path))
        mod = _load_prepare()
        assert mod.save_metrics({"calmar": 1.5, "sharpe": 0.8, "trades": 42}) is True
        data = json.loads((tmp_path / "metrics_raw.json").read_text(encoding="utf-8"))
        assert data["protocol"] == 1
        assert data["metrics"]["calmar"] == 1.5
        assert data["metrics"]["trades"] == 42.0

    def test_no_env_returns_false(self, monkeypatch):
        monkeypatch.delenv("SR_RUN_DIR", raising=False)
        mod = _load_prepare()
        assert mod.save_metrics({"calmar": 1.0}) is False

    def test_rejects_nonfinite(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SR_RUN_DIR", str(tmp_path))
        mod = _load_prepare()
        with pytest.raises(ValueError):
            mod.save_metrics({"calmar": float("inf")})
        with pytest.raises(ValueError):
            mod.save_metrics({"calmar": float("nan")})
        assert not (tmp_path / "metrics_raw.json").exists()

    def test_skips_non_numeric_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SR_RUN_DIR", str(tmp_path))
        mod = _load_prepare()
        mod.save_metrics({
            "calmar": 1.0,
            "note": "随便写的字符串",       # 非数值 → 跳过,不进契约
            "ok": True,                    # bool 也是 int → 必须排除
        })
        data = json.loads((tmp_path / "metrics_raw.json").read_text(encoding="utf-8"))
        assert set(data["metrics"]) == {"calmar"}


# ── backtest.read_metrics_contract(框架读取端) ────────────────


class TestReadMetricsContract:
    def test_missing_file_returns_none(self, tmp_path):
        from strategy_research.apps.backtest import read_metrics_contract
        assert read_metrics_contract(tmp_path / "absent.json") is None

    def test_valid_contract_roundtrip(self, tmp_path):
        from strategy_research.apps.backtest import read_metrics_contract
        p = tmp_path / "metrics_raw.json"
        p.write_text(json.dumps(
            {"protocol": 1, "metrics": {"calmar": 1.5, "trades": 42}}),
            encoding="utf-8")
        out = read_metrics_contract(p)
        assert out == {"calmar": 1.5, "trades": 42.0}

    def test_rejects_nonfinite(self, tmp_path):
        from strategy_research.apps.backtest import read_metrics_contract
        p = tmp_path / "metrics_raw.json"
        p.write_text(
            '{"protocol": 1, "metrics": {"calmar": Infinity}}', encoding="utf-8")
        assert read_metrics_contract(p) is None

    def test_rejects_unknown_protocol(self, tmp_path):
        from strategy_research.apps.backtest import read_metrics_contract
        p = tmp_path / "metrics_raw.json"
        p.write_text(json.dumps(
            {"protocol": 2, "metrics": {"calmar": 1.0}}), encoding="utf-8")
        assert read_metrics_contract(p) is None

    def test_rejects_non_numeric_metrics(self, tmp_path):
        from strategy_research.apps.backtest import read_metrics_contract
        p = tmp_path / "metrics_raw.json"
        p.write_text(json.dumps(
            {"protocol": 1, "metrics": {"calmar": "伪造的文本"}}), encoding="utf-8")
        assert read_metrics_contract(p) is None


class TestCrossCheck:
    def test_same_source_passes(self):
        from strategy_research.apps.backtest import _cross_check_contract
        assert _cross_check_contract(
            {"calmar": 1.234567}, {"calmar": 1.234567}) == []

    def test_print_rounding_within_tolerance(self):
        from strategy_research.apps.backtest import _cross_check_contract
        # run.log 打印 %.6f:1.2345678 → "1.234568"
        assert _cross_check_contract(
            {"calmar": 1.2345678}, {"calmar": 1.234568}) == []

    def test_mismatch_detected(self):
        from strategy_research.apps.backtest import _cross_check_contract
        problems = _cross_check_contract(
            {"calmar": 1.0}, {"calmar": 9.9})
        assert len(problems) == 1
        assert "calmar" in problems[0]


# ── run_backtest_script 集成 ─────────────────────────────────


@pytest.fixture
def script_workspace(tmp_path, monkeypatch):
    """带模板 strategy.py/prepare.py 的工作区,DuckDB/git 已 mock。"""
    from strategy_research.apps import backtest as bt_module

    monkeypatch.setattr(bt_module, "save_backtest_result", lambda **kw: None)
    monkeypatch.setattr(bt_module, "git_get_hash", lambda *a, **kw: "abc1234")

    strat = tmp_path / "strategies" / "demo"
    strat.mkdir(parents=True)
    (strat / "strategy.py").write_text(
        (TEMPLATES / "strategy.py").read_text(encoding="utf-8"), encoding="utf-8")
    (strat / "prepare.py").write_text(
        (TEMPLATES / "prepare.py").read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path, strat, bt_module


def test_run_uses_contract_as_primary_source(script_workspace):
    """模板策略执行后,指标来自契约文件(无数据 → _empty_metrics 全 0)。"""
    tmp_ws, strat, bt = script_workspace
    result = bt.run_backtest_script(tmp_ws, "demo", action="test")
    assert result["success"] is True
    assert result["metrics"]["metrics_source"] == "contract"
    run_dir = strat / "runs" / "run_0001"
    assert (run_dir / "metrics_raw.json").exists()
    # _empty_metrics 全 0 且诚实进入验收链
    assert result["metrics"]["calmar"] == 0.0


def test_run_falls_back_to_run_log_for_legacy(script_workspace):
    """无契约文件的旧策略(只打印)→ 降级到 run.log 正则。"""
    tmp_ws, strat, bt = script_workspace
    (strat / "strategy.py").write_text(
        "print('calmar: 1.0')\nprint('sharpe: 0.5')\n", encoding="utf-8")
    result = bt.run_backtest_script(tmp_ws, "demo", action="test")
    assert result["success"] is True
    assert result["metrics"]["metrics_source"] == "run.log"
    assert result["metrics"]["calmar"] == 1.0


def test_run_fails_closed_on_contract_log_mismatch(script_workspace, monkeypatch):
    """契约与 run.log 不一致 → 整个 run 失败,不落任何结果。"""
    tmp_ws, strat, bt = script_workspace
    (strat / "strategy.py").write_text(
        "print('calmar: 9.9')\nprint('sharpe: 0.5')\n", encoding="utf-8")

    # 模拟:子进程写出的契约说 calmar=1.0,但日志里打印了 9.9
    orig_run_strategy = bt.run_strategy
    def fake_run_strategy(strategy_dir, timeout=300, log_path=None, env_overrides=None):
        run_dir = Path(env_overrides["SR_RUN_DIR"])
        (run_dir / "metrics_raw.json").write_text(json.dumps(
            {"protocol": 1, "metrics": {"calmar": 1.0, "sharpe": 0.5}}),
            encoding="utf-8")
        log_path.write_text("calmar: 9.9\nsharpe: 0.5\n", encoding="utf-8")
        return True, ""
    monkeypatch.setattr(bt, "run_strategy", fake_run_strategy)

    result = bt.run_backtest_script(tmp_ws, "demo", action="test")
    assert result["success"] is False
    assert "完整性校验失败" in result["error"]
    assert result["metrics"] == {}
    # fail-closed:不落 metrics.json / results.tsv / DuckDB 行
    run_dir = strat / "runs" / "run_0001"
    assert not (run_dir / "metrics.json").exists()
    assert not (strat / "runs" / "results.tsv").exists()
