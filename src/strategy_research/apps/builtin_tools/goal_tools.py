"""Goal management tools for the agent.

Tools:
    CreateGoalTool      - create/replace a research goal
    AddEvidenceTool     - append evidence to current goal
    CompleteGoalTool    - mark goal complete (lite mode)
    GetGoalStatusTool   - get current goal snapshot
    ListGoalsTool       - list goals with optional filter
"""

from __future__ import annotations

import json
import logging
from typing import Any

from strategy_research.kernel.agent.tools import EFFECT_DB, BaseTool, ToolContext
from strategy_research.kernel.agent.builtin_tools.utils import err_actionable, tool_ok, try_unwrap_list

logger = logging.getLogger(__name__)


# ── Shared helpers ───────────────────────────────────────────────────


def _get_store():
    """Get a GoalStore instance with default DB path."""
    from strategy_research.kernel.agent.capability import resolve

    GoalStore = resolve("goal.store")
    return GoalStore()


def _missing_required_criteria(store, goal_id: str) -> list[str]:
    """Return 'text (criterion_id)' for required criteria without evidence."""
    try:
        criteria = store.list_criteria(goal_id)
        evidence = store.list_evidence(goal_id)
        covered = {ev.criterion_id for ev in evidence if ev.criterion_id}
        return [
            f"{c.text} ({c.criterion_id})"
            for c in criteria
            if c.required and c.criterion_id not in covered
        ]
    except Exception:  # noqa: BLE001
        return []


def _get_session_id(kwargs: dict[str, Any]) -> str:
    """Extract session_id from kwargs (injected by AgentLoop)."""
    sid = kwargs.get("session_id")
    if not sid:
        return "default"
    return str(sid)


# ── 1. CreateGoalTool ──────────────────────────────────────────────


class CreateGoalTool(BaseTool):
    """创建或替换当前会话的研究目标。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 为当前会话创建研究目标 (goals.db), 已存在目标则被取代。
    # criteria 为空时用默认标准。研究开始前用本工具确立目标,
    # 研究中用 add_evidence 记录证据。
    #
    # ## 参数
    # - objective: 研究目标描述 (必填, 非空)
    # - criteria: 完成标准列表 (可选, list[str]; 字符串/JSON/单键
    #   包裹均容错解析; 缺省用默认标准)
    #
    # ## 示例
    # {"objective": "评估动量因子在 A 股的有效性",
    #  "criteria": ["完成截面 IC 分析", "完成分层回测"]}
    #
    # ## 边界
    # 写 goals.db (effects=db); session_id 由框架注入, 无会话回退
    # default; 已存在目标被取代, 创建前可先 get_goal_status。
    #
    # ## 错误处理范式
    # - objective 缺失/空 → error + expected/fix
    # - 存储异常 → error + 输入回显, 验证参数后重试
    # - 幂等性: 替换语义, 重复调用会覆盖旧目标
    #
    # ## 相关工具
    # 后续: add_evidence / get_goal_status / complete_goal
    # ─────────────────────────────────────────────────────────────
    """

    name = "create_goal"
    description = "为当前会话创建研究目标 (已存在则取代); 返回 goal_id 与状态。"
    repeatable = False
    strict = True
    category = "Goal"
    effects = frozenset({EFFECT_DB})
    def execute(
        self,
        ctx: ToolContext,
        objective: str,
        criteria: list[str] | None = None,
    ) -> str:
        session_id = ctx.session_id or "default"
        if not objective:
            return err_actionable(
                "missing 'objective'",
                expected="non-empty research objective string, e.g. 'build a momentum strategy for A-shares'",
                fix="pass a non-empty objective describing the research goal",
                tool="create_goal",
            )

        # Defensive: criteria may be string, list, or wrapped list
        if isinstance(criteria, str):
            try:
                criteria = json.loads(criteria)
            except (json.JSONDecodeError, TypeError):
                criteria = [c.strip() for c in criteria.split(",") if c.strip()]
        elif isinstance(criteria, dict):
            # LLM wrapped list in dict (e.g. {"items": [...]})
            unwrapped = try_unwrap_list(criteria)
            if unwrapped is not None:
                criteria = unwrapped

        try:
            from strategy_research.kernel.agent.capability import resolve

            default_goal_criteria = resolve("goal.context").default_goal_criteria
            store = _get_store()
            goal = store.replace_goal(
                session_id=session_id,
                objective=objective,
                criteria=criteria or default_goal_criteria(),
            )
            return tool_ok({
                "goal_id": goal.goal_id,
                "goal_status": goal.status.value,
                "objective": goal.objective,
                "progress_percent": goal.progress_percent,
            })
        except Exception as exc:
            logger.exception("create_goal failed")
            return err_actionable(
                f"create_goal failed: {exc}",
                received={"objective": objective, "criteria": criteria},
                fix="verify objective is a non-empty string and criteria is a list of strings",
                tool="create_goal",
            )


# ── 2. AddEvidenceTool ─────────────────────────────────────────────


class AddEvidenceTool(BaseTool):
    """为当前目标添加证据条目。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.2.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板); v1.2.0 无
    # criterion_id 时自动挂载到全部 required criteria (E2E 修复)
    #
    # ## 用途
    # 向当前会话的 active goal 追加证据条目 (指标/观测/结论), 可关联
    # criterion 推动进度百分比; 证据累积完成后用 complete_goal 收尾。
    # 未指定 criterion_id 时自动挂载到全部 required criteria (一条
    # 证据同时覆盖所有完成标准), 返回 auto_attached_to 列表。
    #
    # ## 参数
    # - text: 证据文本 (必填, 非空)
    # - criterion_id: 关联的完成标准 id (可选; 缺省自动挂全部 required)
    # - source_type: 证据来源类型 (默认 evidence)
    # - run_id: 关联的回测 run id (可选)
    #
    # ## 示例
    # {"text": "截面 IC = 0.045 (2023-01-01 至 2023-12-31)",
    #  "criterion_id": "c1"}
    #
    # ## 边界
    # 写 goals.db (effects=db); 需要会话已有 active goal; session_id
    # 由框架注入, 无会话回退 default。自动挂载仅在 goal 存在 required
    # criteria 时发生; 无 criteria 的目标按无关联证据追加。
    #
    # ## 错误处理范式
    # - text 缺失 → error + expected/fix
    # - 无 active goal → error + fix (先 create_goal)
    # - 存储异常 → error + 文本预览回显, 可重试
    # - 幂等性: 每次追加独立证据记录
    #
    # ## 相关工具
    # 前置: create_goal; 后续: get_goal_status / complete_goal
    # ─────────────────────────────────────────────────────────────
    """

    name = "add_evidence"
    description = "为目标添加证据条目 (指标/观测); 关联可选 criterion/run。"
    repeatable = True
    category = "Goal"
    effects = frozenset({EFFECT_DB})
    def execute(
        self,
        ctx: ToolContext,
        text: str,
        criterion_id: str | None = None,
        source_type: str = "evidence",
        run_id: str | None = None,
    ) -> str:
        session_id = ctx.session_id or "default"
        if not text:
            return err_actionable(
                "missing 'text'",
                expected="non-empty evidence string describing the observation/metric",
                fix="pass a non-empty text, e.g. text='Backtest IC = 0.045 on 2023-12-15'",
                tool="add_evidence",
            )



        try:
            from strategy_research.kernel.agent.capability import resolve

            EvidenceInput = resolve("goal.models").EvidenceInput
            store = _get_store()
            current = store.get_current_goal(session_id)
            if current is None:
                return err_actionable(
                    "no active goal for this session; use create_goal first",
                    fix="call create_goal(objective='...') first to set a research goal",
                    tool="add_evidence",
                )

            evidence = EvidenceInput(
                text=text,
                criterion_id=criterion_id,
                source_type=source_type,
                run_id=run_id,
            )
            auto_attached_to: list[str] = []
            if criterion_id is None:
                required = [
                    c for c in store.list_criteria(current.goal_id)
                    if c.required
                ]
                if required:
                    for c in required:
                        store.append_evidence(
                            session_id=session_id,
                            goal_id=current.goal_id,
                            expected_goal_id=current.goal_id,
                            evidence=EvidenceInput(
                                text=text,
                                criterion_id=c.criterion_id,
                                source_type=source_type,
                                run_id=run_id,
                            ),
                        )
                        auto_attached_to.append(c.criterion_id)
                    record = None
                else:
                    record = store.append_evidence(
                        session_id=session_id,
                        goal_id=current.goal_id,
                        expected_goal_id=current.goal_id,
                        evidence=evidence,
                    )
            else:
                record = store.append_evidence(
                    session_id=session_id,
                    goal_id=current.goal_id,
                    expected_goal_id=current.goal_id,
                    evidence=evidence,
                )

            # Re-fetch to get updated progress
            updated = store.get_current_goal(session_id)
            return tool_ok({
                "evidence_id": record.evidence_id if record else auto_attached_to[0],
                "goal_id": current.goal_id,
                "auto_attached_to": auto_attached_to or None,
                "progress_percent": updated.progress_percent if updated else 0,
            })
        except Exception as exc:
            logger.exception("add_evidence failed")
            return err_actionable(
                f"add_evidence failed: {exc}",
                received={"text": text[:200] if isinstance(text, str) else text},
                fix="check the error detail and verify the session has an active goal",
                tool="add_evidence",
            )


# ── 3. CompleteGoalTool ────────────────────────────────────────────


class CompleteGoalTool(BaseTool):
    """完成当前目标并附上总结。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.2.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板); v1.2.0 缺证据
    # 失败时 fix 定向列出缺失 criterion (E2E 修复)
    #
    # ## 用途
    # 将当前会话的 active goal 标记为完成 (lite 模式), 可附 recap
    # 总结。必填 criterion 缺证据时会阻止完成。
    #
    # ## 参数
    # - recap: 完成总结 (可选)
    #
    # ## 示例
    # {"recap": "动量因子截面 IC 显著, 回测 Sharpe 1.2"}
    #
    # ## 边界
    # 写 goals.db (effects=db); 需要会话已有 active goal; 无目标时
    # 提示先 create_goal; session_id 由框架注入。
    #
    # ## 错误处理范式
    # - 无 active goal → error + fix (先 create_goal)
    # - 必填 criterion 缺证据 → 完成被拒, fix 列出缺失 criterion 并
    #   指引 add_evidence(criterion_id=...) 补齐后重试
    # - 存储异常 → error, 检查目标是否已完成
    # - 幂等性: 已完成的目标重复调用会报错
    #
    # ## 相关工具
    # 前置: create_goal / add_evidence; 后续: list_goals
    # ─────────────────────────────────────────────────────────────
    """

    name = "complete_goal"
    description = "将当前目标标记为完成, 可附 recap 总结。"
    repeatable = True
    category = "Goal"
    effects = frozenset({EFFECT_DB})
    def execute(
        self,
        ctx: ToolContext,
        recap: str | None = None,
    ) -> str:
        session_id = ctx.session_id or "default"
        store = None
        current = None

        try:
            store = _get_store()
            current = store.get_current_goal(session_id)
            if current is None:
                return err_actionable(
                    "no active goal for this session",
                    fix="call create_goal(objective='...') first to set a research goal",
                    tool="complete_goal",
                )

            updated = store.complete_lite(
                session_id=session_id,
                goal_id=current.goal_id,
                expected_goal_id=current.goal_id,
                recap=recap,
            )
            return tool_ok({
                "goal_id": updated.goal_id,
                "goal_status": updated.status.value,
                "recap": updated.recap,
            })
        except Exception as exc:
            logger.exception("complete_goal failed")
            msg = str(exc)
            if "lacks evidence" in msg and store is not None and current is not None:
                missing = _missing_required_criteria(store, current.goal_id)
                fix = (
                    f"required criteria lack evidence: {missing or 'n/a'}; "
                    "call add_evidence(criterion_id=..., text=...) for each "
                    "missing criterion, then retry complete_goal"
                )
                return err_actionable(
                    f"complete_goal failed: {msg}",
                    fix=fix,
                    tool="complete_goal",
                )
            return err_actionable(
                f"complete_goal failed: {exc}",
                fix="check the error detail; the goal may have already been completed",
                tool="complete_goal",
            )


# ── 4. GetGoalStatusTool ───────────────────────────────────────────


class GetGoalStatusTool(BaseTool):
    """查询当前目标状态与证据。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 查询当前会话 active goal 的快照: 状态/进度百分比/完成标准及
    # 各自状态/证据数。研究过程检查进度或决定是否 complete_goal。
    #
    # ## 参数
    # (无显式业务参数; session_id 由框架注入)
    #
    # ## 示例
    # {}
    #
    # ## 边界
    # 只读工具 (不写库); 无 active goal 时返回 {has_goal: false}
    # 而非错误; session_id 由框架注入。
    #
    # ## 错误处理范式
    # - 数据库不可访问 → error + fix
    # - 无目标 → 正常返回 has_goal=false (非错误)
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: create_goal; 后续: add_evidence / complete_goal
    # ─────────────────────────────────────────────────────────────
    """

    name = "get_goal_status"
    description = "查询当前会话目标: 状态/进度/标准/证据数。"
    repeatable = True
    category = "Goal"
    def execute(
        self,
        ctx: ToolContext,
    ) -> str:
        session_id = ctx.session_id or "default"

        try:
            store = _get_store()
            snapshot = store.get_current_snapshot(session_id)
            if snapshot is None:
                return tool_ok({
                    "has_goal": False,
                    "message": "no active goal",
                })

            goal = snapshot.get("goal", {})
            criteria = snapshot.get("criteria", [])
            evidence_count = snapshot.get("evidence_count", 0)

            return tool_ok({
                "has_goal": True,
                "goal_id": goal.get("goal_id"),
                "goal_status": goal.get("status"),
                "objective": goal.get("objective"),
                "progress_percent": goal.get("progress_percent", 0),
                "criteria_count": len(criteria),
                "evidence_count": evidence_count,
                "criteria": [
                    {
                        "criterion_id": c.get("criterion_id"),
                        "text": c.get("text"),
                        "status": c.get("status"),
                        "required": c.get("required", True),
                    }
                    for c in criteria
                ],
            })
        except Exception as exc:
            logger.exception("get_goal_status failed")
            return err_actionable(
                f"get_goal_status failed: {exc}",
                fix="verify the database is accessible and the session_id is valid",
                tool="get_goal_status",
            )


# ── 5. ListGoalsTool ───────────────────────────────────────────────


class ListGoalsTool(BaseTool):
    """列出目标（可按状态过滤）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 列出 goals.db 中的研究目标摘要 (goal_id/会话/状态/进度/创建时间),
    # 可按状态过滤, 用于回顾历史目标或恢复研究。
    #
    # ## 参数
    # - status: 过滤状态 (可选; active/complete/abandoned, 缺省全部)
    # - limit: 返回条数上限 (默认 10)
    #
    # ## 示例
    # {"status": "active", "limit": 20}
    #
    # ## 边界
    # 只读工具 (不写库); session_id 由框架注入; 未指定会话时列出
    # 全部会话的目标 (跨会话浏览)。
    #
    # ## 错误处理范式
    # - status 非法 → error + expected 枚举提示
    # - 数据库异常 → error + fix
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # get_goal_status: 当前目标快照; create_goal: 创建目标
    # ─────────────────────────────────────────────────────────────
    """

    name = "list_goals"
    description = "列出目标列表 (可按状态过滤), 返回目标摘要与计数。"
    repeatable = True
    category = "Goal"
    def execute(
        self,
        ctx: ToolContext,
        status: str | None = None,
        limit: int = 10,
    ) -> str:
        session_id = ctx.session_id
        status_str = status

        try:
            from strategy_research.kernel.agent.capability import resolve

            GoalStatus = resolve("goal.models").GoalStatus
            store = _get_store()
            status_filter = GoalStatus(status_str) if status_str else None
            goals = store.list_goals(
                session_id=session_id,
                status=status_filter,
                limit=limit,
            )
            return tool_ok({
                "goals": [
                    {
                        "goal_id": g.goal_id,
                        "session_id": g.session_id,
                        "goal_status": g.status.value,
                        "objective": g.objective,
                        "progress_percent": g.progress_percent,
                        "created_at": g.created_at,
                    }
                    for g in goals
                ],
                "count": len(goals),
            })
        except ValueError:
            # GoalStatus enum parsing failed
            return err_actionable(
                f"invalid status value: {status_str!r}",
                received=status_str,
                expected="one of: active, complete, abandoned (or omit for all)",
                fix="either omit `status` to list all, or pass a valid status like 'active'",
                tool="list_goals",
            )
        except Exception as exc:
            logger.exception("list_goals failed")
            return err_actionable(
                f"list_goals failed: {exc}",
                fix="check the database connection and parameter values",
                tool="list_goals",
            )


# ── Registration ────────────────────────────────────────────────────


def register_goal_tools(registry) -> None:
    """Register all goal tools with the given ToolRegistry."""
    registry.register(CreateGoalTool())
    registry.register(AddEvidenceTool())
    registry.register(CompleteGoalTool())
    registry.register(GetGoalStatusTool())
    registry.register(ListGoalsTool())


__all__ = [
    "CreateGoalTool",
    "AddEvidenceTool",
    "CompleteGoalTool",
    "GetGoalStatusTool",
    "ListGoalsTool",
    "register_goal_tools",
]
