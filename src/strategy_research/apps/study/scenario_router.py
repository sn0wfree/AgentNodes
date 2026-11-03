"""Scenario router — LLM-driven agent-subset orchestration at study start.

Design: ``docs/scenario-router-design.md`` (v3 — free composition with
principle guardrails).

Three-layer prompt: (1) hard principles, (2) typical scenario archetypes
as few-shot examples (reference only, not a menu), (3) the factual agent
catalog + study inputs. The LLM composes the subset freely; code-side
validation enforces the principles regardless of what the LLM returns.

Fallback chain — never raises:
    LLM (once) -> keyword fallback -> DEFAULT_STANDARD_GRAPH (all 8)

Also hosts :func:`detect_max_iter_placeholders`, the same-round
short-circuit probe merged from adaptive_retry (§4b of the design doc):
when an upstream agent produced a max-iterations placeholder instead of
structured JSON, the round's downstream finalize/review work is skipped
to avoid burning further LLM calls on garbage inputs.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Agent universe (matches DEFAULT_STANDARD_GRAPH node ids) ────────

AGENT_CATALOG: tuple[dict[str, str], ...] = (
    {"id": "researcher", "kind": "llm",
     "role": "提出研究假设与因子方向"},
    {"id": "data_quality", "kind": "evaluator",
     "role": "数据质量检查（NaN/缺失/异常）"},
    {"id": "factor_analyst", "kind": "llm",
     "role": "发现并验证因子（IC/IR）"},
    {"id": "strategist", "kind": "planner",
     "role": "将因子集成进策略，产出参数改动"},
    {"id": "portfolio_construction", "kind": "llm",
     "role": "多资产权重配置（风险平价等）；单标的不适用"},
    {"id": "risk_controller", "kind": "evaluator",
     "role": "风控阈值判定（VaR/MaxDD 等）"},
    {"id": "attribution_analyst", "kind": "evaluator",
     "role": "业绩归因解释收益来源"},
    {"id": "anti_overfit_analyst", "kind": "llm",
     "role": "抗过拟合稳健性结论（keep/discard 输入）"},
)
ALL_AGENT_IDS = frozenset(a["id"] for a in AGENT_CATALOG)

# Graph-node level mandatory trio. The design doc's "四件套" includes
# ``backtest``, which always executes as a tool and is not a selectable
# graph node.
MANDATORY_AGENTS = frozenset({"researcher", "strategist", "risk_controller"})

MAX_AGENTS = len(ALL_AGENT_IDS)

# ── Route I/O ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class RouteInput:
    objective: str
    strategy_name: str | None = None
    workspace_path: Path | None = None
    metric_targets: list[dict] | None = None


@dataclass(frozen=True)
class RouteResult:
    selected_agents: list[str]
    llm_selected: list[str]
    repaired: bool
    repair_notes: list[str]
    reasoning: str
    confidence: float
    source: str                       # "llm" | "keyword_fallback" | "default_graph"


# ── Prompt (three layers per design §2.2) ───────────────────────────

_SCENARIO_EXAMPLES = """\
典型场景示例（仅供参考，不是菜单——你的选择应基于目标本身，可以自由组合或新增）：
- 调参找最优组合：researcher + strategist + risk_controller + anti_overfit_analyst
- 发现并验证新因子：researcher + factor_analyst + strategist + risk_controller + attribution_analyst
- 多资产组合配置：researcher + strategist + portfolio_construction + risk_controller
- 策略复盘 / 归因分析：researcher + attribution_analyst + risk_controller
- 风险评估：researcher + risk_controller + anti_overfit_analyst"""

_PRINCIPLES = """\
原则（必须遵守）：
1.【最小流水线】researcher、strategist、risk_controller 必选；backtest 工具恒定执行，不在选择范围。
2.【按需增选】其余 agent 仅当目标明确需要时才加入，并在 reasoning 中逐个说明理由。
3.【节俭】在能满足目标的前提下选最少的 agent——每多一个多一份成本和失败面。
4.【不确定从紧】拿不准是否需要的 agent，倾向不选（后续轮次可加）。"""


def _catalog_text() -> str:
    lines = [f"- {a['id']} ({a['kind']}): {a['role']}" for a in AGENT_CATALOG]
    return "\n".join(lines)


def build_system_prompt() -> str:
    return f"""\
你是量化研究编排助手。根据用户的研究目标，从可用 agent 目录中选出本轮该执行的集合。

{_PRINCIPLES}

{_SCENARIO_EXAMPLES}

可用 agent 目录：
{_catalog_text()}

严格 JSON 输出（单一对象，无 markdown 标记、无前后解释）：
{{
  "selected_agents": ["<agent_id>", "..."],
  "reasoning": "<一段话说明取舍：为什么选这些、为什么不选其余>",
  "confidence": <0 到 1 之间的小数>
}}"""


# ── Keyword fallback (~30 LOC, mirrors the archetypes) ──────────────

_KEYWORD_AGENT_MAP: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (("因子", "factor", "ic", "ir", "alpha"),
     frozenset({"researcher", "factor_analyst", "strategist", "risk_controller"})),
    (("调参", "参数", "优化", "tune", "grid"),
     frozenset({"researcher", "strategist", "risk_controller",
                "anti_overfit_analyst"})),
    (("组合", "权重", "风险平价", "portfolio", "rebalance"),
     frozenset({"researcher", "strategist", "portfolio_construction",
                "risk_controller"})),
    (("复盘", "归因", "review", "报告"),
     frozenset({"researcher", "attribution_analyst", "risk_controller"})),
    (("风控", "风险", "回撤", "var", "cvar", "压力测试"),
     frozenset({"researcher", "risk_controller", "anti_overfit_analyst"})),
)


def keyword_fallback(objective: str) -> list[str]:
    text = objective.lower()
    best: tuple[int, frozenset[str]] | None = None
    for keywords, agents in _KEYWORD_AGENT_MAP:
        score = sum(1 for kw in keywords if kw in text)
        if score > 0 and (best is None or score > best[0]):
            best = (score, agents)
    if best is None:
        return []
    return sorted(best[1])


# ── Single vs multi asset heuristic (design §4 step 4) ──────────────

_MULTI_ASSET_HINTS = ("weight", "risk_parity", "rebalance", "allocation",
                      "portfolio", "权重", "风险平价")


def detect_asset_universe(strategy_name: str | None,
                          workspace_path: Path | None) -> str:
    """Best-effort 'single' | 'multi' classification from strategy sources.

    False 'single' is the safe default: it only disables
    portfolio_construction, which is meaningless for a single asset anyway.
    """
    if strategy_name is None or workspace_path is None:
        return "single"
    strat_dir = Path(workspace_path) / "strategies" / strategy_name
    if not strat_dir.is_dir():
        return "single"
    try:
        for p in sorted(strat_dir.rglob("*.py"))[:20]:   # bounded scan
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:20000]
            except OSError:
                continue
            low = head.lower()
            if any(h in low for h in _MULTI_ASSET_HINTS):
                return "multi"
    except OSError:
        return "single"
    return "single"


# ── LLM call ────────────────────────────────────────────────────────

def _default_llm_client():
    from strategy_research.foundation.llm.config import LLMConfig
    from strategy_research.foundation.llm.openai_client import OpenAICompatClient

    cfg = LLMConfig.load(load_dotenv=True)
    return OpenAICompatClient(cfg)


def _llm_select(inp: RouteInput, client) -> tuple[list[str], str, float] | None:
    """One LLM attempt. Returns (agents, reasoning, confidence) or None."""
    user_msg = (
        f"研究目标：{inp.objective}\n"
        f"strategy_name：{inp.strategy_name or '-'}\n"
        f"metric_targets：{json.dumps(inp.metric_targets or [], ensure_ascii=False)}"
    )
    resp = client.chat(
        [{"role": "system", "content": build_system_prompt()},
         {"role": "user", "content": user_msg}],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    raw = getattr(resp, "content", None) or getattr(resp, "text", "") or ""
    parsed = json.loads(raw)
    agents = parsed.get("selected_agents")
    reasoning = str(parsed.get("reasoning", ""))[:600]
    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    if not isinstance(agents, list) or not all(isinstance(a, str) for a in agents):
        raise ValueError("selected_agents must be a list[str]")
    return agents, reasoning, min(max(confidence, 0.0), 1.0)


# ── Validation pipeline (principles enforced by code) ───────────────

def _validate_and_repair(llm_selected: list[str], inp: RouteInput,
                         reasoning: str, confidence: float,
                         ) -> RouteResult:
    notes: list[str] = []
    picked = [a for a in llm_selected if a in ALL_AGENT_IDS]
    dropped = sorted(set(llm_selected) - set(picked))
    if dropped:
        notes.append(f"dropped unknown ids: {dropped}")

    missing = sorted(MANDATORY_AGENTS - set(picked))
    if missing:
        notes.append(f"forced mandatory agents: {missing}")
        catalog_order = [a["id"] for a in AGENT_CATALOG]
        picked = sorted(set(picked) | MANDATORY_AGENTS,
                        key=catalog_order.index)

    if detect_asset_universe(inp.strategy_name, inp.workspace_path) == "single":
        if "portfolio_construction" in picked:
            notes.append("removed portfolio_construction (single asset)")
        picked = [a for a in picked if a != "portfolio_construction"]

    if not picked:
        notes.append("empty selection -> default graph")
        return default_graph_result(inp, notes)

    repaired = bool(notes)
    return RouteResult(
        selected_agents=picked,
        llm_selected=list(llm_selected),
        repaired=repaired,
        repair_notes=notes,
        reasoning=reasoning or "(no reasoning returned)",
        confidence=confidence,
        source="llm",
    )


def default_graph_result(inp: RouteInput,
                         notes: list[str] | None = None) -> RouteResult:
    """Full standard graph, minus portfolio_construction for single-asset
    strategies (safe default — multi is only inferred when strategy
    sources carry weight-assignment hints)."""
    agents = set(ALL_AGENT_IDS)
    if detect_asset_universe(inp.strategy_name, inp.workspace_path) != "multi":
        agents.discard("portfolio_construction")
    return RouteResult(
        selected_agents=sorted(agents),
        llm_selected=[],
        repaired=bool(notes),
        repair_notes=notes or [],
        reasoning="fallback to full standard graph",
        confidence=0.0,
        source="default_graph",
    )


# ── Public entry point ──────────────────────────────────────────────

def route(inp: RouteInput, llm_client=None) -> RouteResult:
    """Select the agent subset for this study. Never raises."""
    client = llm_client
    if client is None:
        try:
            client = _default_llm_client()
        except Exception as exc:  # noqa: BLE001 — config problems degrade
            logger.warning("scenario_router: no LLM client (%s)", exc)
            client = None

    if client is not None:
        try:
            agents, reasoning, conf = _llm_select(inp, client)
            return _validate_and_repair(agents, inp, reasoning, conf)
        except Exception as exc:  # noqa: BLE001 — fallback chain next
            logger.warning("scenario_router: LLM route failed (%s); "
                           "falling back to keywords", exc)

    kw = keyword_fallback(inp.objective)
    if kw:
        result = _validate_and_repair(kw, inp, "keyword_fallback", 0.3)
        return RouteResult(
            selected_agents=result.selected_agents,
            llm_selected=result.llm_selected,
            repaired=result.repaired,
            repair_notes=result.repair_notes,
            reasoning=result.reasoning,
            confidence=result.confidence,
            source="keyword_fallback",
        )
    return default_graph_result(inp)


def build_graph_for_selection(selected: list[str]):
    """Materialize a StudyGraph containing only ``selected`` nodes.

    Edges are inherited from DEFAULT_STANDARD_GRAPH when both endpoints
    survive the cut. A naive cut can orphan downstream nodes — e.g.
    removing portfolio_construction leaves risk_controller with no
    upstream, which the engines treat as a START entry and run it in
    parallel with the researcher (semantics destroyed). Any selected
    node that lost all of its upstreams is therefore re-attached to the
    nearest preceding selected node in catalog order (researcher being
    the sole legitimate entry).
    """
    from .graph import GraphEdge, StudyGraph
    from .graph_templates import DEFAULT_STANDARD_GRAPH

    keep = set(selected) & ALL_AGENT_IDS
    if not keep:
        return DEFAULT_STANDARD_GRAPH

    catalog_order = [a["id"] for a in AGENT_CATALOG]
    nodes = tuple(n for n in DEFAULT_STANDARD_GRAPH.nodes if n.id in keep)

    edges: list[GraphEdge] = []
    incoming: dict[str, list[str]] = {}
    for e in DEFAULT_STANDARD_GRAPH.edges:
        if e.source in keep and e.target in keep:
            edges.append(GraphEdge(source=e.source, target=e.target))
            incoming.setdefault(e.target, []).append(e.source)

    # Re-attach nodes orphaned by the cut.
    for node_id in sorted(keep, key=catalog_order.index):
        if incoming.get(node_id):
            continue
        if node_id == "researcher":
            continue  # the legitimate entry point
        idx = catalog_order.index(node_id)
        anchor = next(
            (c for c in reversed(catalog_order[:idx]) if c in keep),
            None,
        )
        if anchor is None:  # nothing precedes it in catalog order
            continue
        edges.append(GraphEdge(source=anchor, target=node_id))

    return StudyGraph(nodes=nodes, edges=tuple(edges))


# ── Same-round short-circuit probe (design §4b) ─────────────────────

_MAX_ITER_RE = re.compile(r"Reached max_iterations\s*=", re.IGNORECASE)
_TEXT_FIELDS = ("final_answer", "final_message", "content", "message",
                "text", "error", "output", "result")


def detect_max_iter_placeholders(agent_outputs: dict | None) -> list[str]:
    """Agent ids whose output is a max-iterations placeholder rather than
    structured content. Tolerant of both raw-string and dict payloads;
    false positives only cost us one skipped review cycle, so bias toward
    detection.

    Keys are normalized (``xxx_output`` -> ``xxx``) to match real agent
    ids — phase_engine's aggregated map carries the ``_output`` suffix.
    """
    if not isinstance(agent_outputs, dict):
        return []
    failed: set[str] = set()
    for raw_key, out in agent_outputs.items():
        agent_id = re.sub(r"_output$", "", str(raw_key))
        if isinstance(out, str):
            texts = [out]
        elif isinstance(out, dict):
            texts = [str(out.get(k, "")) for k in _TEXT_FIELDS]
        else:
            continue
        if any(_MAX_ITER_RE.search(t) for t in texts if t):
            failed.add(agent_id)
    return sorted(failed)


__all__ = [
    "RouteInput",
    "RouteResult",
    "route",
    "keyword_fallback",
    "detect_asset_universe",
    "detect_max_iter_placeholders",
    "build_system_prompt",
    "build_graph_for_selection",
    "AGENT_CATALOG",
    "MANDATORY_AGENTS",
    "ALL_AGENT_IDS",
]
