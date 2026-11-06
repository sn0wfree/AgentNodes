"""ASCII DAG renderer for workflow visualization (Phase 4 v0.5.5).

Renders a DAG (dict[str, list[str]]) as ASCII with Unicode box-drawing
characters. Supports status icons, selection markers, and progress display.

Algorithm:
  1. Compute topological layers via ``topological_layers()``
  2. For each layer, render nodes vertically aligned
  3. Draw edges as ``─▶`` between layers
  4. Add status icon prefix (✓ ⏳ ✗ ○)
  5. Highlight selected node with ``▸`` marker

Usage::

    from agentnodes.apps.goal.dag_renderer import render_dag, NodeStatus

    dag = {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]}
    status = {"A": NodeStatus.COMPLETED, "B": NodeStatus.RUNNING}
    print(render_dag(dag, status=status, selected="B", width=60))

Reference: docs/phase-4-plan.md §7.2.
"""
from __future__ import annotations

import enum

from agentnodes.kernel.workflow.dag import topological_layers, validate_dag


class NodeStatus(str, enum.Enum):
    """Status of a node in the DAG visualization."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    SKIPPED = "skipped"


# ── Icons ──────────────────────────────────────────────────────

_ICONS = {
    NodeStatus.PENDING: "○",
    NodeStatus.RUNNING: "⏳",
    NodeStatus.COMPLETED: "✓",
    NodeStatus.ERROR: "✗",
    NodeStatus.SKIPPED: "–",
}

_DEFAULT_ICON = "○"
_SELECTED_MARKER = "▸"
_EDGE_HORIZ = "─"
_EDGE_ARROW = "▶"
_EDGE_DOWN = "│"


def render_dag(
    dag: dict[str, list[str]],
    *,
    status: dict[str, NodeStatus] | None = None,
    selected: str | None = None,
    width: int = 60,
    max_name_len: int = 12,
) -> str:
    """Render a DAG as ASCII with Unicode box-drawing.

    Args:
        dag: Adjacency list ``{node: [upstream_deps]}``.
        status: Optional mapping ``{node: NodeStatus}`` for icons.
        selected: Node name to highlight with ``▸`` marker.
        width: Maximum line width (may be exceeded for wide DAGs).
        max_name_len: Maximum node name length before truncation.

    Returns:
        Multi-line string with the rendered DAG.

    Raises:
        ValueError: If the DAG contains a cycle.
    """
    if not dag:
        return "(empty DAG)"

    # Validate DAG
    try:
        validate_dag(dag)
    except ValueError:
        raise

    status = status or {}
    layers = topological_layers(dag)
    num_layers = len(layers)

    # Find max layer width (for alignment)
    max_nodes_per_layer = max(len(layer) for layer in layers) if layers else 1

    # Compute node name width
    all_nodes = set(dag.keys())
    for deps in dag.values():
        all_nodes.update(deps)
    max_name = max(len(n) for n in all_nodes) if all_nodes else 8
    name_width = min(max_name, max_name_len)

    # Build output lines
    lines: list[str] = []

    # Header
    lines.append(f"┌─ DAG ({len(all_nodes)} nodes, {num_layers} layers) "
                 + "─" * max(0, width - 40) + "┐")

    # Render each layer
    for layer_idx, layer in enumerate(layers):
        # Node rows (each node takes 2-3 lines)
        node_lines: list[list[str]] = []
        for node in layer:
            truncated = node[:max_name_len]
            icon = _ICONS.get(status.get(node, NodeStatus.PENDING), _DEFAULT_ICON)
            marker = _SELECTED_MARKER + " " if node == selected else "  "

            # Box line 1: top border
            box_w = name_width + 4  # padding + icon
            node_lines.append([f"┌{'─' * box_w}┐"])

            # Box line 2: content
            content = f"{marker}{icon} {truncated}"
            padded = content.ljust(box_w)
            node_lines.append([f"│{padded}│"])

            # Box line 3: bottom border
            node_lines.append([f"└{'─' * box_w}┘"])

        # Pad to max nodes in this layer
        while len(node_lines) < max_nodes_per_layer * 3:
            node_lines.append([" " * (name_width + 6)])

        # Interleave node boxes horizontally
        # For simplicity in v0.5.5, render nodes vertically per layer
        for row in node_lines:
            lines.append("  " + row[0])

        # Draw edges to next layer
        if layer_idx < num_layers - 1:
            next_layer = layers[layer_idx + 1]
            edge_parts = []
            for node in layer:
                # Find nodes in the next layer that depend on this node
                dependents = [n for n in next_layer if node in dag.get(n, [])]
                if dependents:
                    edge_parts.append(f"{_EDGE_HORIZ * 3}{_EDGE_ARROW}")
            if edge_parts:
                lines.append("  " + " ".join(edge_parts))

    # Footer
    completed = sum(1 for s in status.values() if s == NodeStatus.COMPLETED)
    total = len(all_nodes)
    pct = (completed / total * 100) if total > 0 else 0
    lines.append(f"└{'─' * (width - 2)}┘")
    lines.append(f"  {completed}/{total} complete ({pct:.0f}%)")

    if selected:
        lines.append(f"  Selected: {selected}")

    return "\n".join(lines)


__all__ = ["render_dag", "NodeStatus"]
