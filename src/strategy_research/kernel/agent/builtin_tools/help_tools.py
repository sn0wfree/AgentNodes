"""工具文档工具: tool_help（自引用注册表说明）。"""

from __future__ import annotations

import inspect
import logging

from strategy_research.core.agent.tools import (
    BaseTool,
    ToolContext,
    ToolRegistry,
)
from .utils import err_actionable, tool_ok

logger = logging.getLogger(__name__)




# ── Registry ─────────────────────────────────────────────────────────


# ── 19. ToolHelpTool ──────────────────────────────────────────────────
# 版本 1.0.0 | 变更: 初版, 返回 docstring 原文 + 元信息


class ToolHelpTool(BaseTool):
    """返回指定工具的详细版说明书（docstring 原文）。

    需要了解某个工具的详细用法、参数语义、边界或错误处理范式时调用；
    出错 debug 时调用可拿到该工具的完整错误处理说明。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 说明书移入 docstring (v2 范式 8 节模板)
    #
    # ## 用途
    # 返回注册表中任意工具的详细版说明书 (docstring 原文), 含用途、
    # 参数语义、示例、边界、错误处理范式、相关工具。系统提示中的
    # 工具目录 (简略版) 只提供一行摘要; 选中工具后或 debug 时用
    # 本工具获取完整说明。
    #
    # ## 参数
    # - name: 目标工具名 (与目录条目中的名字一致)
    #
    # ## 示例
    # {"name": "run_backtest"}
    #
    # ## 边界
    # 只读工具; 不修改任何状态; 需绑定 registry (由 build_default_registry
    # 构造)。
    #
    # ## 错误处理范式
    # - name 缺失/非字符串 → error + expected 提示
    # - 工具不存在 → error + available 列表 (最多 30 个)
    # - 未绑定 registry → error
    # - 本工具始终可安全重试
    #
    # ## 相关工具
    # list_skills / load_skill: 技能系统 (非工具) 的类似按需加载机制
    # ─────────────────────────────────────────────────────────────
    """

    # ── 工具说明书 ──────────────────────────────────────────────────
    # 版本: 1.0.0
    # 变更: 初版
    #
    # ## 用途
    # 返回注册表中任意工具的详细版说明书 (docstring 原文), 含用途、参数
    # 语义、示例、边界、错误处理范式、相关工具。系统提示中的工具目录
    # (简略版) 只提供一行摘要; 选中工具后或 debug 时用本工具获取完整说明。
    #
    # ## 参数
    # - name: 目标工具名 (与目录条目中的名字一致)
    #
    # ## 示例
    # {"name": "run_backtest"}
    #
    # ## 边界
    # 只读工具; 不修改任何状态。
    #
    # ## 错误处理范式
    # - name 缺失/非字符串 → error + expected 提示
    # - 工具不存在 → error + available 列表 (最多 30 个)
    # - 本工具始终可安全重试
    #
    # ## 相关工具
    # list_skills / load_skill: 技能系统 (非工具) 的类似按需加载机制
    # ─────────────────────────────────────────────────────────────

    name = "tool_help"
    description = (
        "返回指定工具的详细版说明书 (docstring 原文): 用途、参数语义、"
        "示例、边界、错误处理范式。选中工具后想知道具体用法或 debug 时调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "目标工具名。"},
        },
        "required": ["name"],
    }
    repeatable = True
    category = "技能"

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry

    def execute(
        self,
        ctx: ToolContext,
        name: str,
    ) -> str:
        if not isinstance(name, str) or not name:
            return err_actionable(
                "missing or invalid 'name'",
                received=name,
                expected="tool name, e.g. 'run_backtest'",
                fix="pass a tool name from the tool list in the system prompt",
                tool="tool_help",
            )
        if self._registry is None:
            return err_actionable(
                "tool_help is not bound to a registry",
                tool="tool_help",
            )
        tool = self._registry.get(name)
        if tool is None:
            available = sorted(self._registry.tool_names)[:30]
            return err_actionable(
                f"tool '{name}' not found",
                received=name,
                fix="use a name from the tool list in the system prompt",
                tool="tool_help",
                extra={"available": available},
            )
        return tool_ok({
            "name": tool.name,
            "category": tool.category,
            "brief": tool.brief,
            "doc": inspect.getdoc(tool) or "",
        })
