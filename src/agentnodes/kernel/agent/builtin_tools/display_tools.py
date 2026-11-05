"""Display tools: the agent decides what the right panel shows.

Design (docs/design/right-panel-agent-driven.md):
- ``show_chart``: render a chart from a workspace FILE (CSV/JSON) —
  the data never enters the LLM context (file-reference style). The
  tool reads the file, downsamples to ≤ MAX_POINTS, and emits a
  ``chart`` SSE event. The chart lands BOTH in the chat stream and on
  the right panel (latest renderable).
- ``show_report``: render a backtest analysis HTML report. If the
  report does not exist yet, it is generated from the run's artifacts
  (equity_curve.csv + metrics.json) as a self-contained page (inline
  SVG curve, no external CDN — offline-safe), then emitted as an
  ``html`` SSE event.

Both tools emit via ToolContext.emit_event (wired to AgentLoop._emit
in loop.py) and are persisted by the projector (chart → _on_chart,
html → _on_html) so reloads keep them in the chat record.
"""

from __future__ import annotations

import csv
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from agentnodes.kernel.agent.tools import BaseTool, ToolContext, ToolError
from .utils import err_actionable, tool_ok

logger = logging.getLogger(__name__)

# Downsample budget: enough for a smooth curve, small enough for SSE
# + DB persistence.
MAX_POINTS = 500

ALLOWED_CHART_TYPES = ("bar", "line", "pie", "scatter")


def _resolve_workspace_file(
    ctx: ToolContext, source_file: str,
) -> Optional[Path]:
    """Resolve a relative workspace path with traversal protection.

    Returns None (with a log) when the path escapes the workspace.
    """
    if ctx.workspace is None:
        return None
    try:
        candidate = (ctx.workspace / source_file).resolve()
        workspace_root = Path(ctx.workspace).resolve()
        if not candidate.is_relative_to(workspace_root):
            logger.warning("path escapes workspace: %s", source_file)
            return None
    except (ValueError, OSError):
        return None
    return candidate


def _downsample(points: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    """Evenly downsample a list of dicts to at most max_points."""
    n = len(points)
    if n <= max_points:
        return points
    step = n / max_points
    out: list[dict[str, Any]] = []
    for i in range(max_points):
        idx = min(n - 1, int(i * step))
        out.append(points[idx])
    # Always keep the last point (curve endpoint)
    if out and out[-1] != points[-1]:
        out[-1] = points[-1]
    return out


def _load_series(path: Path) -> list[dict[str, Any]]:
    """Load a CSV or JSON array of objects into {label-ish, value} rows.

    CSV: any columns are kept; numeric values are coerced to float so
    the frontend can auto-detect x (string) / y (number) keys.
    JSON: a list of objects, or a dict of {label: value}.
    """
    if path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            return [{"label": str(k), "value": float(v)} for k, v in raw.items()]
        if isinstance(raw, list):
            return [
                {k: (float(v) if isinstance(v, (int, float)) else str(v))
                 for k, v in item.items()}
                for item in raw
                if isinstance(item, dict)
            ]
        raise ToolError(f"unsupported JSON shape in {path.name}")

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, Any]] = []
        for row in reader:
            clean: dict[str, Any] = {}
            for k, v in row.items():
                if k is None:
                    continue
                try:
                    clean[k] = float(v)
                except (TypeError, ValueError):
                    clean[k] = str(v)
            if clean:
                rows.append(clean)
        return rows


class ShowChartTool(BaseTool):
    """让 agent 决定右侧面板显示什么图表（文件引用式，数据不进上下文）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.0.0
    # 变更: v1.0.0 新增 (agent-driven right panel)
    #
    # ## 用途
    # 从工作区文件 (CSV/JSON) 读取数据并渲染图表到聊天流 + 右侧面板。
    # 净值曲线数据保存在 runs/<strategy>/<run>/equity_curve.csv
    # (run_backtest 的 artifacts.equity_curve 引用)，用本工具展示时
    # agent 只传路径，数据本身不进 LLM 上下文。
    #
    # ## 参数
    # - source_file: 相对工作区的数据文件路径 (必填, CSV 或 JSON)
    # - chart_type: bar | line | pie | scatter (默认 line)
    # - title: 图表标题 (建议中文, 默认文件名)
    #
    # ## 示例
    # {"source_file": "runs/momentum_20d/run_0007/equity_curve.csv",
    #  "chart_type": "line", "title": "动量策略净值曲线"}
    #
    # ## 边界
    # 只读工具 (effects 无); 路径必须落在工作区内 (防穿越);
    # 最多 500 个数据点 (自动等距采样)。
    #
    # ## 错误处理范式
    # - 路径逃逸工作区 → error + expected
    # - 文件不存在/不可解析 → error + fix (用 run_backtest 的
    #   artifacts 引用或 list_files 确认路径)
    # - 数据为空 → error + 建议先 run_backtest
    #
    # ## 相关工具
    # run_backtest: 产出 equity_curve.csv; show_report: HTML 报告
    # ─────────────────────────────────────────────────────────────
    """

    name = "show_chart"
    description = "从工作区文件渲染图表到聊天流与右侧面板 (数据不进上下文)。"
    repeatable = True
    category = "展示"

    def execute(
        self,
        ctx: ToolContext,
        source_file: str,
        chart_type: str = "line",
        title: str = "",
    ) -> str:
        if not isinstance(source_file, str) or not source_file.strip():
            return err_actionable(
                "missing or invalid 'source_file'",
                received=source_file,
                expected="relative workspace path to a CSV/JSON file, e.g. "
                         "'runs/momentum_20d/run_0007/equity_curve.csv'",
                fix="use the artifacts.equity_curve reference from run_backtest",
                tool="show_chart",
            )
        if chart_type not in ALLOWED_CHART_TYPES:
            return err_actionable(
                f"unsupported chart_type: {chart_type}",
                received=chart_type,
                expected=f"one of {', '.join(ALLOWED_CHART_TYPES)}",
                fix="pick bar/line/pie/scatter",
                tool="show_chart",
            )
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop injects workspace; call from the agent loop",
                tool="show_chart",
            )

        path = _resolve_workspace_file(ctx, source_file)
        if path is None:
            return err_actionable(
                "path escapes the workspace (traversal blocked)",
                received=source_file,
                expected="a path inside the workspace",
                fix="use a relative path under the workspace root",
                tool="show_chart",
            )
        if not path.exists() or not path.is_file():
            return err_actionable(
                "source file not found",
                received=str(path),
                fix="verify with list_files; run_backtest artifacts are under "
                    "runs/<strategy>/<run>/",
                tool="show_chart",
            )

        try:
            rows = _load_series(path)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            return err_actionable(
                f"failed to read file: {exc}",
                received=str(path),
                fix="ensure the file is valid CSV or JSON",
                tool="show_chart",
            )

        if not rows:
            return err_actionable(
                "no data rows in file",
                received=str(path),
                fix="run a backtest first so equity_curve.csv has data",
                tool="show_chart",
            )

        data = _downsample(rows, MAX_POINTS)
        display_title = (title or "").strip() or path.name

        if ctx.emit_event is not None:
            ctx.emit_event("chart", {
                "message_id": ctx.message_id,
                "id": f"chart-{uuid.uuid4().hex[:8]}",
                "chart_type": chart_type,
                "title": display_title,
                "data": data,
            })

        return tool_ok({
            "displayed": display_title,
            "chart_type": chart_type,
            "source_file": source_file,
            "points": len(data),
            "downsampled_from": len(rows),
        })


class ShowReportTool(BaseTool):
    """让 agent 决定右侧面板显示回测分析 HTML 报告（按需生成）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.0.0
    # 变更: v1.0.0 新增 (agent-driven right panel)
    #
    # ## 用途
    # 展示 runs/<strategy>/<run>/report.html。报告不存在时自动从
    # equity_curve.csv + metrics.json 生成 (内联 SVG, 离线可用)，
    # 然后渲染到聊天流 + 右侧面板。run_backtest 成功后调用本工具
    # 即可呈现净值曲线 + 指标卡。
    #
    # ## 参数
    # - strategy_name: 策略目录名 (必填)
    # - run: run 名 (必填, run_backtest 返回的 run)
    #
    # ## 示例
    # {"strategy_name": "momentum_20d", "run": "run_0007"}
    #
    # ## 边界
    # 写文件 (effects: fs) 仅当报告不存在时; 报告内容自包含无外链。
    #
    # ## 错误处理范式
    # - 参数缺失 → error + expected
    # - 产物缺失 → error + fix (先 run_backtest)
    #
    # ## 相关工具
    # run_backtest: 产出 equity_curve.csv/metrics.json; show_chart: 图表
    # ─────────────────────────────────────────────────────────────
    """

    name = "show_report"
    description = "展示回测分析 HTML 报告到聊天流与右侧面板 (不存在时自动生成)。"
    repeatable = True
    category = "展示"
    effects = frozenset({"fs"})

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str,
        run: str,
    ) -> str:
        if not isinstance(strategy_name, str) or not strategy_name.strip():
            return err_actionable(
                "missing or invalid 'strategy_name'",
                received=strategy_name,
                expected="non-empty strategy directory name",
                fix="use the strategy_name from run_backtest",
                tool="show_report",
            )
        if not isinstance(run, str) or not run.strip():
            return err_actionable(
                "missing or invalid 'run'",
                received=run,
                expected="run name, e.g. run_0007",
                fix="use the run field from run_backtest",
                tool="show_report",
            )
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop injects workspace; call from the agent loop",
                tool="show_report",
            )

        # v2: ctx.runs_dir overrides the legacy runs/<strategy> layout
        runs_root = ctx.runs_dir if ctx.runs_dir is not None \
            else ctx.workspace / "runs" / strategy_name
        run_dir = (runs_root / run).resolve()
        workspace_root = Path(ctx.workspace).resolve()
        if not run_dir.is_relative_to(workspace_root):
            return err_actionable(
                "path escapes the workspace (traversal blocked)",
                received=f"runs/{strategy_name}/{run}",
                fix="use a strategy under the workspace",
                tool="show_report",
            )

        report_path = run_dir / "report.html"
        if not report_path.exists():
            generated = _generate_report(run_dir, strategy_name, run)
            if not generated:
                return err_actionable(
                    "cannot generate report: run artifacts missing",
                    received=str(run_dir),
                    fix="run a backtest first: run_backtest(strategy_name=...)",
                    tool="show_report",
                )

        content = _read_html(report_path)
        if not content:
            return err_actionable(
                "report file is empty",
                received=str(report_path),
                fix="regenerate the report",
                tool="show_report",
            )

        if ctx.emit_event is not None:
            ctx.emit_event("html", {
                "message_id": ctx.message_id,
                "id": f"report-{uuid.uuid4().hex[:8]}",
                "title": f"{strategy_name} / {run} 回测报告",
                "content": content,
            })

        return tool_ok({
            "displayed": f"{strategy_name}/{run}",
            "report": str(report_path),
            "bytes": len(content),
        })


# ── HTML report generator ─────────────────────────────────────────


def _read_html(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _load_metrics(run_dir: Path) -> dict[str, Any]:
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        return {}
    try:
        with open(metrics_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _load_nav(run_dir: Path) -> list[dict[str, float]]:
    nav_path = run_dir / "equity_curve.csv"
    if not nav_path.exists():
        return []
    try:
        with open(nav_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = []
            for row in reader:
                if "nav" in row and "date" in row:
                    try:
                        rows.append({
                            "date": str(row["date"]),
                            "nav": float(row["nav"]),
                        })
                    except (TypeError, ValueError):
                        continue
        return _downsample(rows, 600)
    except OSError:
        return []


def _fmt_pct(v: Any) -> str:
    try:
        return f"{float(v) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_num(v: Any, digits: int = 3) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _svg_curve(points: list[dict[str, float]], width: int = 640, height: int = 240) -> str:
    """Build an inline SVG polyline for the nav curve (no external deps)."""
    if not points:
        return "<p class='muted'>无净值数据</p>"
    vals = [p["nav"] for p in points]
    vmin, vmax = min(vals), max(vals)
    span = (vmax - vmin) or 1.0
    pad = 12
    inner_w = width - pad * 2
    inner_h = height - pad * 2 - 24
    pts = []
    for i, p in enumerate(points):
        x = pad + (i / (len(points) - 1)) * inner_w
        y = pad + 24 + inner_h - ((p["nav"] - vmin) / span) * inner_h
        pts.append(f"{x:.1f},{y:.1f}")
    return (
        f"<svg viewBox='0 0 {width} {height}' width='100%' height='{height}' "
        f"xmlns='http://www.w3.org/2000/svg'>"
        f"<polyline fill='none' stroke='#3b82f6' stroke-width='2' "
        f"points='{' '.join(pts)}'/>"
        f"</svg>"
    )


_METRIC_LABELS = {
    "ann_return": "年化收益",
    "sharpe": "夏普比率",
    "max_dd": "最大回撤",
    "calmar": "卡玛比率",
    "sortino": "索提诺",
    "ann_vol": "年化波动",
    "turnover": "年换手",
    "win_rate": "胜率",
}


def _generate_report(run_dir: Path, strategy_name: str, run: str) -> bool:
    """Generate a self-contained report.html from run artifacts."""
    metrics = _load_metrics(run_dir)
    nav = _load_nav(run_dir)
    if not metrics and not nav:
        return False

    curve_svg = _svg_curve(nav)

    metric_cards = ""
    for key, label in _METRIC_LABELS.items():
        if key not in metrics:
            continue
        value = metrics[key]
        if key == "ann_return" or key == "max_dd":
            text = _fmt_pct(value)
        elif key == "turnover":
            text = _fmt_pct(value)
        else:
            text = _fmt_num(value)
        metric_cards += (
            f"<div class='metric'><div class='m-label'>{label}</div>"
            f"<div class='m-value'>{text}</div></div>"
        )
    if not metric_cards:
        metric_cards = "<p class='muted'>无指标</p>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{strategy_name} / {run} 回测报告</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0;
         padding: 16px; background: #0f172a; color: #e2e8f0; }}
  h1 {{ font-size: 15px; margin: 0 0 4px; }}
  .sub {{ color: #64748b; font-size: 12px; margin-bottom: 14px; }}
  .curve {{ background: #1e293b; border-radius: 8px; padding: 8px;
           margin-bottom: 14px; }}
  .metrics {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
             gap: 8px; }}
  .metric {{ background: #1e293b; border-radius: 8px; padding: 8px 10px; }}
  .m-label {{ color: #64748b; font-size: 11px; }}
  .m-value {{ font-family: ui-monospace, monospace; font-size: 13px;
              color: #38bdf8; margin-top: 2px; }}
  .muted {{ color: #64748b; font-size: 12px; }}
</style>
</head>
<body>
  <h1>{strategy_name} / {run}</h1>
  <div class="sub">回测分析报告 · 净值曲线与关键指标（自包含，离线可用）</div>
  <div class="curve">{curve_svg}</div>
  <div class="metrics">{metric_cards}</div>
</body>
</html>
"""
    try:
        report_path = run_dir / "report.html"
        report_path.write_text(html, encoding="utf-8")
        return True
    except OSError:
        logger.warning("failed to write report.html at %s", report_path, exc_info=True)
        return False


def register_display_tools(registry) -> None:
    """Register display tools (show_chart / show_report)."""
    registry.register(ShowChartTool())
    registry.register(ShowReportTool())


__all__ = [
    "ShowChartTool",
    "ShowReportTool",
    "register_display_tools",
]
