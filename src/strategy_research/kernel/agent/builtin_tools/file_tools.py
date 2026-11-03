"""文件工具: read_file / list_files / write_file（沙箱 + AST 守卫）。"""

from __future__ import annotations

import logging

from strategy_research.kernel.agent.sandbox import (
    PathValidationError,
    PathWhitelist,
    validate_python_source,
)
from strategy_research.core.agent.tools import (
    EFFECT_FS,
    BaseTool,
    ToolContext,
)
from .utils import err_actionable, tool_ok

logger = logging.getLogger(__name__)




# ── 1. ReadFileTool ─────────────────────────────────────────────────


class ReadFileTool(BaseTool):
    """读取工作区文件内容（只读，可限制行数）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext; schema 自动派生)
    #
    # ## 用途
    # 读取工作区内文件内容, 支持 limit/offset 分片。路径相对 workspace,
    # 必须位于允许的读取根目录 (strategies/templates/memory/logs/data/docs/.)。
    #
    # ## 参数
    # - path: 相对 workspace 的文件路径 (必填)
    # - limit: 返回的最大行数 (可选)
    # - offset: 起始行偏移, 0 起 (可选)
    #
    # ## 示例
    # {"path": "strategies/momentum_20d/strategy.py"}
    #
    # ## 边界
    # 只读工具; 白名单外路径/绝对路径/.. 会被拒绝; 二进制/非 UTF-8 文件报错。
    #
    # ## 错误处理范式
    # - 缺 path → error + expected 示例
    # - 白名单外 → error + fix 提示允许根目录
    # - 文件不存在/是目录 → error + fix 用 list_files 确认
    # - 非 UTF-8 → 提示用 read_document 或跳过
    # - 所有失败均可安全重试
    #
    # ## 相关工具
    # list_files: 浏览目录; write_file: 写入
    # ─────────────────────────────────────────────
    """

    name = "read"
    description = (
        "读取工作区内文件内容 (行数限制可选); 路径相对 workspace, "
        "限允许根目录 (strategies/templates/memory/logs/data/docs/.)。"
    )
    repeatable = True
    category = "文件"

    def execute(
        self,
        ctx: ToolContext,
        path: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="read",
            )
        workspace = ctx.workspace

        if not isinstance(path, str) or not path:
            return err_actionable(
                "missing or invalid 'path'",
                received=path,
                expected="non-empty string path relative to workspace, e.g. 'strategies/momentum_20d/strategy.py'",
                fix="pass path='strategies/<name>/strategy.py' or 'templates/strategy.py'",
                tool="read",
            )

        # v2: ctx roots override the default white-list roots
        # (study scenario: agents may write/read under study/<id>/)
        wl = PathWhitelist(
            workspace=workspace,
            write_roots=ctx.write_roots,
            read_roots=ctx.read_roots,
        )
        try:
            resolved = wl.resolve_read(path)
        except PathValidationError as exc:
            return err_actionable(
                str(exc),
                received=path,
                expected="path under an allowed read root (strategies/templates/memory/logs/data/docs/)",
                fix="use a path under strategies/, templates/, memory/, logs/, data/, or docs/",
                tool="read",
            )

        if not resolved.exists():
            return err_actionable(
                f"file not found: {path}",
                received=path,
                fix="verify the path exists with list_files(workspace=..., path='<dir>')",
                tool="read",
                extra={"resolved_path": str(resolved)},
            )
        if not resolved.is_file():
            return err_actionable(
                f"not a regular file: {path}",
                received=path,
                fix="use list_files to list a directory, read_file on a file",
                tool="read",
                extra={"resolved_path": str(resolved)},
            )

        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return err_actionable(
                f"file is not valid UTF-8: {path}",
                fix="file may be binary; use read_document for PDF, or skip this file",
                tool="read",
            )
        except OSError as exc:
            return err_actionable(
                f"read failed: {exc}",
                fix="check file permissions",
                tool="read",
            )

        all_lines = content.splitlines()
        if offset:
            all_lines = all_lines[offset:]
        if limit is not None:
            all_lines = all_lines[: int(limit)]
        output = "\n".join(all_lines)

        return tool_ok({
            "path": str(resolved),
            "content": output,
            "total_lines": len(content.splitlines()),
            "returned_lines": len(all_lines),
        })


# ── 1b. ListFilesTool ─────────────────────────────────────────────


class ListFilesTool(BaseTool):
    """列出工作区目录内容（文件/子目录，支持 glob）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 浏览工作区目录结构: 文件与子目录清单 (含大小)。读文件前先用它探索。
    #
    # ## 参数
    # - path: 目录路径, 相对 workspace (默认 '.')
    # - pattern: glob 过滤 (可选, 如 '*.py' / 'strategies/*')
    #
    # ## 示例
    # {"path": "strategies"}
    #
    # ## 边界
    # 只读工具; 仅限 workspace 内目录; 文件路径会报错 (用 read_file)。
    #
    # ## 错误处理范式
    # - 路径不存在 → error + fix 提示顶层结构
    # - 目标是文件 → error + fix 用 read_file
    # - 均可安全重试
    #
    # ## 相关工具
    # read_file: 读文件内容; write_file: 写入
    # ─────────────────────────────────────────────
    """

    name = "list"
    description = (
        "列出工作区目录内容 (文件/子目录, 含大小); path 相对 workspace, "
        "可用 glob pattern 过滤。"
    )
    repeatable = True
    category = "文件"

    def execute(
        self,
        ctx: ToolContext,
        path: str = ".",
        pattern: str | None = None,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="list",
            )
        workspace = ctx.workspace

        rel_path = path or "."

        target = (workspace / rel_path).resolve()
        if not target.exists():
            return err_actionable(
                f"path not found: {rel_path}",
                received=rel_path,
                expected="directory path relative to workspace, e.g. 'strategies' or '.' for root",
                fix="verify the path exists; use list_files(path='.') to see top-level dirs",
                tool="list",
            )
        if not target.is_dir():
            return err_actionable(
                f"not a directory: {rel_path}",
                received=rel_path,
                fix="use read_file for files, list_files for directories",
                tool="list",
            )

        entries = []
        if pattern:
            for p in sorted(target.glob(pattern)):
                entries.append({
                    "name": p.name,
                    "type": "dir" if p.is_dir() else "file",
                    "size": p.stat().st_size if p.is_file() else None,
                })
        else:
            for p in sorted(target.iterdir()):
                entries.append({
                    "name": p.name,
                    "type": "dir" if p.is_dir() else "file",
                    "size": p.stat().st_size if p.is_file() else None,
                })
        return tool_ok({
            "path": str(target),
            "entries": entries,
            "count": len(entries),
        })


# ── 2. WriteFileTool ────────────────────────────────────────────────


class WriteFileTool(BaseTool):
    """写入工作区文件（沙箱路径白名单 + .py AST 安全检查）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext; 副作用改 effects)
    #
    # ## 用途
    # 写入文件内容到工作区。路径限允许写根目录
    # (strategies/templates/memory/logs); .py 文件做 AST 校验,
    # 危险代码 (exec/eval、受限 import、dunder 访问) 会被拒绝。
    #
    # ## 参数
    # - path: 相对 workspace 的文件路径 (必填, 限写白名单)
    # - content: 文件内容 (必填, 字符串)
    #
    # ## 示例
    # {"path": "strategies/momentum_20d/strategy.py", "content": "..."}
    #
    # ## 边界
    # 写工具 (effects=fs); 自动创建父目录; 覆盖已有文件。
    #
    # ## 错误处理范式
    # - 缺 path/content → error + expected 示例
    # - AST 校验失败 → error 含具体危险代码说明
    # - 白名单外 → error + fix 允许根目录
    # - 写入失败 → error + fix 检查权限
    # - 幂等: 重跑覆盖同一路径, 安全
    #
    # ## 相关工具
    # read_file: 读回校验; list_files: 浏览
    # ─────────────────────────────────────────────
    """

    name = "write"
    description = (
        "写入文件到工作区 (限 strategies/templates/memory/logs 写白名单); "
        ".py 做 AST 安全检查, 危险代码被拒。"
    )
    repeatable = True
    strict = True  # All params required, no dict-shape → strict-safe
    category = "文件"
    effects = frozenset({EFFECT_FS})

    def execute(self, ctx: ToolContext, path: str, content: str) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="write",
            )
        workspace = ctx.workspace

        if not isinstance(path, str) or not path:
            return err_actionable(
                "missing or invalid 'path'",
                received=path,
                expected="non-empty string path, e.g. 'strategies/momentum_20d/strategy.py'",
                fix="pass a non-empty path",
                tool="write",
            )
        if not isinstance(content, str):
            return err_actionable(
                "missing or invalid 'content'",
                received=type(content).__name__,
                expected="string content for the file",
                fix="pass content as a string, e.g. content='# strategy parameters\\nPARAMS = {...}'",
                tool="write",
            )

        # AST guard for .py files
        if path.endswith(".py"):
            ok, msg = validate_python_source(content)
            if not ok:
                return err_actionable(
                    f"AST validation failed: {msg}",
                    received=content[:200],
                    fix="remove dangerous code (exec/eval, blocked imports, dunder access); see sandbox rules",
                    tool="write",
                )

        # v2: ctx roots override the default white-list roots
        # (study scenario: agents may write/read under study/<id>/)
        wl = PathWhitelist(
            workspace=workspace,
            write_roots=ctx.write_roots,
            read_roots=ctx.read_roots,
        )
        try:
            resolved = wl.resolve_write(path)
        except PathValidationError as exc:
            return err_actionable(
                str(exc),
                received=path,
                expected="path under an allowed write root (strategies/templates/memory/logs)",
                fix="use a path under strategies/, templates/, memory/, or logs/",
                tool="write",
            )

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except OSError as exc:
            return err_actionable(
                f"write failed: {exc}",
                fix="check filesystem permissions and disk space",
                tool="write",
            )

        return tool_ok({
            "path": str(resolved),
            "bytes_written": len(content.encode("utf-8")),
        })
