"""H 档补测（P1-4）—— `apps/data_import.py` 的导入编排。

``import_data``（auto / 指定源）与 ``_import_with_loader``（增量更新、
空结果、部分成功）此前完全未覆盖：现有测试只打到了长格式校验与本地文件
两条纯函数路径。

这里的契约值得逐条锁：
- auto 模式必须**按市场分组**分别取数，一个市场失败不能拖垮其他市场；
- 增量模式下若所有资产都已最新，必须返回 True（而不是「0 个成功」= False）；
- loader 返回空 map 必须返回 False，不能假装成功；
- 成功计数只算真正写进去且非空的那些。

全部用假 loader 直接注入，不碰网络。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from strategy_research.apps import data_import as di


class FakeLoader:
    name = "fake"

    def __init__(self, data=None, raise_exc=None):
        self._data = data if data is not None else {}
        self._raise = raise_exc
        self.calls = []

    def fetch(self, codes, start, end):
        self.calls.append((list(codes), start, end))
        if self._raise:
            raise self._raise
        return {c: self._data[c] for c in codes if c in self._data}


def _frame(n=3) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1000.0},
        index=idx,
    )


@pytest.fixture
def ws(tmp_path) -> Path:
    d = tmp_path / "ws"
    (d / "strategies" / "s1").mkdir(parents=True)
    return d


def _patch_persist(monkeypatch, ok=True):
    """把落库与元数据更新换成记录器。"""
    saved = []
    monkeypatch.setattr(
        di, "save_ohlcv_data",
        lambda ws_, sn, code, df: (saved.append(code), ok)[1],
    )
    monkeypatch.setattr(
        di, "update_import_meta", lambda *a, **k: None)
    monkeypatch.setattr(di, "get_price_data_info", lambda *a, **k: {})
    return saved


# ── _import_with_loader：非增量 ───────────────────────────


def test_import_writes_each_non_empty_frame(ws, monkeypatch):
    saved = _patch_persist(monkeypatch)
    loader = FakeLoader({"A": _frame(), "B": _frame()})
    ok = di._import_with_loader(ws, "s1", loader, ["A", "B"], "2024-01-01", "2024-12-31", False)
    assert ok is True
    assert saved == ["A", "B"]


def test_import_skips_empty_and_none_frames(ws, monkeypatch):
    saved = _patch_persist(monkeypatch)
    empty = pd.DataFrame()
    loader = FakeLoader({"A": _frame(), "B": empty, "C": None})
    ok = di._import_with_loader(
        ws, "s1", loader, ["A", "B", "C"], "2024-01-01", "2024-12-31", False)
    assert ok is True
    assert saved == ["A"]


def test_import_returns_false_when_loader_returns_nothing(ws, monkeypatch):
    _patch_persist(monkeypatch)
    loader = FakeLoader({})
    assert di._import_with_loader(
        ws, "s1", loader, ["A"], "2024-01-01", "2024-12-31", False) is False


def test_import_returns_false_when_all_writes_fail(ws, monkeypatch):
    _patch_persist(monkeypatch, ok=False)
    loader = FakeLoader({"A": _frame()})
    assert di._import_with_loader(
        ws, "s1", loader, ["A"], "2024-01-01", "2024-12-31", False) is False


def test_import_passes_all_codes_in_non_incremental(ws, monkeypatch):
    _patch_persist(monkeypatch)
    loader = FakeLoader({"A": _frame(), "B": _frame()})
    di._import_with_loader(ws, "s1", loader, ["A", "B"], "2020-01-01", "2020-12-31", False)
    assert loader.calls[0][0] == ["A", "B"]
    assert loader.calls[0][1] == "2020-01-01"


# ── _import_with_loader：增量 ────────────────────────────


def test_incremental_uses_next_day_after_last_import(ws, monkeypatch):
    _patch_persist(monkeypatch)
    monkeypatch.setattr(
        di, "get_last_import_date",
        lambda ws_, sn, code: date(2024, 3, 10) if code == "A" else None,
    )
    loader = FakeLoader({"A": _frame(), "B": _frame()})
    di._import_with_loader(ws, "s1", loader, ["A", "B"], "2024-01-01", "2024-12-31", True)

    codes, fetch_start, _end = loader.calls[0]
    assert set(codes) == {"A", "B"}
    # 取所有资产里最早的增量起点 = B 的原始 start_date
    assert fetch_start == "2024-01-01"


def test_incremental_all_up_to_date_returns_true(ws, monkeypatch, capsys):
    """全部已最新时返回 True——「无需工作」不等于「失败」。"""
    _patch_persist(monkeypatch)
    monkeypatch.setattr(
        di, "get_last_import_date",
        lambda *a: date(2024, 12, 31),   # 晚于 end_date
    )
    loader = FakeLoader({"A": _frame()})
    ok = di._import_with_loader(ws, "s1", loader, ["A"], "2024-01-01", "2024-12-31", True)
    assert ok is True
    assert loader.calls == []  # 根本不该取数
    assert "已是最新" in capsys.readouterr().out


def test_incremental_skips_only_stale_codes(ws, monkeypatch):
    _patch_persist(monkeypatch)
    monkeypatch.setattr(
        di, "get_last_import_date",
        lambda ws_, sn, code: date(2024, 12, 31) if code == "A" else None,
    )
    loader = FakeLoader({"A": _frame(), "B": _frame()})
    di._import_with_loader(ws, "s1", loader, ["A", "B"], "2024-01-01", "2024-12-31", True)
    codes, _, _ = loader.calls[0]
    assert codes == ["B"]


# ── import_data：auto 模式按市场分组 ─────────────────────


def test_auto_mode_groups_by_market(ws, monkeypatch):
    """不同市场走不同 loader——混在一个 loader 里取会静默拿错数据。"""
    from strategy_research.core import data_source as ds

    monkeypatch.setattr(
        ds, "detect_market",
        lambda code: "a_share" if code.endswith(".SZ") else "us",
    )
    resolvers = {}

    class MarketLoader:
        def __init__(self, name):
            self.name = name
            self.fetched = []

        def fetch(self, codes, start, end):
            self.fetched.extend(codes)
            return {c: _frame() for c in codes}

    def fake_resolve(market):
        resolvers.setdefault(market, MarketLoader(market))
        return resolvers[market]

    monkeypatch.setattr(ds, "resolve_loader", fake_resolve)
    _patch_persist(monkeypatch)

    ok = di.import_from_source(
        ws, "s1", "auto", ["600000.SZ", "AAPL"], "2024-01-01", "2024-12-31",
        incremental=False)
    assert ok is True
    assert set(resolvers) == {"a_share", "us"}
    assert resolvers["a_share"].fetched == ["600000.SZ"]
    assert resolvers["us"].fetched == ["AAPL"]


def test_auto_mode_one_market_failure_does_not_kill_others(ws, monkeypatch):
    from strategy_research.core import data_source as ds

    monkeypatch.setattr(
        ds, "detect_market", lambda code: "bad" if code == "X" else "good")

    def fake_resolve(market):
        if market == "bad":
            raise ds.NoAvailableSourceError("no source")
        loader = FakeLoader({"GOOD": _frame()})
        return loader

    monkeypatch.setattr(ds, "resolve_loader", fake_resolve)
    _patch_persist(monkeypatch)

    ok = di.import_from_source(
        ws, "s1", "auto", ["X", "GOOD"], "2024-01-01", "2024-12-31",
        incremental=False)
    assert ok is False  # 有失败市场 → 整体 False
    # 但好市场的数据仍然写进去了
    assert di.get_price_data_info(ws, "s1") == {}


def test_explicit_source_uses_requested_loader(ws, monkeypatch):
    from strategy_research.core import data_source as ds

    monkeypatch.setattr(ds, "detect_market", lambda code: "a_share")
    loader = FakeLoader({"A": _frame()})
    monkeypatch.setattr(ds, "get_loader_or_fallback", lambda src: (lambda: loader))
    _patch_persist(monkeypatch)

    ok = di.import_from_source(
        ws, "s1", "tushare", ["A"], "2024-01-01", "2024-12-31",
        incremental=False)
    assert ok is True
    assert loader.calls


def test_explicit_source_unavailable_returns_false(ws, monkeypatch, capsys):
    from strategy_research.core import data_source as ds

    monkeypatch.setattr(ds, "detect_market", lambda code: "a_share")

    def boom(src):
        raise ds.NoAvailableSourceError("missing sdk")

    monkeypatch.setattr(ds, "get_loader_or_fallback", boom)
    assert di.import_from_source(
        ws, "s1", "ifind", ["A"], "2024-01-01", "2024-12-31",
        incremental=False) is False
    assert "不可用" in capsys.readouterr().out


def test_explicit_source_with_no_codes_defaults_to_a_share(ws, monkeypatch):
    """codes 为空时不能 IndexError——按 a_share 探测。"""
    from strategy_research.core import data_source as ds

    seen = {}
    monkeypatch.setattr(ds, "detect_market", lambda code: seen.setdefault("m", "a_share"))
    loader = FakeLoader({})
    monkeypatch.setattr(ds, "get_loader_or_fallback", lambda src: (lambda: loader))
    monkeypatch.setattr(ds, "resolve_loader", lambda m: loader)
    _patch_persist(monkeypatch)

    assert di.import_from_source(
        ws, "s1", "q", [], "2024-01-01", "2024-12-31",
        incremental=False) is False
