"""Validation API router — /api/validate/*"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


class ValidateRunRequest(BaseModel):
    run_dir: str
    market: str = "a_share"
    monte_carlo: bool = True
    n_simulations: int = 1000
    bootstrap: bool = True
    n_bootstrap: int = 1000
    walk_forward: bool = True
    n_windows: int = 5


@router.post("/run")
async def validate_run(req: ValidateRunRequest, request: Request):
    """执行 validation。"""
    try:
        from strategy_research.apps.validation.cli import (
            _build_synthetic_trades,
            _load_nav_from_duckdb,
            _load_nav_synthetic,
            _load_trades_from_artifacts,
        )
        from strategy_research.apps.validation.market import MarketType
        from strategy_research.apps.validation.runner import run_validation

        # Security: resolve run_dir and require it to live under the
        # configured workspace (env: STRATEGY_RESEARCH_VALIDATE_ROOT,
        # default $HOME). Prevents arbitrary FS read by this endpoint.
        try:
            run_dir = Path(req.run_dir).resolve()
            root = Path(
                os.environ.get("STRATEGY_RESEARCH_VALIDATE_ROOT", os.path.expanduser("~"))
            ).resolve()
            run_dir.relative_to(root)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"run_dir must resolve under {root}",
            )
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail=f"Run dir not found: {run_dir}")

        equity_curve = _load_nav_from_duckdb(run_dir)
        if equity_curve is None or len(equity_curve) < 5:
            equity_curve = _load_nav_synthetic(run_dir)
        if equity_curve is None:
            raise HTTPException(
                status_code=404,
                detail=f"no equity curve under {run_dir} (need DuckDB NAV or metrics.json)",
            )
        trades = _load_trades_from_artifacts(run_dir) or _build_synthetic_trades(run_dir)

        try:
            market = MarketType(req.market)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown market: {req.market}")

        v_cfg: dict = {}
        if req.monte_carlo:
            v_cfg["monte_carlo"] = {"n_simulations": req.n_simulations}
        if req.bootstrap:
            v_cfg["bootstrap"] = {"n_bootstrap": req.n_bootstrap}
        if req.walk_forward:
            v_cfg["walk_forward"] = {"n_windows": req.n_windows}

        result = run_validation(
            config={"validation": v_cfg},
            equity_curve=equity_curve,
            trades=trades,
            market=market,
        )
        return {"status": "ok", "validation": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

