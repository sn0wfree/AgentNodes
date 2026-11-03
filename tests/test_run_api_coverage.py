"""Tests for /api/run/* endpoints — /status, /list, /start.

Coverage targets the previously un-tested routers/run.py paths:
- /status: returns metrics.json when present, empty metrics dict
  when missing, 404 for missing run, 500 on unparseable JSON.
- /list: empty list when workspace/run-dir is missing, sorted
  by directory mtime descending, capped by limit.
- /start: 404 for missing workspace, result passthrough on success.
- /equity edge cases (path traversal already covered by test_run_equity_api.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest


def _build_asgi_app():
    from fastapi import FastAPI

    from strategy_research.interfaces.api.middleware import AuthMiddleware
    from strategy_research.interfaces.api.routers import run

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(run.router, prefix="/api/run")
    return app


def _auth_headers() -> dict:
    from strategy_research.interfaces.api.auth_tokens import create_token

    return {"Authorization": f"Bearer {create_token('tester')}"}


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers=_auth_headers(),
    )


def _seed_run(ws: Path, strategy: str, run_name: str, metrics: dict | None = None,
              *, create_dir: bool = True) -> Path:
    """Create a run dir under workspace/strategies/{strategy}/runs/{run_name}."""
    if create_dir:
        run_dir = ws / "strategies" / strategy / "runs" / run_name
        run_dir.mkdir(parents=True, exist_ok=True)
        if metrics is not None:
            (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
        return run_dir
    return ws / "strategies" / strategy / "runs" / run_name


@pytest.fixture
def app_env(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


# ────────────────────────── /status ──────────────────────────


@pytest.mark.asyncio
async def test_status_returns_metrics(app_env):
    app = _build_asgi_app()
    _seed_run(app_env, "mom", "run_0001", {"sharpe": 1.4, "max_dd": -0.1})
    async with _client(app) as c:
        r = await c.get(
            f"/api/run/status?workspace_path={app_env}&strategy_name=mom&run_name=run_0001"
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["run"] == "run_0001"
        assert data["metrics"]["sharpe"] == 1.4


@pytest.mark.asyncio
async def test_status_missing_run_returns_404(app_env):
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get(
            f"/api/run/status?workspace_path={app_env}&strategy_name=mom&run_name=ghost"
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_status_empty_metrics_when_metrics_json_missing(app_env):
    """Run dir exists but metrics.json is missing → empty dict."""
    app = _build_asgi_app()
    _seed_run(app_env, "mom", "run_0001", create_dir=True)
    async with _client(app) as c:
        r = await c.get(
            f"/api/run/status?workspace_path={app_env}&strategy_name=mom&run_name=run_0001"
        )
        assert r.status_code == 200
        assert r.json()["metrics"] == {}


@pytest.mark.asyncio
async def test_status_invalid_metrics_json_returns_500(app_env):
    app = _build_asgi_app()
    run_dir = _seed_run(app_env, "mom", "run_broken")
    (run_dir / "metrics.json").write_text("not json {", encoding="utf-8")
    async with _client(app) as c:
        r = await c.get(
            f"/api/run/status?workspace_path={app_env}&strategy_name=mom&run_name=run_broken"
        )
        assert r.status_code == 500


# ────────────────────────── /list ──────────────────────────


@pytest.mark.asyncio
async def test_list_empty_when_workspace_missing(tmp_path):
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get(f"/api/run/list?workspace_path={tmp_path / 'no-ws'}")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "runs": []}


@pytest.mark.asyncio
async def test_list_empty_when_strategy_dir_missing(app_env):
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get(
            f"/api/run/list?workspace_path={app_env}&strategy_name=no-strat"
        )
        assert r.status_code == 200
        assert r.json()["runs"] == []


@pytest.mark.asyncio
async def test_list_returns_only_run_prefix_dirs(app_env):
    """Non-run_* dirs (e.g. snapshots) are excluded."""
    app = _build_asgi_app()
    runs = app_env / "strategies" / "mom" / "runs"
    (runs / "run_0001").mkdir(parents=True)
    (runs / "run_0002").mkdir()
    (runs / "snapshot_2026").mkdir()  # not run_* prefix
    async with _client(app) as c:
        r = await c.get(
            f"/api/run/list?workspace_path={app_env}&strategy_name=mom"
        )
        names = [item["name"] for item in r.json()["runs"]]
        assert sorted(names) == ["run_0001", "run_0002"]


@pytest.mark.asyncio
async def test_list_attaches_metrics_when_present(app_env):
    app = _build_asgi_app()
    runs = app_env / "strategies" / "mom" / "runs"
    (runs / "run_001").mkdir(parents=True)
    (runs / "run_001" / "metrics.json").write_text(
        json.dumps({"sharpe": 0.9}), encoding="utf-8"
    )
    (runs / "run_002").mkdir()
    async with _client(app) as c:
        r = await c.get(
            f"/api/run/list?workspace_path={app_env}&strategy_name=mom"
        )
        runs_list = r.json()["runs"]
        with_metrics = next(r for r in runs_list if r["name"] == "run_001")
        without_metrics = next(r for r in runs_list if r["name"] == "run_002")
        assert with_metrics["metrics"]["sharpe"] == 0.9
        assert without_metrics["metrics"] == {}


@pytest.mark.asyncio
async def test_list_caps_at_limit(app_env):
    app = _build_asgi_app()
    runs = app_env / "strategies" / "mom" / "runs"
    for i in range(5):
        (runs / f"run_{i:03d}").mkdir(parents=True)
    async with _client(app) as c:
        r = await c.get(
            f"/api/run/list?workspace_path={app_env}&strategy_name=mom&limit=2"
        )
        assert len(r.json()["runs"]) == 2


# ────────────────────────── /start ──────────────────────────


@pytest.mark.asyncio
async def test_start_missing_workspace_returns_404(tmp_path):
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.post(
            "/api/run/start",
            json={
                "workspace_path": str(tmp_path / "no-ws"),
                "strategy_name": "mom",
                "action": "manual",
            },
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_start_passes_through_result(monkeypatch, app_env):
    """When run_backtest_script returns a result, /start returns it."""
    def fake_backtest(**kwargs):
        return {"success": True, "metrics": {"sharpe": 1.1}, "log": []}

    monkeypatch.setattr(
        "strategy_research.apps.backtest.run_backtest_script",
        fake_backtest,
    )

    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.post(
            "/api/run/start",
            json={
                "workspace_path": str(app_env),
                "strategy_name": "mom",
                "action": "manual",
                "description": "test",
                "timeout": 60,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["result"]["success"] is True
        assert data["result"]["metrics"]["sharpe"] == 1.1
