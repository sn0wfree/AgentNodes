"""工作区工具: git_diff / list_history / list_skills / load_skill。"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from strategy_research.core.agent.tools import (
    BaseTool,
    ToolContext,
)
from .utils import err_actionable, tool_ok

logger = logging.getLogger(__name__)




class GitDiffTool(BaseTool):
    """查看工作区 git 差异（默认未暂存；支持 staged/提交对比/路径过滤）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 查看 workspace 的 git diff。默认未暂存改动; staged=true 看暂存;
    # ref1/ref2 对比两个提交。
    #
    # ## 参数
    # - staged: 只看暂存改动 (默认 false)
    # - ref1/ref2: 提交对比 (需同时给)
    # - pathspec: 限定路径 (不能以 '-' 开头, 防参数注入)
    # - max_lines: 返回最大行数 (默认 200)
    #
    # ## 示例
    # {"pathspec": "strategies/momentum_20d/"}
    #
    # ## 边界
    # 只读工具; 要求 workspace 是 git 仓库; 超时 30s。
    #
    # ## 错误处理范式
    # - 非 git 仓库 → error + fix (git init)
    # - 超时 → fix 用 pathspec 缩小范围
    # - 均可安全重试
    #
    # ## 相关工具
    # read_file: 看具体文件; write_file: 修改后 diff
    # ─────────────────────────────────────────────
    """

    name = "git_diff"
    description = (
        "查看 workspace git diff (默认未暂存; staged/ref 对比/pathspec 可选)。"
    )
    repeatable = True
    category = "文件"

    def execute(
        self,
        ctx: ToolContext,
        staged: bool = False,
        ref1: str | None = None,
        ref2: str | None = None,
        pathspec: str | None = None,
        max_lines: int = 200,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="git_diff",
            )
        workspace = ctx.workspace

        cmd = ["git", "diff", "--no-color"]
        if staged:
            cmd.append("--staged")
        if ref1:
            cmd.append(ref1)
            if ref2:
                cmd.append(ref2)
        if pathspec:
            # Sanitize pathspec (basic guard against flag injection)
            if pathspec.startswith("-"):
                return err_actionable(
                    f"pathspec must not start with '-': {pathspec}",
                    received=pathspec,
                    fix="pass a relative path, e.g. pathspec='strategies/momentum_20d/'",
                    tool="git_diff",
                )
            cmd.extend(["--", pathspec])

        try:
            result = subprocess.run(
                cmd,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return err_actionable(
                "git diff timed out (30s)",
                fix="diff may be too large; try pathspec='strategies/<name>/' to limit scope",
                tool="git_diff",
            )
        except FileNotFoundError:
            return err_actionable(
                "git not found in PATH",
                fix="install git or check PATH",
                tool="git_diff",
            )

        if result.returncode != 0:
            return err_actionable(
                f"git diff returned {result.returncode}: {result.stderr.strip()}",
                fix="verify workspace is a git repo (git init if needed)",
                tool="git_diff",
            )

        diff = result.stdout or ""
        lines = diff.splitlines()
        truncated = len(lines) > max_lines
        if truncated:
            diff = "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"

        return tool_ok({
            "diff": diff,
            "total_lines": len(lines),
            "truncated": truncated,
            "staged": staged,
        })


# ── 6. ListHistoryTool ──────────────────────────────────────────────


class ListHistoryTool(BaseTool):
    """列出历史回测记录（results.tsv，可按策略过滤，最新在前）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 查看过去的回测运行记录: 从 strategies/<name>/runs/results.tsv 读取
    # 摘要行 (含关键指标)。不指定 strategy_name 时找第一个 results.tsv。
    #
    # ## 参数
    # - strategy_name: 按策略过滤 (可选)
    # - limit: 最大返回行数 (默认 20)
    #
    # ## 示例
    # {"strategy_name": "momentum_20d"}
    #
    # ## 边界
    # 只读工具; 无 results.tsv 时返回空 runs + message。
    #
    # ## 错误处理范式
    # - 读取失败 → error + fix 检查权限
    # - 无记录不是错误 (返回空列表)
    #
    # ## 相关工具
    # run_backtest: 产生记录; drawdown_analysis: 深度分析
    # ─────────────────────────────────────────────
    """

    name = "list_history"
    description = (
        "列出历史回测记录 (results.tsv); strategy_name 过滤, limit 限量。"
    )
    repeatable = True
    category = "回测"

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str | None = None,
        limit: int = 20,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="list_history",
            )
        workspace = ctx.workspace

        results_path: Path | None = None
        if strategy_name:
            # v2: ctx.results_tsv overrides the legacy strategy runs layout
            if ctx.results_tsv is not None:
                cand = Path(ctx.results_tsv)
            else:
                cand = workspace / "strategies" / strategy_name / "runs" / "results.tsv"
            if cand.exists():
                results_path = cand
        else:
            # Search all strategies for results.tsv
            strategies_dir = workspace / "strategies"
            if strategies_dir.exists():
                for d in sorted(strategies_dir.iterdir()):
                    cand = d / "runs" / "results.tsv"
                    if cand.exists():
                        results_path = cand
                        break

        if results_path is None or not results_path.exists():
            return tool_ok({
                "runs": [],
                "source": None,
                "message": "no results.tsv found",
            })

        try:
            with open(results_path, encoding="utf-8") as f:
                lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        except OSError as exc:
            return err_actionable(
                f"read failed: {exc}",
                fix="check file permissions on results.tsv",
                tool="list_history",
            )

        if not lines:
            return tool_ok({"runs": [], "source": str(results_path)})

        header = lines[0].split("\t")
        rows: list[dict[str, str]] = []
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) < len(header):
                continue
            row = {h: parts[i] for i, h in enumerate(header)}
            rows.append(row)

        # Sort by run name desc (newest first) and apply limit
        rows.sort(key=lambda r: r.get("run", ""), reverse=True)
        rows = rows[:limit]

        return tool_ok({
            "source": str(results_path),
            "n_rows": len(rows),
            "runs": rows,
        })


# ── 9. ListSkillsTool ─────────────────────────────────────────────


class ListSkillsTool(BaseTool):
    """列出可用技能（名称 + 一句话描述）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 列出方法论技能: workspace/.skills/ 优先, 合并内置
    # templates/.skills/, 返回名称/类别/一句话描述, 可按类别过滤。
    # 技能全文用 load_skill 按需加载, 避免大全文直接进 prompt。
    #
    # ## 参数
    # - category: 按类别过滤 (可选; 缺省返回全部)
    #
    # ## 示例
    # {"category": "因子研究"}
    #
    # ## 边界
    # 只读工具; 需要 workspace 上下文; 无技能时返回空列表 (非错误)。
    #
    # ## 错误处理范式
    # - 缺 workspace 上下文 → error, 需 AgentLoop 注入
    # - 扫描/加载异常 → error + 异常信息, 可重试
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # load_skill: 加载技能全文; tool_help: 同类按需加载机制
    # ─────────────────────────────────────────────────────────────
    """

    name = "list_skills"
    description = "列出全部方法技能: 名称/类别/一句话描述; load_skill 获取全文。"
    repeatable = True
    category = "技能"

    def execute(
        self,
        ctx: ToolContext,
        category: str | None = None,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="list_skills")
        workspace = ctx.workspace

        try:
            from strategy_research.kernel.skills import SkillRegistry
            registry = SkillRegistry()

            # Load from workspace .skills/ first, then bundled templates
            workspace_skills = workspace / ".skills"
            if workspace_skills.is_dir():
                registry.load_directory(workspace_skills)

            bundled_skills = Path(__file__).parent.parent.parent / "templates" / ".skills"
            if bundled_skills.is_dir():
                registry.load_directory(bundled_skills)

            if category:
                skills = registry.by_category(category)
            else:
                skills = registry.list_all()

            skill_list = [
                {
                    "name": s.name,
                    "category": s.category,
                    "description": s.description[:120] if s.description else "",
                }
                for s in skills
            ]

            return tool_ok({
                "n_skills": len(skill_list),
                "categories": registry.categories(),
                "skills": skill_list,
            })
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"list_skills failed: {exc}", tool="list_skills")


# ── 10. LoadSkillTool ─────────────────────────────────────────────


class LoadSkillTool(BaseTool):
    """按名称加载技能全文。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 按名称加载技能的完整 markdown 全文 (含 API 契约/工作流/示例),
    # 供 agent 按方法论执行。workspace/.skills/ 覆盖同名内置技能。
    # 先 list_skills 浏览目录, 再决定加载哪个。
    #
    # ## 参数
    # - name: 技能名 (必填)
    #
    # ## 示例
    # {"name": "factor-research"}
    #
    # ## 边界
    # 只读工具; 需要 workspace 上下文; name 为空/非字符串报错。
    #
    # ## 错误处理范式
    # - name 缺失/非法 → error + 提示
    # - 技能不存在 → error + available 列表 (最多 20 个)
    # - 内部异常 → error + 异常信息, 可重试
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # list_skills: 目录浏览; tool_help: 同类按需加载机制
    # ─────────────────────────────────────────────────────────────
    """

    name = "skill"
    description = "按名称加载技能完整 markdown 文档 (含 API 契约/工作流/示例)。"
    repeatable = True
    category = "技能"

    def execute(
        self,
        ctx: ToolContext,
        name: str,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="skill")
        workspace = ctx.workspace
        if not isinstance(name, str) or not name:
            return err_actionable("missing or invalid 'name'", tool="skill")

        try:
            from strategy_research.kernel.skills import SkillRegistry
            registry = SkillRegistry()

            # Load from workspace .skills/ first (user overrides), then bundled
            workspace_skills = workspace / ".skills"
            if workspace_skills.is_dir():
                registry.load_directory(workspace_skills)

            bundled_skills = Path(__file__).parent.parent.parent / "templates" / ".skills"
            if bundled_skills.is_dir():
                registry.load_directory(bundled_skills)

            skill = registry.get(name)
            if skill is None:
                available = [s.name for s in registry.list_all()][:20]
                return err_actionable(
                    f"skill '{name}' not found",
                    extra={"available": available},
                )

            return tool_ok({
                "name": skill.name,
                "category": skill.category,
                "description": skill.description,
                "tags": skill.tags,
                "content": skill.content,
            })
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"load_skill failed: {exc}", tool="skill")
