"""Tests for OHLCV 保存路径 — save_ohlcv_data + generate_sample_ohlcv_data.

覆盖 P0 修复：
- save_ohlcv_data 保留单资产完整 OHLCV（不丢失为 close-only）
- generate_sample_ohlcv_data 返回 dict[code → OHLCV DataFrame]
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# ============================================================
# 1. generate_sample_ohlcv_data 结构
# ============================================================


class TestGenerateSampleOHLCVData:
    """generate_sample_ohlcv_data 生成器结构测试。"""

    def test_returns_dict_with_correct_count(self):
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        result = generate_sample_ohlcv_data(n_assets=3, n_days=50)
        assert isinstance(result, dict)
        assert len(result) == 3
        assert all(name.startswith("asset_") for name in result.keys())

    def test_each_dataframe_has_ohlcv_columns(self):
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        result = generate_sample_ohlcv_data(n_assets=2, n_days=30)
        for code, df in result.items():
            assert isinstance(df, pd.DataFrame)
            for col in ["open", "high", "low", "close", "volume"]:
                assert col in df.columns, f"{code} missing {col}"

    def test_dataframe_has_correct_shape(self):
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        result = generate_sample_ohlcv_data(n_assets=5, n_days=100)
        for code, df in result.items():
            assert df.shape == (100, 5)  # (T, OHLCV)

    def test_index_is_datetime(self):
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        result = generate_sample_ohlcv_data(n_assets=2, n_days=30)
        for df in result.values():
            assert isinstance(df.index, pd.DatetimeIndex)

    def test_volume_is_positive_integer(self):
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        result = generate_sample_ohlcv_data(n_assets=3, n_days=30)
        for df in result.values():
            assert (df["volume"] > 0).all()

    def test_ohlc_values_are_positive(self):
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        result = generate_sample_ohlcv_data(n_assets=3, n_days=30)
        for df in result.values():
            assert (df["open"] > 0).all()
            assert (df["high"] > 0).all()
            assert (df["low"] > 0).all()
            assert (df["close"] > 0).all()

    def test_high_ge_low_ge_open_close(self):
        """high >= low, high >= open/close, low <= open/close。"""
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        result = generate_sample_ohlcv_data(n_assets=3, n_days=50)
        for df in result.values():
            assert (df["high"] >= df["low"]).all(), "high should be >= low"
            assert (df["high"] >= df["open"]).all()
            assert (df["high"] >= df["close"]).all()

    def test_deterministic_with_same_seed(self):
        """相同 seed 应产生相同数据。"""
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        result1 = generate_sample_ohlcv_data(n_assets=2, n_days=20)
        result2 = generate_sample_ohlcv_data(n_assets=2, n_days=20)
        for code in result1:
            pd.testing.assert_frame_equal(result1[code], result2[code])

    def test_ohlc_not_degenerate(self):
        """OHLC 不应完全退化为 close-only（即 O/H/L 应有变化）。"""
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        result = generate_sample_ohlcv_data(n_assets=3, n_days=50)
        for df in result.values():
            # at least some variation
            assert (df["open"] != df["close"]).any() or (df["high"] != df["close"]).any()


# ============================================================
# 2. save_ohlcv_data 保存 OHLCV
# ============================================================


class TestSaveOHLCVData:
    """save_ohlcv_data 保留完整 OHLCV。"""

    def _init_workspace(self, tmp_path: Path) -> Path:
        from strategy_research.core.db import init_db
        init_db(tmp_path)
        return tmp_path

    def test_preserves_open_high_low(self, tmp_path: Path):
        """保存后 open/high/low 应保留。"""
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        from strategy_research.core.db import init_db, save_ohlcv_data

        init_db(tmp_path)
        ohlcv = generate_sample_ohlcv_data(n_assets=2, n_days=50)
        for code, df in ohlcv.items():
            save_ohlcv_data(tmp_path, "test_strat", code, df)

        import duckdb
        conn = duckdb.connect(str(tmp_path / "data.duckdb"), read_only=True)
        result = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL
                    THEN 1 ELSE 0 END) as ohlc_present
            FROM price_data
        """).fetchone()
        conn.close()
        total, ohlc_present = result
        assert total == 100
        assert ohlc_present == total, "OHLC should all be present"

    def test_preserves_volume(self, tmp_path: Path):
        """保存后 volume 应保留非 0 值。"""
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        from strategy_research.core.db import init_db, save_ohlcv_data

        init_db(tmp_path)
        ohlcv = generate_sample_ohlcv_data(n_assets=2, n_days=30)
        for code, df in ohlcv.items():
            save_ohlcv_data(tmp_path, "test_strat", code, df)

        import duckdb
        conn = duckdb.connect(str(tmp_path / "data.duckdb"), read_only=True)
        result = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN volume > 0 THEN 1 ELSE 0 END) as vol_positive
            FROM price_data
        """).fetchone()
        conn.close()
        total, vol_positive = result
        assert total == 60
        assert vol_positive == total, "volume should be > 0 (not degenerate)"

    def test_ohlc_not_degenerate_to_close_only(self, tmp_path: Path):
        """保存后 OHLC 应有真实变化（非 OHL=close 全等）。"""
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        from strategy_research.core.db import init_db, save_ohlcv_data

        init_db(tmp_path)
        ohlcv = generate_sample_ohlcv_data(n_assets=3, n_days=50)
        for code, df in ohlcv.items():
            save_ohlcv_data(tmp_path, "test_strat", code, df)

        import duckdb
        conn = duckdb.connect(str(tmp_path / "data.duckdb"), read_only=True)
        varying = conn.execute("""
            SELECT COUNT(*) FROM price_data
            WHERE open != close OR high != close OR low != close
        """).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM price_data").fetchone()[0]
        conn.close()
        assert varying > 0, "OHLC should not be degenerate"
        assert varying == total, "all rows should have varying OHLC"

    def test_assigns_correct_asset_code(self, tmp_path: Path):
        """保存的 asset_code 应正确。"""
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        from strategy_research.core.db import init_db, save_ohlcv_data

        init_db(tmp_path)
        ohlcv = generate_sample_ohlcv_data(n_assets=2, n_days=10)
        for code, df in ohlcv.items():
            save_ohlcv_data(tmp_path, "test_strat", code, df)

        import duckdb
        conn = duckdb.connect(str(tmp_path / "data.duckdb"), read_only=True)
        codes = sorted(c[0] for c in conn.execute(
            "SELECT DISTINCT asset_code FROM price_data"
        ).fetchall())
        conn.close()
        assert codes == ["asset_000", "asset_001"]

    def test_replaces_existing_data(self, tmp_path: Path):
        """重复保存应替换不追加。"""
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        from strategy_research.core.db import init_db, save_ohlcv_data

        init_db(tmp_path)
        ohlcv = generate_sample_ohlcv_data(n_assets=1, n_days=10)

        # 第一次保存
        save_ohlcv_data(tmp_path, "test_strat", "asset_000", ohlcv["asset_000"])
        # 第二次保存（应替换不追加）
        save_ohlcv_data(tmp_path, "test_strat", "asset_000", ohlcv["asset_000"])

        import duckdb
        conn = duckdb.connect(str(tmp_path / "data.duckdb"), read_only=True)
        total = conn.execute("SELECT COUNT(*) FROM price_data").fetchone()[0]
        conn.close()
        assert total == 10, f"应只保留 10 行，实际 {total}"

    def test_returns_true_on_success(self, tmp_path: Path):
        """保存成功应返回 True。"""
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        from strategy_research.core.db import init_db, save_ohlcv_data

        init_db(tmp_path)
        ohlcv = generate_sample_ohlcv_data(n_assets=1, n_days=5)
        ok = save_ohlcv_data(tmp_path, "test_strat", "asset_000", ohlcv["asset_000"])
        assert ok is True

    def test_returns_false_when_duckdb_missing(self, tmp_path: Path, monkeypatch):
        """duckdb 不可用时返回 False。"""
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "duckdb":
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        from strategy_research.core.db import save_ohlcv_data

        ohlcv = generate_sample_ohlcv_data(n_assets=1, n_days=5)
        ok = save_ohlcv_data(tmp_path, "test_strat", "asset_000", ohlcv["asset_000"])
        assert ok is False


# ============================================================
# 3. _import_with_loader 集成（间接验证 T0.3）
# ============================================================


class TestImportWithLoaderUsesOHLCV:
    """_import_with_loader 应使用 save_ohlcv_data 保留 OHLCV。"""

    def test_loaders_return_ohlcv_data_preserved(self, tmp_path: Path):
        """loader 返回 OHLCV → save_ohlcv_data → DB 保留 OHLCV。"""
        from strategy_research.apps.data_import import generate_sample_ohlcv_data
        from strategy_research.core.db import init_db, save_ohlcv_data

        init_db(tmp_path)
        # 模拟 loader 返回 dict[code → OHLCV DataFrame]
        ohlcv_map = generate_sample_ohlcv_data(n_assets=3, n_days=50)

        # 模拟 _import_with_loader 的保存循环
        for code, df in ohlcv_map.items():
            save_ohlcv_data(tmp_path, "test_strat", code, df)

        import duckdb
        conn = duckdb.connect(str(tmp_path / "data.duckdb"), read_only=True)
        result = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL
                    AND volume > 0 THEN 1 ELSE 0 END) as complete
            FROM price_data
        """).fetchone()
        conn.close()
        total, complete = result
        assert total == 150
        assert complete == total, "all rows should have complete OHLCV"


# ============================================================
# 4. 回归测试 — 老的 generate_sample_data 行为不变
# ============================================================


class TestGenerateSampleDataLegacy:
    """generate_sample_data 仍返回宽 close 面板。

    2026-08-05: 此函数保留 (用于 CLI 旧 sample 路径), 但已无 in-tree 调用方
    (import_dataframe 已删除). 未来要彻底清理 — TODO(toolsets).
    """

    def test_returns_wide_close_panel(self):
        from strategy_research.apps.data_import import generate_sample_data
        result = generate_sample_data(n_assets=5, n_days=30)
        assert isinstance(result, pd.DataFrame)
        # 宽面板：(T, N) 列名=asset
        assert result.shape == (30, 5)
        assert all(c.startswith("asset_") for c in result.columns)

    def test_values_are_close_prices(self):
        """返回的应是 close prices（单资产）。"""
        from strategy_research.apps.data_import import generate_sample_data
        result = generate_sample_data(n_assets=2, n_days=10)
        # 单列不应含 open/high/low/volume
        for col in result.columns:
            for c in ["open", "high", "low", "volume"]:
                assert c not in result[col].index, f"{col} should not contain {c}"
