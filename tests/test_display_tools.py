"""Display tools tests: show_chart / show_report / projector html part.

Covers docs/right-panel-agent-driven.md:
- show_chart: file-reference reading, downsampling, emit, traversal
- show_report: on-demand report generation + html emit
- projector._on_html: html part persistence (reload keeps reports)
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.events.event_v2 import EventType, EventV2
from strategy_research.api.session.projector import ProjectedSession, Projector
from strategy_research.kernel.agent.builtin_tools.display_tools import (
    ShowChartTool,
    ShowReportTool,
    _downsample,
    _generate_report,
    _svg_curve,
)
from strategy_research.kernel.agent.tools import ToolContext


class _Ctx(ToolContext):
    """ToolContext that records emitted events."""

    def __init__(self, workspace: Path):
        self.events: list[tuple[str, dict]] = []
        super().__init__(
            workspace=workspace,
            session_id="s1",
            emit_event=lambda t, d: self.events.append((t, d)),
        )


def _make_run(ws: Path, n: int = 50) -> Path:
    run_dir = ws / "runs" / "momentum_20d" / "run_0007"
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "equity_curve.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "nav"])
        for i in range(n):
            w.writerow([f"2024-01-{i % 28 + 1:02d}", round(1.0 + i * 0.01, 4)])
    with open(run_dir / "metrics.json", "w") as f:
        json.dump({"ann_return": 0.15, "sharpe": 1.2, "max_dd": -0.08}, f)
    return run_dir


class TestShowChartTool(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self.run_dir = _make_run(self.ws)
        self.tool = ShowChartTool()
        self.ctx = _Ctx(self.ws)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reads_csv_and_emits_chart(self) -> None:
        res = json.loads(self.tool.execute(
            self.ctx,
            source_file="runs/momentum_20d/run_0007/equity_curve.csv",
            chart_type="line",
            title="净值曲线",
        ))
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["points"], 50)
        self.assertEqual(res["downsampled_from"], 50)
        self.assertEqual(len(self.ctx.events), 1)
        etype, data = self.ctx.events[0]
        self.assertEqual(etype, "chart")
        self.assertEqual(data["chart_type"], "line")
        self.assertEqual(data["title"], "净值曲线")
        self.assertEqual(len(data["data"]), 50)
        self.assertIn("nav", data["data"][0])

    def test_downsampling_budget(self) -> None:
        _make_run(self.ws, n=1200)
        res = json.loads(self.tool.execute(
            self.ctx,
            source_file="runs/momentum_20d/run_0007/equity_curve.csv",
        ))
        self.assertEqual(res["points"], 500)
        self.assertEqual(res["downsampled_from"], 1200)
        emitted = self.ctx.events[-1][1]["data"]
        self.assertEqual(len(emitted), 500)

    def test_traversal_blocked(self) -> None:
        res = json.loads(self.tool.execute(self.ctx, source_file="../../etc/passwd"))
        self.assertEqual(res["status"], "error")
        self.assertIn("escape", res.get("error", ""))
        self.assertEqual(self.ctx.events, [])

    def test_missing_file(self) -> None:
        res = json.loads(self.tool.execute(self.ctx, source_file="runs/nope.csv"))
        self.assertEqual(res["status"], "error")
        self.assertEqual(self.ctx.events, [])

    def test_invalid_chart_type(self) -> None:
        res = json.loads(self.tool.execute(
            self.ctx, source_file="runs/momentum_20d/run_0007/equity_curve.csv",
            chart_type="heatmap",
        ))
        self.assertEqual(res["status"], "error")

    def test_json_source(self) -> None:
        with open(self.ws / "points.json", "w") as f:
            json.dump([{"label": "a", "value": 1}, {"label": "b", "value": 2}], f)
        res = json.loads(self.tool.execute(self.ctx, source_file="points.json"))
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(self.ctx.events[0][1]["data"]), 2)

    def test_no_emit_when_callback_missing(self) -> None:
        ctx = ToolContext(workspace=self.ws, session_id="s1", emit_event=None)
        res = json.loads(self.tool.execute(
            ctx, source_file="runs/momentum_20d/run_0007/equity_curve.csv",
        ))
        self.assertEqual(res["status"], "ok")


class TestShowReportTool(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self.run_dir = _make_run(self.ws)
        self.tool = ShowReportTool()
        self.ctx = _Ctx(self.ws)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_generates_and_emits_html(self) -> None:
        res = json.loads(self.tool.execute(
            self.ctx, strategy_name="momentum_20d", run="run_0007",
        ))
        self.assertEqual(res["status"], "ok")
        self.assertTrue((self.run_dir / "report.html").exists())
        self.assertEqual(len(self.ctx.events), 1)
        etype, data = self.ctx.events[0]
        self.assertEqual(etype, "html")
        self.assertIn("<svg", data["content"])
        self.assertIn("年化收益", data["content"])
        self.assertEqual(data["title"], "momentum_20d / run_0007 回测报告")

    def test_reuses_existing_report(self) -> None:
        report = self.run_dir / "report.html"
        report.write_text("<html>custom</html>", encoding="utf-8")
        res = json.loads(self.tool.execute(
            self.ctx, strategy_name="momentum_20d", run="run_0007",
        ))
        self.assertEqual(res["status"], "ok")
        self.assertEqual(self.ctx.events[0][1]["content"], "<html>custom</html>")

    def test_missing_run_artifacts(self) -> None:
        res = json.loads(self.tool.execute(self.ctx, strategy_name="nope", run="run_1"))
        self.assertEqual(res["status"], "error")
        self.assertEqual(self.ctx.events, [])

    def test_traversal_blocked(self) -> None:
        res = json.loads(self.tool.execute(self.ctx, strategy_name="../../etc", run="x"))
        self.assertEqual(res["status"], "error")

    def test_generate_report_returns_false_without_artifacts(self) -> None:
        empty = self.ws / "runs" / "empty" / "run_1"
        empty.mkdir(parents=True)
        self.assertFalse(_generate_report(empty, "empty", "run_1"))


class TestProjectorHtmlPart(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Projector(Path(self._tmp.name) / "t.db")
        self.state = ProjectedSession(session_id="s1")
        # seed an assistant message so the part has a home
        evt = EventV2(
            id="assistant-1", aggregate_id="s1", seq=1,
            type=EventType.ASSISTANT_MESSAGE,
            data={"message_id": "m1", "content": "报告如下"},
            time_created=1000.0,
        )
        self.proj._apply(evt, self.state)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_persists_html_part(self) -> None:
        evt = EventV2(
            id="html-1", aggregate_id="s1", seq=2,
            type=EventType.HTML,
            data={"message_id": "m1", "id": "report-1", "title": "报告", "content": "<html>x</html>"},
            time_created=1001.0,
        )
        self.proj._apply(evt, self.state)
        msg = self.state.messages["m1"]
        part = msg.parts["report-1"]
        self.assertEqual(part.type, "html")
        self.assertEqual(part.data["content"], "<html>x</html>")

    def test_idempotent_on_replay(self) -> None:
        evt = EventV2(
            id="html-1", aggregate_id="s1", seq=2,
            type=EventType.HTML,
            data={"message_id": "m1", "id": "report-1", "content": "<html>a</html>"},
            time_created=1001.0,
        )
        self.proj._apply(evt, self.state)
        self.proj._apply(evt, self.state)
        msg = self.state.messages["m1"]
        self.assertEqual(len(msg.parts), 1)


class TestDownsampleAndSvg(unittest.TestCase):
    def test_downsample_keeps_last_point(self) -> None:
        points = [{"i": i} for i in range(1000)]
        out = _downsample(points, 100)
        self.assertEqual(len(out), 100)
        self.assertEqual(out[-1], points[-1])

    def test_no_downsample_when_within_budget(self) -> None:
        points = [{"i": i} for i in range(10)]
        self.assertEqual(_downsample(points, 500), points)

    def test_svg_curve_renders(self) -> None:
        svg = _svg_curve([{"nav": 1.0}, {"nav": 1.05}, {"nav": 1.03}])
        self.assertIn("<svg", svg)
        self.assertIn("polyline", svg)

    def test_svg_empty_points(self) -> None:
        self.assertIn("无净值数据", _svg_curve([]))


if __name__ == "__main__":
    unittest.main()
