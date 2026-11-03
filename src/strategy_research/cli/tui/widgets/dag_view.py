"""DAGView — ASCII DAG visualization widget for Textual TUI (Phase 4 v0.5.5).

Displays a workflow DAG as ASCII with Unicode box-drawing, status icons,
and keyboard navigation.

Key bindings:
  j/k — select next/prev node
  h/l — select parent/child
  Enter — edit node details
  e — edit YAML
  : — command mode (placeholder)
  q — close DAG view

Usage::

    from strategy_research.cli.tui.widgets.dag_view import DAGView

    dag_view = DAGView(dag={"A": [], "B": ["A"], "C": ["A"]})
    dag_view.update_status({"A": NodeStatus.COMPLETED})

Reference: docs/phase-4-plan.md §7.3.
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Static

from strategy_research.apps.goal.dag_renderer import NodeStatus, render_dag


class DAGView(Vertical):
    """Display an ASCII DAG with selection state and key bindings.

    This widget renders a workflow DAG using ``render_dag()`` and
    provides keyboard navigation to select nodes for inspection or
    editing.
    """

    BINDINGS = [
        Binding("j", "select_next", "Next", show=True),
        Binding("k", "select_prev", "Prev", show=True),
        Binding("h", "select_parent", "Parent", show=True),
        Binding("l", "select_child", "Child", show=True),
        Binding("enter", "edit_node", "Edit", show=True),
        Binding("e", "edit_yaml", "YAML", show=True),
        Binding("q", "close_view", "Close", show=True),
    ]

    def __init__(
        self,
        dag: dict[str, list[str]],
        *,
        workflow_name: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._dag = dag
        self._workflow_name = workflow_name
        self._status: dict[str, NodeStatus] = {}
        self._selected: str | None = None
        self._all_nodes: list[str] = []
        self._node_index: int = 0
        self._rebuild_node_list()

    def compose(self) -> ComposeResult:
        self._renderer = Static(id="dag-renderer")
        yield self._renderer

    def on_mount(self) -> None:
        self._render_dag()

    # ── Public API ──────────────────────────────────────────────

    def update_dag(self, dag: dict[str, list[str]]) -> None:
        """Replace the DAG and re-render."""
        self._dag = dag
        self._rebuild_node_list()
        self._render_dag()

    def update_status(self, status: dict[str, NodeStatus]) -> None:
        """Update node statuses and re-render."""
        self._status = status
        self._render_dag()

    def set_selected(self, node: str | None) -> None:
        """Set the selected node and re-render."""
        self._selected = node
        if node and node in self._all_nodes:
            self._node_index = self._all_nodes.index(node)
        self._render_dag()

    # ── Key bindings ────────────────────────────────────────────

    def action_select_next(self) -> None:
        """Move selection to the next node."""
        if not self._all_nodes:
            return
        self._node_index = (self._node_index + 1) % len(self._all_nodes)
        self._selected = self._all_nodes[self._node_index]
        self._render_dag()

    def action_select_prev(self) -> None:
        """Move selection to the previous node."""
        if not self._all_nodes:
            return
        self._node_index = (self._node_index - 1) % len(self._all_nodes)
        self._selected = self._all_nodes[self._node_index]
        self._render_dag()

    def action_select_parent(self) -> None:
        """Move selection to the first parent (upstream dependency)."""
        if not self._selected:
            return
        deps = self._dag.get(self._selected, [])
        if deps:
            self._selected = deps[0]
            if self._selected in self._all_nodes:
                self._node_index = self._all_nodes.index(self._selected)
        self._render_dag()

    def action_select_child(self) -> None:
        """Move selection to the first child (downstream dependent)."""
        if not self._selected:
            return
        for node, deps in self._dag.items():
            if self._selected in deps:
                self._selected = node
                if self._selected in self._all_nodes:
                    self._node_index = self._all_nodes.index(self._selected)
                break
        self._render_dag()

    def action_edit_node(self) -> None:
        """Edit the selected node (placeholder)."""
        if self._selected:
            self.notify(f"Edit node: {self._selected} (not yet implemented)")

    def action_edit_yaml(self) -> None:
        """Edit the workflow YAML (placeholder)."""
        self.notify("YAML editor (not yet implemented)")

    def action_close_view(self) -> None:
        """Close the DAG view."""
        self.remove()

    # ── Internals ───────────────────────────────────────────────

    def _rebuild_node_list(self) -> None:
        """Build a flat list of all nodes in topological order."""
        from strategy_research.kernel.workflow.dag import topological_layers
        layers = topological_layers(self._dag)
        self._all_nodes = [node for layer in layers for node in layer]
        if self._selected not in self._all_nodes:
            self._selected = None
            self._node_index = 0

    def _render_dag(self) -> None:
        """Re-render the DAG into the Static widget."""
        try:
            renderer = self.query_one("#dag-renderer", Static)
        except Exception:
            return

        text = render_dag(
            self._dag,
            status=self._status,
            selected=self._selected,
            width=self.size.width if self.size.width > 0 else 60,
        )
        renderer.update(text)


__all__ = ["DAGView"]
