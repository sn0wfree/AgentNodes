"""Tests for A3 (validation.py path containment) + A4 (SQL param binding)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# ────────────────────────── A3: validation.py containment ──────────────────────────


def _build_app():
    from fastapi import FastAPI

    from strategy_research.interfaces.api.middleware import AuthMiddleware
    from strategy_research.interfaces.api.routers.validation import router as v_router

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(v_router, prefix="/api/validate")
    return app


def _client(app):
    import httpx

    from strategy_research.interfaces.api.auth_tokens import create_token

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {create_token('tester')}"},
    )


@pytest.mark.asyncio
async def test_validate_run_rejects_path_escaping_root(tmp_path, monkeypatch):
    """A3: run_dir 解析后若不在 STRATEGY_RESEARCH_VALIDATE_ROOT 内 → 400。"""
        # default root = $HOME; force it to a known dir under tmp.
    monkeypatch.setenv("STRATEGY_RESEARCH_VALIDATE_ROOT", str(tmp_path))
    app = _build_app()
    async with _client(app) as c:
        r = await c.post(
            "/api/validate/run",
            json={"run_dir": "/etc", "n_simulations": 10},
        )
        assert r.status_code == 400
        assert "must resolve under" in r.json()["detail"]


@pytest.mark.asyncio
async def test_validate_run_accepts_contained_run_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_RESEARCH_VALIDATE_ROOT", str(tmp_path))
    run_dir = tmp_path / "strategies" / "mom" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    # A.4 adapter needs an equity source — synthetic NAV from metrics.json
    (run_dir / "metrics.json").write_text(
        '{"ann_return": 0.12, "sharpe": 1.5}', encoding="utf-8"
    )

    fake_result = {"monte_carlo": {}, "walk_forward": {}}
    with patch(
        "strategy_research.apps.validation.runner.run_validation",
        return_value=fake_result,
    ) as mock_rv:
        app = _build_app()
        async with _client(app) as c:
            r = await c.post(
                "/api/validate/run",
                json={"run_dir": str(run_dir), "n_simulations": 10},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "ok"
        # A.4: caller must speak the real signature (config/equity_curve/…),
        # not the imagined (run_dir=/run_monte_carlo=) one.
        assert mock_rv.call_count == 1
        kwargs = mock_rv.call_args.kwargs
        assert set(kwargs) >= {"config", "equity_curve", "trades", "market"}
        assert kwargs["config"]["validation"]["monte_carlo"]["n_simulations"] == 10
        assert kwargs["market"].value == "a_share"


# ────────────────────────── A4: SQL parameter binding ──────────────────────────


def test_local_loader_uses_parameterized_sql(tmp_path):
    """A4: local_loader.py:96 用 `?` 参数化而不再 f-string 拼 SQL。"""
    from strategy_research.apps.data_source import local_loader

    src = Path(local_loader.__file__).read_text(encoding="utf-8")
    assert "f\"SELECT * FROM prices WHERE asset_code = '{code}'\"" not in src, (
        "f-string SQL interpolation is back; A4 regression"
    )
    assert "WHERE asset_code = ?" in src, "expected parameterized `?` binding"
    # The execute call must pass `[code]` (list) not a string concatenation.
    assert '"SELECT * FROM prices WHERE asset_code = ?", [code]' in src
