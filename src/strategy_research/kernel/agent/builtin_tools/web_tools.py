"""Agent tools for Web I/O: web_search, read_url, read_document."""

from __future__ import annotations

import logging

from strategy_research.core.agent.tools import BaseTool, ToolContext, ToolRegistry
from .utils import err_actionable

logger = logging.getLogger(__name__)


# ── 1. WebSearchTool ─────────────────────────────────────────────


class WebSearchTool(BaseTool):
    """网页搜索 — 优先 MiniMax, 回退 DuckDuckGo。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.2.0
    # 变更: v1.2.0 切换 MiniMax 为优先 backend (DuckDuckGo 兜底)
    #
    # ## 用途
    # 通用网页搜索, 返回标题/URL/摘要。
    #
    # ## Backend 优先级
    # 1. **MiniMax Token Plan / Coding Plan** — 当以下任一环境变量
    #    设置时使用: MINIMAX_CODE_PLAN_KEY / MINIMAX_CODING_API_KEY
    #    / MINIMAX_OAUTH_TOKEN / MINIMAX_API_KEY.
    #    Region 默认 CN (api.minimaxi.com/v1/coding_plan/search),
    #    MINIMAX_API_HOST 可覆盖。
    # 2. **DuckDuckGo** — 无 key 时的兜底。需 duckduckgo-search 包。
    #
    # ## 参数
    # - query: 搜索词 (必填)
    # - max_results: 最大结果数 (默认 10, MiniMax 实际限制 1-10)
    #
    # ## 示例
    # {"query": "A-share momentum factor research"}
    #
    # ## 边界
    # 只读工具 (effects: net)。
    #
    # ## 错误处理范式
    # - 缺 query → error + expected 示例
    # - 网络失败 / key 无效 → error (transient, 可重试)
    #
    # ## 相关工具
    # read_url: 打开结果
    # ─────────────────────────────────────────────
    """

    name = "websearch"
    description = (
        "网页搜索 (优先 MiniMax Token Plan, 回退 DuckDuckGo); "
        "返回标题/URL/摘要。"
    )
    repeatable = True
    strict = True
    category = "Web"
    effects = frozenset({"net"})

    @classmethod
    def check_available(cls) -> bool:
        # Always available — MiniMax (env-keyed) or DDG (pkg-keyed) handles
        # the lookup; if neither is configured the dispatcher returns an
        # explicit error JSON to the agent.
        try:
            from strategy_research.foundation.web import minimax_search  # noqa: F401
            return True
        except ImportError:
            pass
        try:
            import duckduckgo_search  # noqa: F401
            return True
        except ImportError:
            return False

    def execute(
        self,
        ctx: ToolContext,
        query: str,
        max_results: int = 10,
    ) -> str:
        from strategy_research.foundation.web.search import web_search
        if not query:
            return err_actionable(
                "missing required parameter 'query'",
                expected="non-empty search string, e.g. 'quantitative trading A-share momentum'",
                fix="pass a non-empty query, e.g. query='Python pandas tutorial'",
                tool="websearch",
            )
        return web_search(query=query, max_results=max_results)


# ── 2. ReadUrlTool ───────────────────────────────────────────────


class ReadUrlTool(BaseTool):
    """抓取网页并转 Markdown。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名)
    #
    # ## 用途
    # 抓取网页 URL 并返回 Markdown 内容; 适合读文档/文章/论文。
    #
    # ## 参数
    # - url: 要抓取的 URL (必填)
    # - max_chars: 最大字符数 (默认 10000)
    #
    # ## 示例
    # {"url": "https://docs.python.org/3/"}
    #
    # ## 边界
    # 只读工具 (effects: net); 依赖 ...web.fetch。
    #
    # ## 错误处理范式
    # - 缺 url → error + expected 示例
    # - 网络/解析失败 → error (transient, 可重试)
    #
    # ## 相关工具
    # web_search: 找 URL
    # ─────────────────────────────────────────────
    """

    name = "webfetch"
    description = "抓取网页 URL 并返回 Markdown 内容。"
    repeatable = True
    strict = True
    category = "Web"
    effects = frozenset({"net"})

    def execute(
        self,
        ctx: ToolContext,
        url: str,
        max_chars: int = 10_000,
    ) -> str:
        from strategy_research.foundation.web.fetch import read_url
        if not url:
            return err_actionable(
                "missing required parameter 'url'",
                expected="non-empty URL string, e.g. 'https://docs.python.org/3/'",
                fix="pass a valid http(s) URL, e.g. url='https://example.com/article'",
                tool="webfetch",
            )
        return read_url(url=url, max_chars=max_chars)


# ── 3. ReadDocumentTool ──────────────────────────────────────────


class ReadDocumentTool(BaseTool):
    """从 PDF 提取文本。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名)
    #
    # ## 用途
    # 从 PDF 文件提取文本内容 (含页码标记)。需 PyMuPDF。
    #
    # ## 参数
    # - path: PDF 文件路径 (必填, 绝对路径)
    # - max_pages: 最大页数 (默认 50)
    #
    # ## 示例
    # {"path": "/home/user/papers/momentum.pdf"}
    #
    # ## 边界
    # 只读工具; 依赖 fitz (PyMuPDF), 缺失时被排除注册。
    #
    # ## 错误处理范式
    # - 缺 path → error + expected 示例
    # - 文件无法解析 → error
    #
    # ## 相关工具
    # 无
    # ─────────────────────────────────────────────
    """

    name = "read_document"
    description = "从 PDF 文件提取文本 (含页码标记); 需 PyMuPDF。"
    repeatable = True
    strict = True
    category = "Web"

    @classmethod
    def check_available(cls) -> bool:
        try:
            import fitz  # noqa: F401
            return True
        except ImportError:
            return False

    def execute(
        self,
        ctx: ToolContext,
        path: str,
        max_pages: int = 50,
    ) -> str:
        from strategy_research.foundation.web.pdf import read_document
        if not path:
            return err_actionable(
                "missing required parameter 'path'",
                expected="absolute path to a PDF file",
                fix="pass an absolute path, e.g. path='/home/user/papers/momentum.pdf'",
                tool="read_document",
            )
        return read_document(path=path, max_pages=max_pages)


def register_web_tools(registry: ToolRegistry) -> None:
    """Register all web tools into a ToolRegistry."""
    for tool_cls in (WebSearchTool, ReadUrlTool, ReadDocumentTool):
        if tool_cls.check_available():
            registry.register(tool_cls())
