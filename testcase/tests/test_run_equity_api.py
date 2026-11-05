"""Tests for the run equity endpoint (GET /api/run/equity)."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from strategy_research.interfaces.api.auth_tokens import create_token
from strategy_research.interfaces.api.routers.run import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/run")
    return TestClient(app)


def auth_header(user_id: str = "tester") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token(user_id)}"}


def _make_run(ws, strategy: str, run: str, rows: list[tuple]) -> None:
    run_dir = ws / "strategies" / strategy / "runs" / run
    run_dir.mkdir(parents=True)
    lines = ["timestamp,capital,unrealized,equity,positions"]
    for t, equity in rows:
        lines.append(f"{t},{equity},{0.0},{equity},{0}")
    (run_dir / "equity_curve.csv").write_text("\n".join(lines) + "\n")
    (run_dir / "metrics.json").write_text(
        json.dumps({"total_return": 0.5, "sharpe": 1.2, "status": "ok"})
    )


class TestRunEquity:
    def test_equity_returns_points(self, client, tmp_path):
        _make_run(tmp_path, "s1", "run_0001", [(i, 1.0 + i * 0.1) for i in range(10)])
        res = client.get(
            "/api/run/equity",
            params={
                "workspace_path": str(tmp_path),
                "strategy_name": "s1",
                "run_name": "run_0001",
            },
            headers=auth_header(),
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert len(data["equity"]) == 10
        assert data["equity"][0]["equity"] == 1.0
        assert data["equity"][9]["equity"] == 1.9

    def test_equity_sampling_caps_points(self, client, tmp_path):
        _make_run(tmp_path, "s1", "run_0001", [(i, float(i)) for i in range(1000)])
        res = client.get(
            "/api/run/equity",
            params={
                "workspace_path": str(tmp_path),
                "strategy_name": "s1",
                "run_name": "run_0001",
                "max_points": 100,
            },
            headers=auth_header(),
        )
        data = res.json()
        assert len(data["equity"]) == 100
        assert data["equity"][0]["equity"] == 0.0
        assert data["equity"][-1]["equity"] == 999.0

    def test_equity_missing_run_404(self, client, tmp_path):
        res = client.get(
            "/api/run/equity",
            params={
                "workspace_path": str(tmp_path),
                "strategy_name": "s1",
                "run_name": "run_9999",
            },
            headers=auth_header(),
        )
        assert res.status_code == 404

    def test_equity_empty_curve_returns_empty_list(self, client, tmp_path):
        run_dir = tmp_path / "strategies" / "s1" / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        (run_dir / "equity_curve.csv").write_text(
            "timestamp,capital,unrealized,equity,positions\n"
        )
        res = client.get(
            "/api/run/equity",
            params={
                "workspace_path": str(tmp_path),
                "strategy_name": "s1",
                "run_name": "run_0001",
            },
            headers=auth_header(),
        )
        assert res.status_code == 200
        assert res.json()["equity"] == []

    def test_equity_blocks_path_traversal(self, client, tmp_path):
        res = client.get(
            "/api/run/equity",
            params={
                "workspace_path": str(tmp_path),
                "strategy_name": "..",
                "run_name": "run_0001",
            },
            headers=auth_header(),
        )
        assert res.status_code in (400, 404)
