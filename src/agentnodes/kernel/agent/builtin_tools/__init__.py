"""内置 Agent 工具（按域拆分模块，本文件为注册中枢）。

域模块:
    file_tools            文件: read_file / list_files / write_file
    workspace_tools       工作区: git_diff / list_history / list_skills / load_skill
    options_tools         期权定价: options_pricing
    help_tools            工具文档: tool_help

**业务工具已归位 L2（2026-11-05 批次 6）**：backtest_tools / factor_tools /
data_tools / goal_tools / backtest_analysis_tools / data_clean_tools 六个模块
原本住在本目录，但它们调用的全是业务能力（factor.compute / db.* / backtest.*），
属「L3 里的 L2」。已 git mv 到 ``apps/builtin_tools/``，由
``apps/capabilities.py`` 注册成 ``tool.*`` kind，本中枢通过
:func:`_register_business_tools` 从 registry 拉取。

降级语义：kind 缺失时静默跳过（``logger.debug`` 一行），工具不出现在
registry 里——比注册一个「调用必然失败」的工具好。
"""
from __future__ import annotations

import logging
from pathlib import Path

from agentnodes.kernel.agent.tools import ToolRegistry
from .file_tools import ListFilesTool, ReadFileTool, WriteFileTool
from .help_tools import ToolHelpTool
from .options_tools import OptionsPricingTool
from .workspace_tools import GitDiffTool, ListHistoryTool, ListSkillsTool, LoadSkillTool

logger = logging.getLogger(__name__)


#: 业务工具 kind → 在 ToolRegistry 里的注册方式。
#:
#: 每个 kind 的工厂返回 ``list[BaseTool]``（一个模块可以产多个工具）。
#: 批次 6 之后这些实现住在 ``apps/builtin_tools/``，由 apps 侧注册。
BUSINESS_TOOL_KINDS = (
    "tool.factor",
    "tool.data",
    "tool.backtest",
    "tool.backtest_analysis",
    "tool.data_clean",
    "tool.goal",
)


def _register_business_tools(registry: ToolRegistry) -> None:
    """从 CapabilityRegistry 拉业务工具（L2 提供的实现）。

    每个 kind 的工厂返回 ``list[BaseTool]``；kind 缺失或工厂抛错时
    跳过该组并记 debug 日志——**框架不因业务能力缺失而启动失败**，
    这是 L3→L2 端口化已经建立的模式（与 ``resolve("db.get_connection")``
    的调用点一致）。
    """
    from agentnodes.kernel.agent.capability import (
        CapabilityNotFound,
        resolve,
    )

    for kind in BUSINESS_TOOL_KINDS:
        try:
            tools = resolve(kind)
        except CapabilityNotFound:
            logger.debug("business tool kind not registered: %s", kind)
            continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("business tool kind resolve failed: %s (%s)", kind, exc)
            continue
        # ``resolve()`` 已执行 factory，返回值就是工厂产物（这里是 list[BaseTool]）。
        if tools is None:
            logger.debug("business tool kind returned None: %s", kind)
            continue
        if not isinstance(tools, (list, tuple)):
            tools = [tools]
        for tool in tools:
            try:
                registry.register(tool)
            except Exception as exc:  # noqa: BLE001
                logger.warning("register business tool failed (%s): %s", kind, exc)


def build_default_registry(workspace: Path | None = None) -> ToolRegistry:
    """Build a ToolRegistry with all tools.

    Tools are stateless; AgentLoop injects `workspace` per call.
    No workspace is bound at construction time.

    When ``workspace`` is given, composite tools from
    ``<workspace>/tools/combo/*.yml`` are loaded and registered
    (paradigm v2 分层注册: 显式核心 + 能力组 + 组合库加载器).
    """
    r = ToolRegistry()
    r.register(ReadFileTool())
    r.register(ListFilesTool())
    r.register(WriteFileTool())
    r.register(GitDiffTool())
    r.register(ListHistoryTool())
    r.register(ListSkillsTool())
    r.register(LoadSkillTool())
    r.register(OptionsPricingTool())
    # 业务工具（L2 实现，经 CapabilityRegistry 注入）— 批次 6
    _register_business_tools(r)
    # Phase 2: Web I/O tools (conditional on dependencies)
    try:
        from .web_tools import register_web_tools
        register_web_tools(r)
    except Exception:
        pass
    # Display tools (agent-driven right panel: show_chart / show_report)
    try:
        from .display_tools import register_display_tools
        register_display_tools(r)
    except Exception:
        pass
    # Shell tools (opt-in, gated by allow_shell_tools)
    try:
        from .shell_tools import register_shell_tools
        register_shell_tools(r)
    except Exception:
        pass
    # Background-command tools (opt-in, same gate; long-task nohup mode)
    try:
        from .bg_tools import register_bg_tools
        register_bg_tools(r)
    except Exception:
        pass
    # Tool documentation (self-referential; registered last)
    r.register(ToolHelpTool(r))
    # Sub-agent delegation
    from .subagent_tool import SubAgentTool
    r.register(SubAgentTool())
    # Todo / task tracking
    from .todo_tools import TodoWriteTool
    r.register(TodoWriteTool())

    # Paradigm v2 分层注册: 组合库加载器 (workspace tools/combo/*.yml)
    if workspace is not None:
        from agentnodes.kernel.agent.combo import load_combo_tools
        load_combo_tools(workspace, r)

    # DSH-inspired: plugin discovery via entry_points
    _discover_tool_plugins(r)

    return r


def _discover_tool_plugins(registry: ToolRegistry) -> None:
    """Discover and register tool plugins via importlib.metadata entry_points.

    Plugins register under the ``agentnodes.tools`` group::

        # pyproject.toml
        [project.entry-points."agentnodes.tools"]
        my_tools = "my_package.tools:register_tools"

    The entry point must be a callable that takes a ToolRegistry and
    registers tools on it.  Failures are logged and swallowed.
    """
    try:
        from importlib.metadata import entry_points
    except ImportError:
        return  # Python < 3.10 fallback

    eps = entry_points()
    # Python 3.12+ returns a SelectableGroups; 3.10-3.11 returns dict
    if hasattr(eps, "select"):
        tool_eps = eps.select(group="agentnodes.tools")
    elif isinstance(eps, dict):
        tool_eps = eps.get("agentnodes.tools", [])
    else:
        tool_eps = []

    for ep in tool_eps:
        try:
            register_fn = ep.load()
            register_fn(registry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load tool plugin '%s': %s", ep.name, exc)


__all__ = [
    # 框架工具（本层）
    "ListFilesTool",
    "ReadFileTool",
    "WriteFileTool",
    "ToolHelpTool",
    "OptionsPricingTool",
    "GitDiffTool",
    "ListHistoryTool",
    "ListSkillsTool",
    "LoadSkillTool",
    # 注册中枢
    "BUSINESS_TOOL_KINDS",
    "build_default_registry",
]
