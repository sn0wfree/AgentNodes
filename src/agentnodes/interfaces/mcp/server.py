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

from agentnodes.kernel.agent.builtin_tools.utils import tool_err
from agentnodes.kernel.agent.tools import BaseTool, ToolContext, _schema_from_signature

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
        # 业务能力注册（2026-11-05 批次 6 后续）：业务工具住在 apps/ 并经
        # CapabilityRegistry 注册。MCP 服务器可能被**独立构造**（不经 CLI
        # 入口），此时 apps 尚未 import -> `resolve("tool.*")` 全部失败，
        # 工具静默消失。这里显式触发一次注册（幂等）。
        try:
            import agentnodes.apps  # noqa: F401 —— 注册副作用
        except Exception:  # noqa: BLE001 —— 业务包不可用不应阻断框架工具
            logger.debug("business capabilities unavailable", exc_info=True)

        self._register_skill_tools()
        # _register_goal_tools / _register_hypothesis_tools 已随 apps/goal、
        # apps/hypothesis 移除（2026-11-04 做减法批次 2）。
        self._register_backtest_tools()
        self._register_factor_tools()
        self._register_memory_tools()
        self._register_session_tools()
        self._register_swarm_tools()
        self._register_web_tools()
        # _register_data_tools 已随 apps/data_source 移除（2026-11-05 批次 5）
        self._register_swarm_execution_tools()

    def _register_skill_tools(self) -> None:
        def list_skills(**kwargs: Any) -> str:
            from agentnodes.kernel.skills import SkillRegistry
            skills_dir = Path(__file__).parent.parent.parent / "templates" / ".skills"
            registry = SkillRegistry()
            registry.load_directory(skills_dir)
            skills = registry.list_all()
            return json.dumps([{"name": s.name, "category": s.category, "description": s.description} for s in skills], ensure_ascii=False)

        def load_skill(name: str = "", **kwargs: Any) -> str:
            from agentnodes.kernel.skills import SkillRegistry
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

    # _register_goal_tools / _register_hypothesis_tools 已随 apps/goal、
    # apps/hypothesis 移除（2026-11-04 做减法批次 2；归档见
    # testcase/golden/apps/{goal,hypothesis}/）。

    def _register_backtest_tools(self) -> None:
        # Agent registry's 4-step orchestrated run_backtest (config_load →
        # data_prepare → data_gate → engine_run); MCP params map 1:1.
        #
        # 2026-11-05 批次 6：工具实现已从 kernel/agent/builtin_tools/ 归位到
        # apps/builtin_tools/。L1 → L2 是合法下行，但这里走 CapabilityRegistry
        # 保持与 kernel 侧同一套解析路径——业务侧没注册 kind 时不暴露该工具
        # （比暴露一个「调用必然失败」的工具好）。
        from agentnodes.kernel.agent.capability import resolve

        try:
            tools = resolve("tool.backtest")
        except Exception:  # noqa: BLE001 —— 含 CapabilityNotFound
            return
        for tool in tools or ():
            if type(tool).__name__ != "RunBacktestTool":
                continue
            self.register(_wrap_base_tool(
                tool,
                param_map={"strategy": "strategy_name"},
                include_workspace=True,
            ))
        # validate_run 工具已随 apps/validation 移除（2026-11-04 做减法批次 2）；
        # 此前它就因模块路径问题从未真正可用（注释见 git 历史）。

    def _register_factor_tools(self) -> None:
        # Agent registry's compute_factor (expression → factor_code).
        from agentnodes.kernel.agent.capability import resolve

        try:
            tools = resolve("tool.factor")
        except Exception:  # noqa: BLE001 —— 含 CapabilityNotFound
            return
        for tool in tools or ():
            if type(tool).__name__ != "ComputeFactorTool":
                continue
            self.register(_wrap_base_tool(
                tool,
                param_map={"expression": "factor_code"},
                include_workspace=True,
            ))

    def _register_memory_tools(self) -> None:
        def search_memory(query: str = "", limit: int = 5, **kwargs: Any) -> str:
            # Phase B: 真接 PersistentMemory.find_relevant (替代旧 stub 空列表)
            try:
                from agentnodes.kernel.memory import PersistentMemory
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
                from agentnodes.kernel.memory import PersistentMemory
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
            from agentnodes.kernel.session.unified import list_sessions as _list
            rows = _list()
            return json.dumps(
                [{"id": r["id"], "title": r["title"]} for r in rows],
                ensure_ascii=False,
            )

        def search_messages(query: str = "", **kwargs: Any) -> str:
            from agentnodes.kernel.session.unified import search_messages as _search
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
            import agentnodes.kernel.swarm as swarm_module
            from agentnodes.kernel.swarm import list_presets
            presets_dir = Path(swarm_module.__file__).parent / "presets"  # 层无关：跟随 swarm 包实际位置
            presets = list_presets(presets_dir)
            return json.dumps([{"name": p.name, "description": p.description, "agents": len(p.agents)} for p in presets], ensure_ascii=False)

        self.register(MCPTool(
            name="list_swarm_presets",
            description="列出所有 swarm preset",
            parameters={"type": "object", "properties": {}},
            handler=list_swarm_presets,
        ))

    def _register_web_tools(self) -> None:
        from agentnodes.kernel.agent.builtin_tools.web_tools import (
            ReadDocumentTool,
            ReadUrlTool,
            WebSearchTool,
        )

        for tool in (WebSearchTool(), ReadUrlTool(), ReadDocumentTool()):
            self.register(_wrap_base_tool(tool))

    def _register_swarm_execution_tools(self) -> None:
        from agentnodes.kernel.swarm.run_store import RunStore
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

            import agentnodes.kernel.swarm as swarm_module
            from agentnodes.kernel.swarm import SwarmRuntime, load_preset

            presets_dir = Path(swarm_module.__file__).parent / "presets"  # 层无关：跟随 swarm 包实际位置
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
        handler 落 **stderr**，日志（含 AN_LOG_JSON=1 的 JSON）不会混进
        协议帧——与 Web/CLI 同一开关语义（AN_LOG_LEVEL/AN_LOG_JSON/
        AN_LOG_FILE 由统一入口解析）。
        """
        import sys

        # 两级点号 = core/。三级（...observability）会解析到
        # agentnodes.observability——不存在。今天第四次修这个错误类
        # （此前：MCP validate_run、scheduled_research/cli、data_readiness）。
        try:
            # MCP 面日志必须走 stderr——stdout 是 JSON-RPC 协议帧通道。
            # 统一入口（docs/guides/logging-unified-entry-design-20261102.md §4.1）。
            from agentnodes.foundation.observability import configure_logging

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
