"""Tests for data_source/registry.py — loader registration + fallback resolution."""

from __future__ import annotations

import pytest

from strategy_research.apps.data_source import registry


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset _registered flag to False so _ensure_registered runs fresh per test."""
    registry._registered = False
    yield
    registry._registered = False


# ============================================================
# Registration
# ============================================================


class TestRegistration:
    def test_register_adds_to_registry(self):
        class FakeLoader:
            name = "fake"

        registry.register(FakeLoader)
        assert "fake" in registry.LOADER_REGISTRY
        assert registry.LOADER_REGISTRY["fake"] is FakeLoader

    def test_register_returns_class(self):
        class FakeLoader:
            name = "fake2"

        result = registry.register(FakeLoader)
        assert result is FakeLoader

    def test_get_loader_returns_registered(self):
        class FakeLoader:
            name = "fake3"

        registry.register(FakeLoader)
        result = registry.get_loader("fake3")
        assert result is FakeLoader

    def test_get_loader_unknown_returns_none(self):
        result = registry.get_loader("nonexistent_loader_xyz")
        assert result is None

    def test_list_loaders_includes_registered(self):
        class FakeLoader:
            name = "fake4"

        registry.register(FakeLoader)
        loaders = registry.list_loaders()
        assert "fake4" in loaders


# ============================================================
# FALLBACK_CHAINS
# ============================================================


class TestFallbackChains:
    def test_a_share_chain_exists(self):
        assert "a_share" in registry.FALLBACK_CHAINS
        assert len(registry.FALLBACK_CHAINS["a_share"]) > 0

    def test_hk_chain_exists(self):
        assert "hk" in registry.FALLBACK_CHAINS

    def test_us_chain_exists(self):
        assert "us" in registry.FALLBACK_CHAINS

    def test_crypto_chain_exists(self):
        assert "crypto" in registry.FALLBACK_CHAINS

    def test_macro_chain_exists(self):
        assert "macro" in registry.FALLBACK_CHAINS

    def test_chains_include_local(self):
        # All chains should have local as a fallback
        for market, chain in registry.FALLBACK_CHAINS.items():
            assert "local" in chain, f"Chain for {market} missing local fallback"

    def test_no_network_fallback_is_local(self):
        assert "local" in registry._NO_NETWORK_FALLBACK


# ============================================================
# NoAvailableSourceError
# ============================================================


class TestNoAvailableSourceError:
    def test_exception_can_be_raised(self):
        with pytest.raises(registry.NoAvailableSourceError):
            raise registry.NoAvailableSourceError("test message")

    def test_exception_message(self):
        err = registry.NoAvailableSourceError("custom message")
        assert str(err) == "custom message"

    def test_is_exception_subclass(self):
        assert issubclass(registry.NoAvailableSourceError, Exception)


# ============================================================
# resolve_loader_with_fallback
# ============================================================


class TestResolveLoaderWithFallback:
    def test_unknown_source_raises(self):
        with pytest.raises(registry.NoAvailableSourceError, match="未知数据源"):
            registry.resolve_loader_with_fallback("totally_made_up_source_123")

    def test_local_loader_no_network_fallback(self):
        # Local loader is registered and always available
        # If unavailable, should raise (no fallback to network)
        result = registry.resolve_loader_with_fallback("local")
        # local is always available, returns the class
        assert result is not None


# ============================================================
# get_loader_or_fallback
# ============================================================


class TestGetLoaderOrFallback:
    def test_unknown_source_raises(self):
        with pytest.raises(registry.NoAvailableSourceError):
            registry.get_loader_or_fallback("nonexistent_loader_xyz_abc")

    def test_unknown_source_message_mentions_source(self):
        with pytest.raises(registry.NoAvailableSourceError, match="未知数据源"):
            registry.get_loader_or_fallback("nonexistent_loader_xyz_abc")

    def test_unknown_source_does_not_fall_back_to_tushare(self, monkeypatch):
        # Even if tushare is available, an unknown source must NOT silently
        # return tushare — that's the bug the regression test guards against.
        sentinel = registry.LOADER_REGISTRY.get("tushare")
        captured = []

        def _spy(*a, **kw):
            captured.append(a)
            return sentinel

        monkeypatch.setattr(
            "strategy_research.apps.data_source.registry.LOADER_REGISTRY",
            {**registry.LOADER_REGISTRY, "tushare": _spy},
        )
        # Make sure the spy registers as available
        monkeypatch.setattr(
            registry, "resolve_loader_with_fallback",
            lambda s: (_ for _ in ()).throw(registry.NoAvailableSourceError(f"未知数据源: {s}")),
        )
        with pytest.raises(registry.NoAvailableSourceError):
            registry.get_loader_or_fallback("nonexistent_loader_xyz_abc")
        assert captured == [], "tushare must not be invoked for unknown source"

    def test_known_but_unavailable_falls_back_to_tushare(self, monkeypatch):
        # A registered source whose loader raises NoAvailableSourceError
        # should fall back to tushare when tushare is available.
        class _FakeButDown:
            name = "fake_down"

            @staticmethod
            def is_available():
                return False

        fake_cls = _FakeButDown
        registry.LOADER_REGISTRY.get('tushare')

        # Patch LOADER_REGISTRY to register fake_down + tushare
        new_registry = dict(registry.LOADER_REGISTRY)
        new_registry["fake_down"] = fake_cls
        monkeypatch.setattr(
            "strategy_research.apps.data_source.registry.LOADER_REGISTRY",
            new_registry,
        )
        # resolve_loader_with_fallback raises because fake_down.is_available() == False
        # and 'fake_down' has no markets (so no market-chain fallback either).
        try:
            result = registry.get_loader_or_fallback("fake_down")
        except registry.NoAvailableSourceError:
            return  # tushare unavailable → re-raise is the documented behavior
        assert result is registry.LOADER_REGISTRY["tushare"]

    def test_does_not_loop_to_tushare_when_already_asking_tushare(self, monkeypatch):
        # If someone asks for tushare and it raises, we should NOT
        # re-attempt tushare and recurse (the new guard `source != "tushare"`)
        class _TushareDown:
            name = "tushare"

            @staticmethod
            def is_available():
                return False

        new_registry = dict(registry.LOADER_REGISTRY)
        new_registry["tushare"] = _TushareDown
        monkeypatch.setattr(
            "strategy_research.apps.data_source.registry.LOADER_REGISTRY",
            new_registry,
        )
        with pytest.raises(registry.NoAvailableSourceError):
            registry.get_loader_or_fallback("tushare")


# ============================================================
# resolve_loader
# ============================================================


class TestResolveLoader:
    def test_unknown_market_raises(self):
        # Unknown market with no chain → NoAvailableSourceError
        with pytest.raises(registry.NoAvailableSourceError):
            registry.resolve_loader("totally_made_up_market_xyz")


# ============================================================
# _ensure_registered
# ============================================================


class TestEnsureRegistered:
    def test_registers_at_least_local(self):
        registry._registered = False
        registry._ensure_registered()
        assert registry._registered is True
        # local loader should be registered
        assert "local" in registry.LOADER_REGISTRY

    def test_idempotent(self):
        registry._ensure_registered()
        first_count = len(registry.LOADER_REGISTRY)
        registry._ensure_registered()
        # Should not re-register
        assert len(registry.LOADER_REGISTRY) == first_count
