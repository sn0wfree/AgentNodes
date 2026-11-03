"""Run API router — /api/run/*"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


def _resolve_run_dir(workspace_path: str, strategy_name: str, run_name: str) -> Path:
    """Resolve a run dir, containing it inside the workspace.

    strategy_name / run_name come from query params and must not be able
    to escape the workspace via ".." (path traversal). The run dir is
    only read when it exists inside the workspace; otherwise a 404 is
    raised.
    """
    workspace = Path(workspace_path).resolve()
    if not workspace.is_dir():
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace}")
    run_dir = (workspace / "strategies" / strategy_name / "runs" / run_name).resolve()
    try:
        run_dir.relative_to(workspace)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid run path (outside workspace)",
        )
    return run_dir


class RunStartRequest(BaseModel):
    workspace_path: str
    strategy_name: str
    action: str = "manual"
    description: str = ""
    timeout: int = 300


@router.post("/start")
async def run_start(req: RunStartRequest, request: Request):
    """启动回测 run。"""
    try:
        from strategy_research.apps.backtest import run_backtest_script

        workspace = Path(req.workspace_path)
        if not workspace.exists():
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace}")

        result = run_backtest_script(
            workspace_path=workspace,
            strategy_name=req.strategy_name,
            action=req.action,
            description=req.description,
            timeout=req.timeout,
        )
        return {"status": "ok" if result["success"] else "error", "result": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def run_status(
    request: Request,
    workspace_path: str = ".",
    strategy_name: str = "",
    run_name: str = "",
):
    """获取 run 状态。"""
    try:
        run_dir = _resolve_run_dir(workspace_path, strategy_name, run_name)

        if not run_dir.exists():
            raise HTTPException(status_code=404, detail=f"Run not found: {run_dir}")

        import json

        metrics_path = run_dir / "metrics.json"
        if metrics_path.exists():
            with open(metrics_path, "r", encoding="utf-8") as f:
                metrics = json.load(f)
            return {"status": "ok", "run": run_name, "metrics": metrics}
        return {"status": "ok", "run": run_name, "metrics": {}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/equity")
async def run_equity(
    request: Request,
    workspace_path: str = ".",
    strategy_name: str = "",
    run_name: str = "",
    max_points: int = 2000,
):
    """读取 run 的净值曲线 equity_curve.csv（等距采样至 max_points 点）。"""
    try:
        run_dir = _resolve_run_dir(workspace_path, strategy_name, run_name)

        if not run_dir.exists():
            raise HTTPException(status_code=404, detail=f"Run not found: {run_dir}")

        equity_path = run_dir / "equity_curve.csv"
        if not equity_path.exists():
            return {"status": "ok", "run": run_name, "equity": []}

        import pandas as pd

        df = pd.read_csv(equity_path)
        if df.empty:
            return {"status": "ok", "run": run_name, "equity": []}

        if len(df) > max_points:
            step = len(df) / max_points
            idx = sorted({
                0,
                *(int(i * step) for i in range(1, max_points - 1)),
                len(df) - 1,
            })
            df = df.iloc[idx]

        df = df.fillna(0)
        equity = df.to_dict(orient="records")
        for row in equity:
            for k, v in list(row.items()):
                if isinstance(v, float):
                    row[k] = round(v, 6)
        return {"status": "ok", "run": run_name, "equity": equity}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def run_list(
    request: Request,
    workspace_path: str = ".",
    strategy_name: str = "",
    limit: int = 20,
):
    """列出 runs。"""
    try:
        workspace = Path(workspace_path).resolve()
        if not workspace.is_dir():
            # Legacy behavior: missing workspace → empty run list
            return {"status": "ok", "runs": []}
        runs_dir = _resolve_run_dir(workspace_path, strategy_name, ".")

        if not runs_dir.exists():
            return {"status": "ok", "runs": []}

        import json

        runs = []
        for d in sorted(runs_dir.iterdir(), reverse=True):
            if d.is_dir() and d.name.startswith("run_"):
                metrics_path = d / "metrics.json"
                metrics = {}
                if metrics_path.exists():
                    with open(metrics_path, "r", encoding="utf-8") as f:
                        metrics = json.load(f)
                runs.append({"name": d.name, "metrics": metrics})
                if len(runs) >= limit:
                    break

        return {"status": "ok", "runs": runs}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
