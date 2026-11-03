"""Built-in study graph templates.

Each template is a ``StudyGraph`` frozen at module import. The runner
falls back to ``DEFAULT_STANDARD_GRAPH`` whenever ``graph.json`` is
missing or malformed (legacy studies; see migration script).

Templates:
    * ``DEFAULT_STANDARD_GRAPH`` — 8 agents, multi-entry (researcher →
      dq + fa) and multi-exit (risk_controller → attribution + aoa).
    * ``MINIMAL_GRAPH`` — 3 agents, linear. For quick experiments.
    * ``EXPLORE_GRAPH`` — adds an optional ``explore`` feedback branch.
"""
from __future__ import annotations

from .graph import GraphEdge, GraphNode, StudyGraph


def _node(id_: str, type_: str, label: str) -> GraphNode:
    return GraphNode(id=id_, type=type_, label=label, config={}, enabled=True)


# ── Standard: 8 agents, multi-entry + multi-exit ─────────────────

DEFAULT_STANDARD_GRAPH: StudyGraph = StudyGraph(
    nodes=(
        _node("researcher", "llm_agent", "Researcher"),
        _node("data_quality", "evaluator", "Data Quality"),
        _node("factor_analyst", "llm_agent", "Factor Analyst"),
        _node("strategist", "planner", "Strategist"),
        _node("portfolio_construction", "llm_agent", "Portfolio"),
        _node("risk_controller", "evaluator", "Risk Control"),
        _node("attribution_analyst", "evaluator", "Attribution"),
        _node("anti_overfit_analyst", "evaluator", "Anti-Overfit"),
    ),
    edges=(
        # Multi-entry: researcher fans out to data_quality + factor_analyst
        GraphEdge(source="researcher", target="data_quality"),
        GraphEdge(source="researcher", target="factor_analyst"),
        # Multi-entry: strategist waits on both dq and fa
        GraphEdge(source="data_quality", target="strategist"),
        GraphEdge(source="factor_analyst", target="strategist"),
        # Standard chain
        GraphEdge(source="strategist", target="portfolio_construction"),
        GraphEdge(source="portfolio_construction", target="risk_controller"),
        # Multi-exit: risk_controller fans out to attribution + aoa
        GraphEdge(source="risk_controller", target="attribution_analyst"),
        GraphEdge(source="risk_controller", target="anti_overfit_analyst"),
    ),
)


# ── Minimal: 3 agents, single entry + exit, linear ──────────────

MINIMAL_GRAPH: StudyGraph = StudyGraph(
    nodes=(
        _node("researcher", "llm_agent", "Researcher"),
        _node("strategist", "planner", "Strategist"),
        _node("backtest", "tool", "Backtest"),
    ),
    edges=(
        GraphEdge(source="researcher", target="strategist"),
        GraphEdge(source="strategist", target="backtest"),
    ),
)


# ── Explore: standard + extra explore node + feedback loop ───────
# Same shape as standard but with an additional ``explore`` agent that
# runs in parallel with the strategist and writes extra knowledge.

EXPLORE_GRAPH: StudyGraph = StudyGraph(
    nodes=(
        _node("researcher", "llm_agent", "Researcher"),
        _node("data_quality", "evaluator", "Data Quality"),
        _node("factor_analyst", "llm_agent", "Factor Analyst"),
        _node("explore", "llm_agent", "Explore"),
        _node("strategist", "planner", "Strategist"),
        _node("portfolio_construction", "llm_agent", "Portfolio"),
        _node("risk_controller", "evaluator", "Risk Control"),
        _node("attribution_analyst", "evaluator", "Attribution"),
        _node("anti_overfit_analyst", "evaluator", "Anti-Overfit"),
    ),
    edges=(
        # researcher fan-out
        GraphEdge(source="researcher", target="data_quality"),
        GraphEdge(source="researcher", target="factor_analyst"),
        GraphEdge(source="researcher", target="explore"),
        # strategist waits on dq + fa + explore
        GraphEdge(source="data_quality", target="strategist"),
        GraphEdge(source="factor_analyst", target="strategist"),
        GraphEdge(source="explore", target="strategist"),
        # chain
        GraphEdge(source="strategist", target="portfolio_construction"),
        GraphEdge(source="portfolio_construction", target="risk_controller"),
        # risk multi-exit
        GraphEdge(source="risk_controller", target="attribution_analyst"),
        GraphEdge(source="risk_controller", target="anti_overfit_analyst"),
    ),
)


TEMPLATES: dict[str, StudyGraph] = {
    "standard": DEFAULT_STANDARD_GRAPH,
    "minimal": MINIMAL_GRAPH,
    "explore": EXPLORE_GRAPH,
}


def get_template(name: str) -> StudyGraph:
    """Return a fresh copy of the named template (caller may mutate)."""
    if name not in TEMPLATES:
        name = "standard"
    # Rebuild from dict so callers can't mutate the shared frozen instance.
    return StudyGraph.from_dict(TEMPLATES[name].to_dict())


__all__ = [
    "DEFAULT_STANDARD_GRAPH",
    "MINIMAL_GRAPH",
    "EXPLORE_GRAPH",
    "TEMPLATES",
    "get_template",
]
