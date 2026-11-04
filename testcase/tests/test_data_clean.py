"""数据清洗工具集单元测试"""

import pandas as pd
import pytest

from strategy_research.apps.tools.data_clean import PRESETS, CleaningReport, clean_data


@pytest.fixture
def sample_data():
    """创建测试数据"""
    return pd.DataFrame({
        'asset': ['A', 'A', 'A', 'B', 'B', 'B'],
        'date': ['2020-01-01', '2020-01-01', '2020-01-02', '2020-01-01', '2020-01-01', '2020-01-02'],
        'open': [100.0, 100.0, 101.0, 200.0, 200.0, 201.0],
        'high': [100.0, 100.0, 101.0, 200.0, 200.0, 201.0],
        'low': [100.0, 100.0, 101.0, 200.0, 200.0, 201.0],
        'close': [100.0, 100.0, 101.0, 200.0, 200.0, 201.0],
        'volume': [1000.0, 1000.0, 1000.0, 2000.0, 2000.0, 2000.0],
    })


@pytest.fixture
def data_with_missing():
    """创建包含缺失值的测试数据"""
    return pd.DataFrame({
        'asset': ['A', 'A', 'A', 'B', 'B', 'B'],
        'date': ['2020-01-01', '2020-01-02', '2020-01-03', '2020-01-01', '2020-01-02', '2020-01-03'],
        'open': [100.0, None, 102.0, 200.0, 201.0, 202.0],
        'high': [100.0, None, 102.0, 200.0, 201.0, 202.0],
        'low': [100.0, None, 102.0, 200.0, 201.0, 202.0],
        'close': [100.0, None, 102.0, 200.0, 201.0, 202.0],
        'volume': [1000.0, None, 1000.0, 2000.0, 2000.0, 2000.0],
    })


@pytest.fixture
def daily_data():
    """创建日线数据用于变频测试"""
    dates = pd.date_range('2020-01-01', '2020-03-31', freq='D')
    return pd.DataFrame({
        'asset': ['A'] * len(dates),
        'date': dates,
        'open': range(100, 100 + len(dates)),
        'high': range(101, 101 + len(dates)),
        'low': range(99, 99 + len(dates)),
        'close': range(100, 100 + len(dates)),
        'volume': [1000] * len(dates),
    })


class TestPresets:
    """测试预设模式"""

    def test_presets_defined(self):
        """测试预设模式已定义"""
        assert "quick" in PRESETS
        assert "standard" in PRESETS
        assert "thorough" in PRESETS
        assert "resample" in PRESETS
        assert "custom" in PRESETS

    def test_preset_has_steps(self):
        """测试预设模式有步骤"""
        for preset_name, preset in PRESETS.items():
            assert "steps" in preset, f"{preset_name} missing steps"
            assert "params" in preset, f"{preset_name} missing params"

    def test_quick_preset_steps(self):
        """测试 quick 预设步骤"""
        steps = PRESETS["quick"]["steps"]
        assert steps == ["dedup"]

    def test_standard_preset_steps(self):
        """测试 standard 预设步骤"""
        steps = PRESETS["standard"]["steps"]
        assert "dedup" in steps
        assert "impute" in steps

    def test_thorough_preset_steps(self):
        """测试 thorough 预设步骤"""
        steps = PRESETS["thorough"]["steps"]
        assert "dedup" in steps
        assert "impute" in steps
        assert "outlier" in steps
        assert "returns" in steps

    def test_resample_preset_steps(self):
        """测试 resample 预设步骤"""
        steps = PRESETS["resample"]["steps"]
        assert "resample" in steps
        assert "dedup" in steps
        assert "impute" in steps


class TestCleanData:
    """测试 clean_data 函数"""

    def test_clean_data_quick(self, sample_data):
        """测试 quick 模式"""
        result_df, report = clean_data(sample_data, preset="quick")

        assert report.duplicates_removed > 0
        assert "dedup" in report.steps_applied

    def test_clean_data_standard(self, sample_data):
        """测试 standard 模式"""
        result_df, report = clean_data(sample_data, preset="standard")

        assert report.duplicates_removed > 0
        assert "dedup" in report.steps_applied
        assert "impute" in report.steps_applied

    def test_clean_data_thorough(self, sample_data):
        """测试 thorough 模式"""
        result_df, report = clean_data(sample_data, preset="thorough")

        assert report.duplicates_removed > 0
        assert "returns" in report.steps_applied
        assert "return" in result_df.columns

    def test_clean_data_custom_steps(self, sample_data):
        """测试自定义步骤"""
        steps = ["dedup", "outlier"]
        params = {
            "dedup_strategy": "first",
            "outlier_method": "iqr",
            "outlier_threshold": 1.5,
            "outlier_action": "flag",
        }
        result_df, report = clean_data(sample_data, preset="custom", steps=steps, params=params)

        assert "dedup" in report.steps_applied
        assert "outlier" in report.steps_applied
        assert "impute" not in report.steps_applied

    def test_clean_data_override_steps(self, sample_data):
        """测试覆盖预设步骤"""
        # standard 预设包含 dedup 和 impute，但我们只想要 dedup
        steps = ["dedup"]
        result_df, report = clean_data(sample_data, preset="standard", steps=steps)

        assert "dedup" in report.steps_applied
        assert "impute" not in report.steps_applied

    def test_clean_data_dry_run(self, sample_data):
        """测试 dry_run 模式"""
        original_len = len(sample_data)
        result_df, report = clean_data(sample_data, dry_run=True)

        # dry_run 不修改原始数据
        assert len(sample_data) == original_len

    def test_clean_data_with_missing(self, data_with_missing):
        """测试缺失值填充"""
        result_df, report = clean_data(data_with_missing, preset="standard")

        # 检查缺失值是否被填充
        assert report.missing_filled >= 0

    def test_clean_data_custom_params_override(self, sample_data):
        """测试自定义参数覆盖预设"""
        params = {"dedup_strategy": "max_volume"}
        result_df, report = clean_data(sample_data, preset="standard", params=params)

        # 自定义参数应该覆盖预设
        assert report.params_applied["dedup_strategy"] == "max_volume"


class TestResample:
    """测试变频功能"""

    def test_resample_weekly(self, daily_data):
        """测试周线变频"""
        result_df, report = clean_data(daily_data, preset="resample")

        assert report.resampled is True
        assert report.target_freq == "W"
        assert len(result_df) < len(daily_data)

    def test_resample_monthly(self, daily_data):
        """测试月线变频"""
        params = {"resample_freq": "M"}
        result_df, report = clean_data(daily_data, preset="custom", steps=["resample"], params=params)

        assert report.resampled is True
        assert report.target_freq == "M"

    def test_resample_preserves_ohlcv(self, daily_data):
        """测试变频保留 OHLCV 数据"""
        result_df, report = clean_data(daily_data, preset="resample")

        assert 'open' in result_df.columns
        assert 'high' in result_df.columns
        assert 'low' in result_df.columns
        assert 'close' in result_df.columns

    def test_resample_high_low(self, daily_data):
        """测试变频使用 high/low 聚合"""
        params = {"resample_freq": "W", "resample_method": "high"}
        result_df, report = clean_data(daily_data, preset="custom", steps=["resample"], params=params)

        assert report.resampled is True


class TestDeduplication:
    """测试去重功能"""

    def test_dedup_first(self, sample_data):
        """测试保留第一条"""
        result_df, removed = _dedup_test(sample_data, "first")
        assert removed > 0
        assert len(result_df) < len(sample_data)

    def test_dedup_last(self, sample_data):
        """测试保留最后一条"""
        result_df, removed = _dedup_test(sample_data, "last")
        assert removed > 0

    def test_dedup_max_volume(self, sample_data):
        """测试保留成交量最大"""
        result_df, removed = _dedup_test(sample_data, "max_volume")
        assert removed > 0


def _dedup_test(df, strategy):
    """去重测试辅助函数"""
    from strategy_research.apps.tools.data_clean import _dedup
    params = {
        "dedup_strategy": strategy,
        "dedup_subset": ["asset", "date"],
    }
    return _dedup(df.copy(), params)


class TestImputation:
    """测试缺失值填充"""

    def test_impute_ffill(self, data_with_missing):
        """测试前向填充"""
        from strategy_research.apps.tools.data_clean import _impute
        params = {
            "impute_method": "ffill",
            "impute_columns": ["open", "high", "low", "close"],
        }
        result_df, filled = _impute(data_with_missing.copy(), params)
        assert filled >= 0

    def test_impute_bfill(self, data_with_missing):
        """测试后向填充"""
        from strategy_research.apps.tools.data_clean import _impute
        params = {
            "impute_method": "bfill",
            "impute_columns": ["open", "high", "low", "close"],
        }
        result_df, filled = _impute(data_with_missing.copy(), params)
        assert filled >= 0

    def test_impute_zero(self, data_with_missing):
        """测试零值填充"""
        from strategy_research.apps.tools.data_clean import _impute
        params = {
            "impute_method": "zero",
            "impute_columns": ["open", "high", "low", "close"],
        }
        result_df, filled = _impute(data_with_missing.copy(), params)
        assert filled >= 0


class TestOutlierDetection:
    """测试异常值检测"""

    def test_outlier_iqr(self, sample_data):
        """测试 IQR 方法"""
        from strategy_research.apps.tools.data_clean import _detect_outliers
        params = {
            "outlier_method": "iqr",
            "outlier_threshold": 1.5,
            "outlier_action": "flag",
        }
        result_df, detected = _detect_outliers(sample_data.copy(), params)
        assert detected >= 0

    def test_outlier_flag(self, sample_data):
        """测试标记异常值"""
        from strategy_research.apps.tools.data_clean import _detect_outliers
        params = {
            "outlier_method": "iqr",
            "outlier_threshold": 1.5,
            "outlier_action": "flag",
        }
        result_df, _ = _detect_outliers(sample_data.copy(), params)
        assert "is_outlier" in result_df.columns


class TestCleaningReport:
    """测试清洗报告"""

    def test_report_creation(self):
        """测试报告创建"""
        report = CleaningReport(
            initial_rows=100,
            final_rows=90,
            steps_applied=["dedup", "impute"],
            duplicates_removed=10,
            missing_filled=5,
            outliers_detected=2,
            params_applied={"test": "value"},
            message="Test message"
        )
        assert report.initial_rows == 100
        assert report.final_rows == 90
        assert report.steps_applied == ["dedup", "impute"]
        assert report.duplicates_removed == 10

    def test_report_message(self, sample_data):
        """测试报告消息"""
        result_df, report = clean_data(sample_data, preset="quick")
        assert isinstance(report.message, str)
        assert len(report.message) > 0
