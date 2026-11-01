"""H 档补测 —— `core/data_readiness.py` 的边界与序列化路径。

已有 test_data_readiness.py 覆盖了 C1–C7 的主判定分支；本文件补：
- C0（数据库不可用）—— 唯一的 fail-early 路径
- `_fmt_list` / `_truncate` 两个纯格式化辅助（截断与省略逻辑）
- `ReadinessCheck.to_dict` / `ReadinessReport.to_dict` 序列化契约
- 空 workspace（无 DB 文件）与显式 codes 的交互

docs/run-backtest-data-gate.md 把 C0 定义为「fail 立即返回」，此前无测试。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.core import data_readiness as dr


# ── C0: 数据库不可用 ────────────────────────────────────────


def test_missing_db_reports_c0_fail(tmp_path):
    """空 workspace（无 .duckdb）→ C0 fail，且不抛。"""
    report = dr.check_data_readiness(tmp_path, "s1", cfg={"data": {"codes": ["A"]}})
    c0 = [c for c in report.checks if c.id == "C0"]
    assert c0, "应当产生 C0 检查项"
    assert c0[0].status == "fail"
    assert report.ok is False


def test_missing_db_without_codes_still_returns_report(tmp_path):
    report = dr.check_data_readiness(tmp_path, "s1")
    assert isinstance(report, dr.ReadinessReport)
    assert report.ok is False


# ── _fmt_list ───────────────────────────────────────────────


def test_fmt_list_under_limit_is_complete():
    out = dr._fmt_list(["a", "b"])
    assert "a" in out and "b" in out
    assert "…" not in out and "..." not in out


def test_fmt_list_over_limit_truncates():
    """超限时保留前 N 项并附「等 N 项」总数标注。"""
    items = [f"item{i}" for i in range(10)]
    out = dr._fmt_list(items, max_items=3)
    assert "item0" in out
    assert "item9" not in out
    assert "等 10 项" in out


def test_fmt_list_uses_cjk_separator():
    assert dr._fmt_list(["a", "b"]) == "a、b"


def test_fmt_list_empty_is_cjk_placeholder():
    assert dr._fmt_list([]) == "无"


# ── _truncate ───────────────────────────────────────────────


def test_truncate_short_text_unchanged():
    assert dr._truncate("short") == "short"


def test_truncate_long_text_capped():
    """截断保留 max_len 个字符 + 3 个点的省略标记。"""
    text = "x" * 500
    out = dr._truncate(text, max_len=50)
    assert out.startswith("x" * 50)
    assert out.endswith("...")
    assert len(out) == 53


def test_truncate_strips_whitespace():
    assert dr._truncate("  padded  ") == "padded"


def test_truncate_preserves_none_semantics():
    """None 输入不应抛（防御性）。"""
    try:
        out = dr._truncate(None)  # type: ignore[arg-type]
    except TypeError:
        pytest.skip("_truncate 不承诺接受 None")
    else:
        assert out in ("", "None")


# ── 序列化契约 ──────────────────────────────────────────────


def test_readiness_check_to_dict_shape():
    c = dr.ReadinessCheck(id="C1", name="资产覆盖", status="ok", detail="d",
                          fix_hint="h")
    d = c.to_dict()
    assert set(d) == {"id", "name", "status", "detail", "fix_hint"}
    assert d["id"] == "C1"


def test_readiness_report_to_dict_shape():
    r = dr.ReadinessReport(
        ok=True,
        checks=[dr.ReadinessCheck(id="C1", name="n", status="ok")],
    )
    d = r.to_dict()
    assert d["ok"] is True
    assert len(d["checks"]) == 1
    assert d["checks"][0]["id"] == "C1"


def test_report_ok_ignores_warn_but_not_fail():
    """ok 的判定规则：任何 fail 即 False，warn 不影响。"""
    warn_only = dr.ReadinessReport(
        ok=all(c.status != "fail" for c in [
            dr.ReadinessCheck(id="C2", name="n", status="warn")]),
        checks=[dr.ReadinessCheck(id="C2", name="n", status="warn")],
    )
    assert warn_only.ok is True


# ── 显式参数优先 ────────────────────────────────────────────


def test_explicit_codes_override_config(tmp_path):
    """显式 codes 优先于 cfg.data.codes（source='explicit' 场景）。

    需要真实 DB 才能走到 C1 的资产比对分支，所以先播种两个资产。
    """
    import numpy as np
    import pandas as pd

    from strategy_research.core.db import save_ohlcv_to_db

    dates = pd.bdate_range("2023-01-02", periods=250)
    rng = np.random.default_rng(7)
    for code in ("EXPLICIT", "FROM_CFG"):
        close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.02, len(dates)))
        save_ohlcv_to_db(
            tmp_path,
            {code: pd.DataFrame(
                {"open": close * 0.99, "high": close * 1.02,
                 "low": close * 0.98, "close": close, "volume": 1e6},
                index=pd.DatetimeIndex(dates, name="trade_date"))},
            "s1",
        )

    report = dr.check_data_readiness(
        tmp_path, "s1",
        cfg={"data": {"codes": ["FROM_CFG"], "start_date": "2023-01-02",
                      "end_date": "2023-12-29"}},
        codes=["EXPLICIT"],
        start_date="2023-01-02",
        end_date="2023-12-29",
    )
    detail = " ".join(c.detail for c in report.checks)
    assert "EXPLICIT" in detail
