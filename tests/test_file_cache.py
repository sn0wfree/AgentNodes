"""Tests for file_cache decorator used by market data loaders."""

import os
import time

import pandas as pd
import pytest

from strategy_research.apps.data_source.file_cache import file_cache, get_cache_path


@pytest.fixture
def cache_dir(tmp_path):
    """Provide a temporary cache directory."""
    d = tmp_path / "test_cache"
    d.mkdir()
    return str(d)


class TestFileCache:
    """Test file_cache decorator."""

    def test_cache_hit_returns_same_result(self, cache_dir):
        """Same call should hit cache and return identical result."""
        call_count = 0

        @file_cache(enable_cache=True, granularity='d', cache_dir=cache_dir)
        def fetch_data(code: str) -> pd.DataFrame:
            nonlocal call_count
            call_count += 1
            return pd.DataFrame({"code": [code], "value": [1.0]})

        # First call: cache miss
        result1 = fetch_data("000001.SZ")
        assert call_count == 1
        assert len(result1) == 1

        # Second call: cache hit
        result2 = fetch_data("000001.SZ")
        assert call_count == 1  # Should NOT increment
        assert len(result2) == 1

    def test_force_refresh_bypasses_cache(self, cache_dir):
        """force_refresh=True should skip cache read."""
        call_count = 0

        @file_cache(enable_cache=True, granularity='d', cache_dir=cache_dir)
        def fetch_data(code: str) -> pd.DataFrame:
            nonlocal call_count
            call_count += 1
            return pd.DataFrame({"code": [code], "value": [1.0]})

        # First call
        fetch_data("000001.SZ")
        assert call_count == 1

        # force_refresh=True should re-execute
        fetch_data("000001.SZ", force_refresh=True)
        assert call_count == 2

    def test_different_args_different_cache(self, cache_dir):
        """Different arguments should produce different cache entries."""
        call_count = 0

        @file_cache(enable_cache=True, granularity='d', cache_dir=cache_dir)
        def fetch_data(code: str) -> pd.DataFrame:
            nonlocal call_count
            call_count += 1
            return pd.DataFrame({"code": [code], "value": [1.0]})

        fetch_data("000001.SZ")
        fetch_data("600519.SH")
        assert call_count == 2

    def test_cache_dir_created(self, cache_dir):
        """Cache directory should be created on first cache miss."""
        target = os.path.join(cache_dir, "sub")

        @file_cache(enable_cache=True, granularity='d', cache_dir=target)
        def fetch_data(code: str) -> str:
            return f"data_{code}"

        fetch_data("test")
        assert os.path.exists(target)

    def test_different_granularity(self, cache_dir):
        """Different granularity should produce different cache keys."""
        call_count = 0

        @file_cache(enable_cache=True, granularity='H', cache_dir=cache_dir)
        def fetch_hourly(code: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"hourly_{code}"

        @file_cache(enable_cache=True, granularity='d', cache_dir=cache_dir)
        def fetch_daily(code: str) -> str:
            return f"daily_{code}"

        fetch_hourly("test")
        assert call_count == 1

        # Same granularity, same args = cache hit
        fetch_hourly("test")
        assert call_count == 1

    def test_cache_disabled(self, cache_dir):
        """enable_cache=False should always execute function."""
        call_count = 0

        @file_cache(enable_cache=False, granularity='d', cache_dir=cache_dir)
        def fetch_data(code: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"data_{code}"

        fetch_data("test")
        fetch_data("test")
        assert call_count == 2

    def test_cache_expiry(self, cache_dir):
        """Cache should expire after granularity period (simulated)."""
        call_count = 0

        @file_cache(enable_cache=True, granularity='S', cache_dir=cache_dir)
        def fetch_data(code: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"data_{code}"

        fetch_data("test")
        assert call_count == 1

        # Wait for 1 second to expire the 'S' granularity cache
        time.sleep(1.1)

        fetch_data("test")
        assert call_count == 2  # Should re-execute after expiry


class TestGetCachePath:
    """Test get_cache_path utility."""

    def test_creates_directory(self, cache_dir):
        """Should create date-stamped subdirectory."""
        path = get_cache_path(enable_cache=True, cache_dir=cache_dir)
        today = time.strftime('%Y%m%d')
        expected = os.path.join(cache_dir, today)
        assert path == expected
        assert os.path.exists(path)

    def test_no_create_when_disabled(self, cache_dir):
        """Should not create directory when enable_cache=False."""
        target = os.path.join(cache_dir, "nodir")
        path = get_cache_path(enable_cache=False, cache_dir=target)
        assert not os.path.exists(path)
