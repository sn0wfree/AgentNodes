"""MCPServer — Model Context Protocol server (research-only).

Exposes strategy-research tools via MCP protocol for external AI tools.
All tools are research-only (no trading/order execution).

Reuse: tools whose MCP contract matches the agent ``BaseTool`` surface
(web_search / read_url / read_document / list_data_sources / search_symbol /
run_backtest / compute_factor) are wrapped from the shared agent registry
via ``_wrap_base_tool`` — no duplicated handler logic. MCP-only tools
(goal / hypothesis / memory / session / swarm / list_skills / load_skill /
get_market_data) keep their own MCP-specific contracts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from ..agent.builtin_tools.utils import tool_err
from ..agent.tools import BaseTool, ToolContext, _schema_from_signature

logger = logging.getLogger(__name__)


def _serialize_value(val: Any) -> Any:
    """Convert a pandas/numpy value to a JSON-safe scalar."""
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if hasattr(val, "item"):
        return val.item()
    return val


def _df_to_records(rows: Any) -> list[dict]:
    """Convert a DataFrame (with reset index) into JSON-safe record dicts."""
    records = []
    for _, row in rows.iterrows():
        record = {}
        for col in rows.columns:
            record[col] = _serialize_value(row[col])
        records.append(record)
    return records


def _package_fetch_result(data: dict, max_rows: int) -> tuple[dict, int, bool]:
    """Convert fetched DataFrames into JSON-safe records with truncation."""
    result_data = {}
    total_rows = 0
    truncated = False
    for code, df in data.items():
        if df is None or df.empty:
            result_data[code] = []
            continue
        # 先量原始长度再截断：比较 tail 之后的结果会让 truncated 恒为
        # False（n_rows 永远 <= max_rows），客户端无从得知数据被截断。
        if len(df) > max_rows:
            truncated = True
        rows = df.tail(max_rows).reset_index()
        total_rows += len(rows)
        result_data[code] = _df_to_records(rows)
    return result_data, total_rows, truncated


def _handle_get_market_data(
    codes: list[str] | None,
    start_date: str,
    end_date: str,
    interval: str,
    source: str | None,
    max_rows: int,
    force_refresh: bool,
) -> str:
    """Handle the get_market_data MCP tool call."""
    from ..data_source.base import validate_date_range
    from ..data_source.registry import NoAvailableSourceError

    if not codes:
        return json.dumps({"status": "error", "error": "codes is required"}, ensure_ascii=False)
    if not start_date or not end_date:
        return json.dumps({"status": "error", "error": "start_date and end_date are required"}, ensure_ascii=False)

    try:
        validate_date_range(start_date, end_date)
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

    try:
        loader, effective_source = _resolve_market_loader(source, codes)
        if loader is None:
            return json.dumps({
                "status": "error",
                "error": f"source '{source}' is not available",
            }, ensure_ascii=False)

        data = loader.fetch(codes, start_date, end_date, interval=interval,
                           force_refresh=force_refresh)

        result_data, total_rows, truncated = _package_fetch_result(
            data, max_rows
        )

        return json.dumps({
            "status": "ok",
            "data": result_data,
            "meta": {
                "codes": codes,
                "start_date": start_date,
                "end_date": end_date,
                "interval": interval,
                "source": effective_source,
                "total_rows": total_rows,
                "max_rows_per_code": max_rows,
                "truncated": truncated,
            },
        }, ensure_ascii=False, default=str)

    except NoAvailableSourceError as exc:
        return json.dumps({"status": "error", "error": f"no available data source: {exc}"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"status": "error", "error": f"fetch failed: {exc}"}, ensure_ascii=False)


def _resolve_market_loader(
    source: str | None, codes: list[str]
) -> tuple[Any | None, str]:
    """Resolve the data loader for an explicit source or auto market detection.

    Returns ``(loader, effective_source)``; ``(None, "")`` when an explicit
    source is registered but unavailable.
    """
    from ..data_source.registry import LOADER_REGISTRY, resolve_loader
    from ..data_source.utils import detect_market

    if source and source in LOADER_REGISTRY:
        loader = LOADER_REGISTRY[source]()
        if not loader.is_available():
            return None, ""
        return loader, source
    market = detect_market(codes[0])
    loader = resolve_loader(market)
    return loader, loader.name


class MCPTool:
    """An MCP tool definition."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Any],
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler

    def to_schema(self) -> dict[str, Any]:
        """Convert to MCP tool schema."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }


def _wrap_base_tool(
    tool: BaseTool,
    *,
    param_map: dict[str, str] | None = None,
    include_workspace: bool = False,
) -> MCPTool:
    """Wrap an agent ``BaseTool`` as an MCP tool.

    The MCP schema is derived from the tool's signature-based OpenAI
    function schema. ``param_map`` renames MCP params to agent params
    (MCP name → agent name); ``include_workspace`` adds a ``workspace``
    param that is routed into ``ToolContext`` instead of the tool kwargs.
    """
    # Use the non-strict signature schema: strict (OpenAI structured
    # outputs) forces ALL params into `required`, which is wrong for MCP.
    fn_def = {"description": tool.description, "parameters": _schema_from_signature(tool)}
    parameters = fn_def["parameters"] or {}
    properties = dict(parameters.get("properties", {}))
    required = list(parameters.get("required", []))

    if param_map:
        # Schema 侧展示 MCP 名（客户端按 schema 传参，handler 再翻译成
        # agent 名）——所以这里要**反向**映射。用正向 get 查不到键，
        # schema 会原样保留 agent 名，翻译逻辑永远不会触发。
        reverse = {agent: mcp for mcp, agent in param_map.items()}
        properties = {reverse.get(k, k): v for k, v in properties.items()}
        required = [reverse.get(n, n) for n in required]
    if include_workspace and "workspace" not in properties:
        properties["workspace"] = {"type": "string", "description": "工作区路径（必填）"}
        required.append("workspace")

    def handler(**kwargs: Any) -> str:
        workspace = kwargs.pop("workspace", None)
        ctx = ToolContext(workspace=Path(workspace).resolve() if workspace else None)
        if param_map:
            kwargs = {param_map.get(k, k): v for k, v in kwargs.items()}
        try:
            return tool.invoke({"ctx": ctx, **kwargs})
        except Exception as exc:  # noqa: BLE001
            return tool_err(str(exc))

    return MCPTool(
        name=tool.name,
        description=fn_def.get("description") or tool.description,
        parameters={"type": "object", "properties": properties, "required": required},
        handler=handler,
    )


class MCPServer:
    """MCP server exposing research-only tools.

    Usage:
        server = MCPServer()
        server.register_default_tools()
        # Serve via stdio
        server.serve_stdio()
        # Or get tool list
        tools = server.list_tools()
    """

    def __init__(self, name: str = "strategy-research") -> None:
        self.name = name
        self._tools: dict[str, MCPTool] = {}

    def register(self, tool: MCPTool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def list_tools(self) -> list[dict[str, Any]]:
        """List all registered tools as MCP schemas."""
        return [t.to_schema() for t in self._tools.values()]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool by name."""
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"Tool '{name}' not found"}

        try:
            result = tool.handler(**arguments)
            return {"content": [{"type": "text", "text": str(result)}]}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}

    def register_default_tools(self) -> None:
        """Register all default research-only tools."""
        self._register_skill_tools()
        self._register_goal_tools()
        self._register_hypothesis_tools()
        self._register_backtest_tools()
        self._register_factor_tools()
        self._register_memory_tools()
        self._register_session_tools()
        self._register_swarm_tools()
        self._register_web_tools()
        self._register_data_tools()
        self._register_swarm_execution_tools()

    def _register_skill_tools(self) -> None:
        def list_skills(**kwargs: Any) -> str:
            from ..skills import SkillRegistry
            skills_dir = Path(__file__).parent.parent.parent / "templates" / ".skills"
            registry = SkillRegistry()
            registry.load_directory(skills_dir)
            skills = registry.list_all()
            return json.dumps([{"name": s.name, "category": s.category, "description": s.description} for s in skills], ensure_ascii=False)

        def load_skill(name: str = "", **kwargs: Any) -> str:
            from ..skills import SkillRegistry
            skills_dir = Path(__file__).parent.parent.parent / "templates" / ".skills"
            registry = SkillRegistry()
            registry.load_directory(skills_dir)
            skill = registry.get(name)
            if skill is None:
                return f"Skill '{name}' not found"
            return skill.content

        self.register(MCPTool(
            name="list_skills",
            description="列出所有可用技能",
            parameters={"type": "object", "properties": {}},
            handler=list_skills,
        ))
        self.register(MCPTool(
            name="skill",
            description="按名称加载技能内容",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "技能名称"}},
                "required": ["name"],
            },
            handler=load_skill,
        ))

    def _register_goal_tools(self) -> None:
        def start_research_goal(objective: str = "", session_id: str = "default", **kwargs: Any) -> str:
            # Phase B: 真接 GoalStore (替代旧 stub 返回假数据)
            try:
                from ..goal import GoalStore, RiskTier
                store = GoalStore()
                goal = store.replace_goal(
                    session_id=session_id,
                    objective=objective,
                    criteria=["backtest_profitable", "risk_within_limits"],
                    source="mcp",
                    risk_tier=RiskTier.RESEARCH_GENERAL,
                )
                return json.dumps({
                    "status": "ok",
                    "goal_id": goal.goal_id,
                    "session_id": session_id,
                    "objective": goal.objective,
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        def get_research_goal(session_id: str = "default", **kwargs: Any) -> str:
            try:
                from ..goal import GoalStore
                store = GoalStore()
                goal = store.get_current_goal(session_id)
                if goal is None:
                    return json.dumps({"status": "no_active_goal", "session_id": session_id}, ensure_ascii=False)
                return json.dumps({
                    "goal_id": goal.goal_id,
                    "session_id": session_id,
                    "objective": goal.objective,
                    "ui_summary": goal.ui_summary,
                    "status": goal.status.value,
                    "criteria": store.list_criteria(goal.goal_id),
                }, ensure_ascii=False, default=str)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        self.register(MCPTool(
            name="start_research_goal",
            description="创建研究目标（写入 GoalStore SQLite）",
            parameters={
                "type": "object",
                "properties": {
                    "objective": {"type": "string", "description": "研究目标"},
                    "session_id": {"type": "string", "description": "会话 ID", "default": "default"},
                },
                "required": ["objective"],
            },
            handler=start_research_goal,
        ))
        self.register(MCPTool(
            name="get_research_goal",
            description="获取当前会话的研究目标",
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID", "default": "default"},
                },
            },
            handler=get_research_goal,
        ))

        def add_goal_evidence(
            session_id: str = "default",
            goal_id: str = "",
            text: str = "",
            criterion_id: str | None = None,
            claim_id: str | None = None,
            source_type: str | None = None,
            source_uri: str | None = None,
            confidence: str | None = None,
            caveat: str | None = None,
            **kwargs: Any,
        ) -> str:
            try:
                from ..goal import EvidenceInput, GoalStore
                store = GoalStore()
                if not goal_id:
                    return json.dumps({"status": "error", "error": "goal_id is required"}, ensure_ascii=False)
                if not text:
                    return json.dumps({"status": "error", "error": "text is required"}, ensure_ascii=False)
                evidence = EvidenceInput(
                    text=text,
                    criterion_id=criterion_id or None,
                    claim_id=claim_id or None,
                    source_provider="mcp",
                    source_type=source_type or None,
                    source_uri=source_uri or None,
                    confidence=confidence or None,
                    caveat=caveat or None,
                )
                record = store.append_evidence(
                    session_id=session_id,
                    goal_id=goal_id,
                    expected_goal_id=goal_id,
                    evidence=evidence,
                )
                return json.dumps({
                    "status": "ok",
                    "evidence_id": record.evidence_id,
                    "goal_id": record.goal_id,
                    "criterion_id": record.criterion_id,
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        def update_research_goal_status(
            session_id: str = "default",
            goal_id: str = "",
            status: str = "",
            recap: str | None = None,
            **kwargs: Any,
        ) -> str:
            try:
                from ..goal import GoalStatus, GoalStore
                store = GoalStore()
                if not goal_id:
                    return json.dumps({"status": "error", "error": "goal_id is required"}, ensure_ascii=False)
                if not status:
                    return json.dumps({"status": "error", "error": "status is required"}, ensure_ascii=False)
                try:
                    goal_status = GoalStatus(status)
                except ValueError:
                    valid = [s.value for s in GoalStatus]
                    return json.dumps({
                        "status": "error",
                        "error": f"invalid status: {status!r}. Valid: {valid}",
                    }, ensure_ascii=False)
                record = store.update_status(
                    session_id=session_id,
                    goal_id=goal_id,
                    expected_goal_id=goal_id,
                    status=goal_status,
                    recap=recap or None,
                )
                return json.dumps({
                    "status": "ok",
                    "goal_id": record.goal_id,
                    "new_status": record.status.value,
                    "progress": record.progress_percent,
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        self.register(MCPTool(
            name="add_goal_evidence",
            description="向研究目标追加证据（自动更新 criterion 状态）",
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID", "default": "default"},
                    "goal_id": {"type": "string", "description": "目标 ID"},
                    "text": {"type": "string", "description": "证据内容"},
                    "criterion_id": {"type": "string", "description": "关联的 criterion ID（可选）"},
                    "claim_id": {"type": "string", "description": "关联的 claim ID（可选）"},
                    "source_type": {"type": "string", "description": "来源类型（如 backtest、data、analysis）"},
                    "source_uri": {"type": "string", "description": "来源 URI（如文件路径、URL）"},
                    "confidence": {"type": "string", "description": "置信度（high/medium/low）"},
                    "caveat": {"type": "string", "description": "注意事项或局限性"},
                },
                "required": ["goal_id", "text"],
            },
            handler=add_goal_evidence,
        ))
        self.register(MCPTool(
            name="update_research_goal_status",
            description="更新研究目标状态（active/complete/cancelled/paused 等）",
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID", "default": "default"},
                    "goal_id": {"type": "string", "description": "目标 ID"},
                    "status": {"type": "string", "description": "新状态", "enum": [
                        "active", "paused", "waiting_user", "needs_refresh",
                        "insufficient_evidence", "blocked", "complete", "cancelled",
                    ]},
                    "recap": {"type": "string", "description": "完成/取消时的总结"},
                },
                "required": ["goal_id", "status"],
            },
            handler=update_research_goal_status,
        ))

    def _register_hypothesis_tools(self) -> None:
        def list_hypotheses(**kwargs: Any) -> str:
            from ..hypothesis import HypothesisRegistry
            registry = HypothesisRegistry()
            hyps = registry.list()
            return json.dumps([{"id": h.hypothesis_id, "title": h.title, "status": h.status} for h in hyps], ensure_ascii=False)

        self.register(MCPTool(
            name="list_hypotheses",
            description="列出所有假说",
            parameters={"type": "object", "properties": {}},
            handler=list_hypotheses,
        ))

    def _register_backtest_tools(self) -> None:
        # Agent registry's 4-step orchestrated run_backtest (config_load →
        # data_prepare → data_gate → engine_run); MCP params map 1:1.
        from ..agent.builtin_tools.backtest_tools import RunBacktestTool

        self.register(_wrap_base_tool(
            RunBacktestTool(),
            param_map={"strategy": "strategy_name"},
            include_workspace=True,
        ))

        def validate_run(run_dir: str = "", market: str = "a_share", **kwargs: Any) -> str:
            # Phase B: 真接 core.validation.runner (替代旧 stub)
            if not run_dir:
                return json.dumps({"status": "error", "error": "run_dir 是必需参数"}, ensure_ascii=False)
            try:
                # 注意：必须是 ``..validation``（core/validation）。写成
                # ``.validation`` 会解析到 core/mcp/validation——不存在，
                # MCP 客户端调用 validate_run 永远得到 "No module named"。
                from ..validation import MarketType, run_validation
                from ..validation.cli import _build_synthetic_trades, _load_nav_synthetic

                run_path = Path(run_dir).resolve()
                market_enum = MarketType(market)

                equity_curve = _load_nav_synthetic(run_path)
                trades = _build_synthetic_trades(run_path)
                if equity_curve is None or len(equity_curve) == 0:
                    return json.dumps({
                        "status": "error",
                        "error": f"无法从 {run_dir} 加载 NAV 时间序列",
                    }, ensure_ascii=False)

                config = {"validation": {"monte_carlo": True, "bootstrap": True}}
                results = run_validation(
                    config=config,
                    equity_curve=equity_curve,
                    trades=trades,
                    market=market_enum,
                )
                return json.dumps({
                    "status": "ok",
                    "run_dir": run_dir,
                    "validation": results,
                }, ensure_ascii=False, default=str)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        self.register(MCPTool(
            name="validate_run",
            description="验证回测结果（Monte Carlo + Bootstrap + Walk-Forward）",
            parameters={
                "type": "object",
                "properties": {
                    "run_dir": {"type": "string", "description": "回测运行目录"},
                    "market": {"type": "string", "description": "市场类型", "default": "a_share"},
                },
                "required": ["run_dir"],
            },
            handler=validate_run,
        ))

    def _register_factor_tools(self) -> None:
        # Agent registry's compute_factor (expression → factor_code).
        from ..agent.builtin_tools.factor_tools import ComputeFactorTool

        self.register(_wrap_base_tool(
            ComputeFactorTool(),
            param_map={"expression": "factor_code"},
            include_workspace=True,
        ))

    def _register_memory_tools(self) -> None:
        def search_memory(query: str = "", limit: int = 5, **kwargs: Any) -> str:
            # Phase B: 真接 PersistentMemory.find_relevant (替代旧 stub 空列表)
            try:
                from ..memory import PersistentMemory
                mem = PersistentMemory()
                entries = mem.find_relevant(query, max_results=limit)
                return json.dumps({
                    "query": query,
                    "n_results": len(entries),
                    "results": [
                        {
                            "title": e.title,
                            "type": e.memory_type,
                            "description": e.description,
                            "preview": e.body[:200],
                        }
                        for e in entries
                    ],
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        def add_memory(title: str = "", content: str = "", memory_type: str = "project", **kwargs: Any) -> str:
            try:
                from ..memory import PersistentMemory
                mem = PersistentMemory()
                path = mem.add(title, content, memory_type=memory_type)
                return json.dumps({
                    "status": "ok",
                    "title": title,
                    "path": str(path),
                    "memory_type": memory_type,
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        self.register(MCPTool(
            name="search_memory",
            description="搜索持久化记忆（keyword scoring + recency boost）",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "返回数量上限", "default": 5},
                },
                "required": ["query"],
            },
            handler=search_memory,
        ))
        self.register(MCPTool(
            name="add_memory",
            description="添加持久化记忆",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "标题"},
                    "content": {"type": "string", "description": "内容"},
                    "memory_type": {"type": "string", "description": "类型 (user/feedback/project/reference)", "default": "project"},
                },
                "required": ["title", "content"],
            },
            handler=add_memory,
        ))

    def _register_session_tools(self) -> None:
        def list_sessions(**kwargs: Any) -> str:
            # B4 Step 3: unified DB (the legacy SessionDB dataclass had no
            # `workspace` field, so this payload was mostly null).
            from ..session.unified import list_sessions as _list
            rows = _list()
            return json.dumps(
                [{"id": r["id"], "title": r["title"]} for r in rows],
                ensure_ascii=False,
            )

        def search_messages(query: str = "", **kwargs: Any) -> str:
            from ..session.unified import search_messages as _search
            return json.dumps(_search(query), ensure_ascii=False)

        self.register(MCPTool(
            name="list_sessions",
            description="列出所有会话",
            parameters={"type": "object", "properties": {}},
            handler=list_sessions,
        ))
        self.register(MCPTool(
            name="search_messages",
            description="搜索消息",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"],
            },
            handler=search_messages,
        ))

    def _register_swarm_tools(self) -> None:
        def list_swarm_presets(**kwargs: Any) -> str:
            from ..swarm import list_presets
            presets_dir = Path(__file__).parent.parent / "swarm" / "presets"
            presets = list_presets(presets_dir)
            return json.dumps([{"name": p.name, "description": p.description, "agents": len(p.agents)} for p in presets], ensure_ascii=False)

        self.register(MCPTool(
            name="list_swarm_presets",
            description="列出所有 swarm preset",
            parameters={"type": "object", "properties": {}},
            handler=list_swarm_presets,
        ))

    def _register_web_tools(self) -> None:
        from ..agent.builtin_tools.web_tools import (
            ReadDocumentTool,
            ReadUrlTool,
            WebSearchTool,
        )

        for tool in (WebSearchTool(), ReadUrlTool(), ReadDocumentTool()):
            self.register(_wrap_base_tool(tool))

    def _register_data_tools(self) -> None:
        def get_market_data(
            codes: list[str] | None = None,
            start_date: str = "",
            end_date: str = "",
            interval: str = "1D",
            source: str | None = None,
            max_rows: int = 500,
            force_refresh: bool = False,
            **kwargs: Any,
        ) -> str:
            return _handle_get_market_data(
                codes, start_date, end_date, interval, source, max_rows, force_refresh
            )

        self.register(MCPTool(
            name="get_market_data",
            description="获取 OHLCV 市场行情数据（自动选择最佳数据源）",
            parameters={
                "type": "object",
                "properties": {
                    "codes": {"type": "array", "items": {"type": "string"}, "description": "资产代码列表"},
                    "start_date": {"type": "string", "description": "开始日期 (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "结束日期 (YYYY-MM-DD)"},
                    "interval": {"type": "string", "description": "K 线周期（默认 1D）", "default": "1D"},
                    "source": {"type": "string", "description": "指定数据源（可选）"},
                    "max_rows": {"type": "integer", "description": "每个代码最大行数（默认 500）", "default": 500},
                },
                "required": ["codes", "start_date", "end_date"],
            },
            handler=get_market_data,
        ))

        # Agent-registry tools with matching MCP contracts (no duplication)
        from ..agent.builtin_tools.data_tools import ListDataSourcesTool, SearchSymbolTool

        self.register(_wrap_base_tool(ListDataSourcesTool()))
        self.register(_wrap_base_tool(SearchSymbolTool()))

    def _register_swarm_execution_tools(self) -> None:
        from ..swarm.run_store import RunStore
        run_store = RunStore()

        def run_swarm(
            preset_name: str = "",
            workspace: str = "",
            task: str = "",
            max_workers: int = 4,
            timeout: int = 300,
            **kwargs: Any,
        ) -> str:
            if not preset_name:
                return json.dumps({"status": "error", "error": "preset_name is required"}, ensure_ascii=False)
            if not workspace:
                return json.dumps({"status": "error", "error": "workspace is required"}, ensure_ascii=False)
            if not task:
                task = f"执行 swarm preset: {preset_name}"

            from pathlib import Path

            from ..swarm import SwarmRuntime, load_preset

            presets_dir = Path(__file__).parent.parent / "swarm" / "presets"
            preset = None
            for ext in ("*.yaml", "*.yml"):
                for f in presets_dir.glob(ext):
                    p = load_preset(f)
                    if p and p.name == preset_name:
                        preset = p
                        break
                if preset:
                    break

            if preset is None:
                return json.dumps({
                    "status": "error",
                    "error": f"preset '{preset_name}' not found",
                }, ensure_ascii=False)

            try:
                runtime = SwarmRuntime(max_workers=max_workers)
                result = runtime.execute(preset, Path(workspace), task)

                # Serialize result
                agent_results = {}
                for aid, ar in result.agent_results.items():
                    agent_results[aid] = {
                        "status": ar.status.value if hasattr(ar.status, "value") else str(ar.status),
                        "elapsed_s": ar.elapsed_s,
                        "error": ar.error,
                    }

                result_dict = {
                    "run_id": result.run_id,
                    "preset_name": result.preset_name,
                    "success": result.success,
                    "elapsed_s": result.elapsed_s,
                    "agent_results": agent_results,
                    "final_output": result.final_output[:2000] if result.final_output else "",
                }
                run_store.save(result.run_id, result_dict)

                return json.dumps({
                    "status": "ok",
                    **result_dict,
                }, ensure_ascii=False, default=str)

            except Exception as exc:
                return json.dumps({
                    "status": "error",
                    "error": f"swarm execution failed: {exc}",
                }, ensure_ascii=False)

        def get_swarm_status(run_id: str = "", **kwargs: Any) -> str:
            if not run_id:
                return json.dumps({"status": "error", "error": "run_id is required"}, ensure_ascii=False)

            stored = run_store.get(run_id)
            if stored is not None:
                return json.dumps({
                    "status": "ok",
                    "run_status": "completed",
                    **stored,
                }, ensure_ascii=False, default=str)

            return json.dumps({
                "status": "ok",
                "run_status": "not_found",
                "run_id": run_id,
            }, ensure_ascii=False)

        self.register(MCPTool(
            name="run_swarm",
            description="执行 swarm preset（同步阻塞，直到完成或超时）",
            parameters={
                "type": "object",
                "properties": {
                    "preset_name": {"type": "string", "description": "Swarm preset 名称"},
                    "workspace": {"type": "string", "description": "工作区路径"},
                    "task": {"type": "string", "description": "任务描述"},
                    "max_workers": {"type": "integer", "description": "最大并行 worker 数（默认 4）", "default": 4},
                    "timeout": {"type": "integer", "description": "超时秒数（默认 300）", "default": 300},
                },
                "required": ["preset_name", "workspace"],
            },
            handler=run_swarm,
        ))
        self.register(MCPTool(
            name="get_swarm_status",
            description="查询 swarm 运行状态",
            parameters={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "运行 ID"},
                },
                "required": ["run_id"],
            },
            handler=get_swarm_status,
        ))

    def serve_stdio(self) -> None:
        """Serve via stdin/stdout (blocking).

        stdout 只走 MCP 协议帧；configure_logging("mcp") 的 console
        handler 落 **stderr**，日志（含 SR_LOG_JSON=1 的 JSON）不会混进
        协议帧——与 Web/CLI 同一开关语义（SR_LOG_LEVEL/SR_LOG_JSON/
        SR_LOG_FILE 由统一入口解析）。
        """
        import sys

        # 两级点号 = core/。三级（...observability）会解析到
        # strategy_research.observability——不存在。今天第四次修这个错误类
        # （此前：MCP validate_run、scheduled_research/cli、data_readiness）。
        try:
            # MCP 面日志必须走 stderr——stdout 是 JSON-RPC 协议帧通道。
            # 统一入口（docs/logging-unified-entry-design-20261102.md §4.1）。
            from ..observability import configure_logging

            configure_logging("mcp")
        except Exception:  # noqa: BLE001 — 日志装配失败不能阻断 MCP
            pass
        logger.info("MCP server '%s' starting on stdio", self.name)
        print(json.dumps({"server": self.name, "tools": self.list_tools()}))
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                method = request.get("method", "")
                params = request.get("params", {})
                req_id = request.get("id")

                if method == "tools/list":
                    response = {"tools": self.list_tools()}
                elif method == "tools/call":
                    tool_name = params.get("name", "")
                    arguments = params.get("arguments", {})
                    response = self.call_tool(tool_name, arguments)
                else:
                    response = {"error": f"Unknown method: {method}"}

                if req_id is not None:
                    print(json.dumps({"id": req_id, "result": response}))
            except json.JSONDecodeError:
                print(json.dumps({"error": "Invalid JSON"}))
