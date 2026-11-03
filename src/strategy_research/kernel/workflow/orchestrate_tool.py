"""SubmitDagStepTool — the only tool exposed to DAG-orchestrator sessions.

The LLM submits the full modified DAG (one incremental step per call);
the tool validates it with the exact same ``WorkflowDefinition.validate``
used by the definitions save API, so anything that would 422 at save time
is caught here and returned to the LLM for self-correction inside the
agent loop.

Robustness: the payload comes straight from LLM JSON output, where deeply
nested values are occasionally serialized as strings (e.g. ``config``
rendered as ``"{\"tool\": \"x\"}"`` or a node written as a bare string).
Such shapes used to raise ``AttributeError: 'str' object has no attribute
'get'`` inside ``WorkflowNode.from_dict``, escaping the tool's try/except
and surfacing as an unreadable framework error. ``_sanitize_payload``
normalizes these shapes into readable validation errors (or valid dicts)
before any ``.get()`` is called, so the LLM always receives
``{"applied": false, "errors": [...]}`` it can self-correct against.
"""

from __future__ import annotations

import json
from typing import Any

from strategy_research.core.agent.tools import BaseTool, ToolContext

MAX_NODES = 50


def _coerce_node(n: Any, index: int) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize one nodes[] element; returns (node_or_None, error_or_None)."""
    if isinstance(n, dict):
        node = dict(n)
        cfg = node.get("config")
        if isinstance(cfg, str):
            try:
                node["config"] = json.loads(cfg)
            except (ValueError, TypeError):
                return None, f"nodes[{index}].config 不是合法 JSON: {cfg[:80]!r}"
            if not isinstance(node["config"], dict):
                return None, f"nodes[{index}].config 解析后必须是对象"
        elif cfg is not None and not isinstance(cfg, dict):
            return None, f"nodes[{index}].config 必须是对象或 JSON 字符串"
        return node, None
    if isinstance(n, str):
        # LLM occasionally emits a node as a JSON string; salvage it.
        try:
            parsed = json.loads(n)
        except (ValueError, TypeError):
            return None, f"nodes[{index}] 必须是对象，收到字符串: {n[:80]!r}"
        if not isinstance(parsed, dict):
            return None, f"nodes[{index}] 必须是对象，收到的 JSON 是 {type(parsed).__name__}"
        return _coerce_node(parsed, index)
    return None, f"nodes[{index}] 必须是对象，收到 {type(n).__name__}"


def _coerce_edge(e: Any, index: int) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize one edges[] element; returns (edge_or_None, error_or_None)."""
    if isinstance(e, dict):
        return dict(e), None
    if isinstance(e, str):
        try:
            parsed = json.loads(e)
        except (ValueError, TypeError):
            return None, f"edges[{index}] 必须是对象，收到字符串: {e[:80]!r}"
        if not isinstance(parsed, dict):
            return None, f"edges[{index}] 必须是对象，收到的 JSON 是 {type(parsed).__name__}"
        return dict(parsed), None
    return None, f"edges[{index}] 必须是对象，收到 {type(e).__name__}"


def _sanitize_payload(
    dag: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Coerce raw LLM output into clean node/edge dicts; return errors.

    Every structural problem is reported as a readable message instead of
    letting a ``str.get()`` crash surface as an AttributeError.
    """
    raw_nodes = dag.get("nodes") or []
    raw_edges = dag.get("edges") or []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    errors: list[str] = []

    if not isinstance(raw_nodes, list):
        errors.append("dag.nodes 必须为数组")
        raw_nodes = []
    if not isinstance(raw_edges, list):
        errors.append("dag.edges 必须为数组")
        raw_edges = []

    for i, n in enumerate(raw_nodes):
        node, err = _coerce_node(n, i)
        if err:
            errors.append(err)
        elif node is not None:
            nodes.append(node)

    for i, e in enumerate(raw_edges):
        edge, err = _coerce_edge(e, i)
        if err:
            errors.append(err)
        elif edge is not None:
            edges.append(edge)

    return nodes, edges, errors


def _validate_dag(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    """Validate a DAG payload; returns [] when valid."""
    from .definition import WorkflowDefinition

    definition = WorkflowDefinition.from_dict(
        {"name": "orchestrate", "nodes": nodes, "edges": edges},
        source="orchestrator",
    )
    errors = definition.validate()
    if len(nodes) > MAX_NODES:
        errors.append(f"node count {len(nodes)} exceeds limit {MAX_NODES}")
    return errors


class SubmitDagStepTool(BaseTool):
    """提交 DAG 修改（一次性提交修改后的完整 DAG，校验通过即应用）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # ## 用途
    # 编排会话专用：LLM 每轮把「修改后的完整 DAG」提交给此工具。工具做与
    # 保存定义一致的服务端校验（6 种节点类型、planner/evaluator/approval
    # 各最多 1 个、依赖无环、id 引用完整、节点数上限）。
    #
    # ## 行为
    # - 校验通过 → {"applied": true, ...}（前端据此应用画布）
    # - 校验失败 → {"applied": false, "errors": [...]}，错误逐条可读，
    #   供 LLM 修正后重新提交（agent loop 内自动回传）
    #
    # ## 格式
    # {"dag": {"nodes": [{"id","type","label","config"}], "edges": [{"source","target"}]}}
    # ─────────────────────────────────────────────────────────────
    """

    name = "submit_dag_step"
    description = (
        "提交修改后的完整 DAG（含全部节点与连线）。每轮只做一处增量修改："
        "新增/删除/修改一个节点或一条连线。校验通过返回 applied:true；"
        "失败返回 applied:false 与 errors 列表，请逐条修正后重新提交。"
    )
    repeatable = True
    category = "编排"

    def execute(self, ctx: ToolContext, dag: dict[str, Any]) -> str:
        try:
            if not isinstance(dag, dict):
                return json.dumps(
                    {"applied": False, "errors": ["dag 参数必须是 JSON 对象"]},
                    ensure_ascii=False,
                )
            nodes, edges, errors = _sanitize_payload(dag)
            if errors:
                return json.dumps(
                    {"applied": False, "errors": errors},
                    ensure_ascii=False,
                )
            errors = _validate_dag(nodes, edges)
            if errors:
                return json.dumps(
                    {"applied": False, "errors": errors},
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "applied": True,
                    "nodes": len(nodes),
                    "edges": len(edges),
                    "diff": f"当前 DAG: {len(nodes)} 节点 / {len(edges)} 连线",
                },
                ensure_ascii=False,
            )
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            # Last-resort guard: never let a malformed LLM payload escape
            # as a raw exception — always return a structured, readable
            # error the agent loop feeds back for self-correction.
            return json.dumps(
                {"applied": False, "errors": [f"DAG 结构解析异常: {type(exc).__name__}: {exc}"]},
                ensure_ascii=False,
            )
