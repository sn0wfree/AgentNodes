"""Phase 4 — v0.5.5 tests: DAGView Textual widget.

TDD tests for the DAG visualization widget.

Covers:
  - DAGView mounts and renders
  - update_dag() re-renders
  - update_status() shows icons
  - Keyboard navigation (j/k/h/l)
  - Selected node highlight
  - close_view removes widget

Reference: docs/phase-4-plan.md §7.3.
"""
from __future__ import annotations

from strategy_research.apps.goal.dag_renderer import NodeStatus

# ─── Basic rendering ──────────────────────────────────────────────────


class TestDAGViewRendering:
    """DAGView should render the DAG correctly."""

    async def test_mount_renders_dag(self):
        from strategy_research.cli.tui.app import ResearchApp

        app = ResearchApp(skip_resume=True)
        async with app.run_test() as pilot:
            from strategy_research.cli.tui.widgets.dag_view import DAGView
            dag_view = DAGView(
                dag={"A": [], "B": ["A"]},
                workflow_name="test",
            )
            app.query_one("#transcript").mount(dag_view)
            await pilot.pause()
            assert dag_view.is_mounted

    async def test_update_dag(self):
        from strategy_research.cli.tui.app import ResearchApp
        from strategy_research.cli.tui.widgets.dag_view import DAGView

        app = ResearchApp(skip_resume=True)
        async with app.run_test() as pilot:
            dag_view = DAGView(dag={"A": []})
            app.query_one("#transcript").mount(dag_view)
            await pilot.pause()
            # Update with a new DAG
            dag_view.update_dag({"X": [], "Y": ["X"]})
            await pilot.pause()
            # Should not crash

    async def test_update_status(self):
        from strategy_research.cli.tui.app import ResearchApp
        from strategy_research.cli.tui.widgets.dag_view import DAGView

        app = ResearchApp(skip_resume=True)
        async with app.run_test() as pilot:
            dag_view = DAGView(dag={"A": [], "B": ["A"]})
            app.query_one("#transcript").mount(dag_view)
            await pilot.pause()
            dag_view.update_status({"A": NodeStatus.COMPLETED})
            await pilot.pause()

    async def test_set_selected(self):
        from strategy_research.cli.tui.app import ResearchApp
        from strategy_research.cli.tui.widgets.dag_view import DAGView

        app = ResearchApp(skip_resume=True)
        async with app.run_test() as pilot:
            dag_view = DAGView(dag={"A": [], "B": ["A"]})
            app.query_one("#transcript").mount(dag_view)
            await pilot.pause()
            dag_view.set_selected("B")
            await pilot.pause()
            assert dag_view._selected == "B"


# ─── Keyboard navigation ──────────────────────────────────────────────


class TestDAGViewNavigation:
    """Keyboard bindings should navigate the DAG."""

    async def test_j_k_navigation(self):
        from strategy_research.cli.tui.app import ResearchApp
        from strategy_research.cli.tui.widgets.dag_view import DAGView

        app = ResearchApp(skip_resume=True)
        async with app.run_test() as pilot:
            dag_view = DAGView(dag={"A": [], "B": ["A"], "C": ["B"]})
            app.query_one("#transcript").mount(dag_view)
            await pilot.pause()
            nodes = dag_view._all_nodes
            # Start at first node
            dag_view.set_selected(nodes[0])
            # j moves to next
            dag_view.action_select_next()
            assert dag_view._selected == nodes[1]
            # j again
            dag_view.action_select_next()
            assert dag_view._selected == nodes[2]
            # k moves back
            dag_view.action_select_prev()
            assert dag_view._selected == nodes[1]

    async def test_h_l_parent_child(self):
        from strategy_research.cli.tui.app import ResearchApp
        from strategy_research.cli.tui.widgets.dag_view import DAGView

        app = ResearchApp(skip_resume=True)
        async with app.run_test() as pilot:
            dag_view = DAGView(dag={"A": [], "B": ["A"], "C": ["B"]})
            app.query_one("#transcript").mount(dag_view)
            await pilot.pause()
            dag_view.set_selected("C")
            # h moves to parent
            dag_view.action_select_parent()
            assert dag_view._selected == "B"
            # h again
            dag_view.action_select_parent()
            assert dag_view._selected == "A"
            # l moves to child
            dag_view.action_select_child()
            assert dag_view._selected == "B"

    async def test_close_view(self):
        from strategy_research.cli.tui.app import ResearchApp
        from strategy_research.cli.tui.widgets.dag_view import DAGView

        app = ResearchApp(skip_resume=True)
        async with app.run_test() as pilot:
            dag_view = DAGView(dag={"A": []})
            app.query_one("#transcript").mount(dag_view)
            await pilot.pause()
            assert dag_view.is_mounted
            # action_close_view removes widget
            dag_view.action_close_view()
            await pilot.pause()
            # Widget should be removed — query returns empty list
            assert len(app.query("DAGView")) == 0
