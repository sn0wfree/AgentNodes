"""数据清洗工具: clean_data（preset/steps 自定义）。"""

from __future__ import annotations

import logging
from typing import Any

from strategy_research.kernel.agent.tools import (
    EFFECT_DB,
    BaseTool,
    ToolContext,
)
from .utils import err_actionable, tool_ok

logger = logging.getLogger(__name__)




# ── DataCleanTool ──────────────────────────────────────────────────


class DataCleanTool(BaseTool):
    """清洗工作区 OHLCV 数据（去重/填充/异常/变频）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.2.0
    # 变更: v1.2.0 迁移 v2 (显式签名 + ToolContext); v1.1.0 修复 price_data 查询
    #
    # ## 用途
    # 清洗策略的 OHLCV 数据: 去重/缺失填充/异常检测/变频。
    # preset 快捷执行或 steps+params 自定义。
    #
    # ## 参数
    # - strategy_name: 策略名 (默认 'default')
    # - preset: 预设模式 (quick/standard/thorough/resample/custom, 默认 standard)
    # - steps: 自定义步骤列表 (custom 模式)
    # - params: 自定义参数
    # - dry_run: 只生成报告不写库 (默认 True)
    #
    # ## 示例
    # {"strategy_name": "default", "preset": "standard", "dry_run": False}
    #
    # ## 边界
    # 写工具 (effects: db); dry_run=False 会清空并重写该策略的 price_data;
    # 幂等 (重跑安全)。
    #
    # ## 错误处理范式
    # - 无效 preset → error + expected 枚举
    # - 无 DB/空表 → error + fix 提示 get_market_data
    # - 均可安全重试 (dry_run=False 重跑覆盖)
    #
    # ## 相关工具
    # get_market_data: 数据来源; run_backtest: 清洗后回测
    # ─────────────────────────────────────────────
    """

    name = "clean_data"
    description = (
        "清洗 OHLCV 数据 (去重/缺失填充/异常检测/变频); preset 或 steps+params; "
        "dry_run 只出报告, False 写回 DuckDB。"
    )
    repeatable = True
    category = "数据"
    effects = frozenset({EFFECT_DB})

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str = "default",
        preset: str = "standard",
        steps: list[str] | None = None,
        params: dict[str, Any] | None = None,
        dry_run: bool = True,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="clean_data",
            )
        workspace = ctx.workspace

        # 验证 preset
        from strategy_research.kernel.agent.capability import resolve

        PRESETS = resolve("data.clean").PRESETS
        if preset not in PRESETS:
            return err_actionable(
                f"invalid preset: {preset}",
                received=preset,
                expected="one of: quick, standard, thorough, resample, custom",
                fix="use a valid preset name",
                tool="clean_data",
            )

        try:
            # 加载数据
            from strategy_research.kernel.agent.capability import resolve

            get_connection = resolve("db.get_connection")
            clean_data = resolve("data.clean").clean_data
            if not (workspace / "data.duckdb").exists():
                return err_actionable(
                    "no DuckDB database in workspace",
                    received={"strategy_name": strategy_name},
                    fix="use get_market_data to fetch and persist data first",
                    tool="clean_data",
                )
            conn = get_connection(workspace, read_only=dry_run)
            if conn is None:
                return err_actionable(
                    "failed to open DuckDB",
                    tool="clean_data",
                )

            df = conn.execute(
                "SELECT date, asset_code AS asset, open, high, low, close, volume "
                "FROM price_data WHERE strategy_name = ?",
                [strategy_name]
            ).fetch_df()

            if df.empty:
                conn.close()
                return err_actionable(
                    "ohlcv table is empty",
                    received={"strategy_name": strategy_name},
                    fix="use get_market_data to fetch data first",
                    tool="clean_data",
                )

            # 执行清洗
            result_df, report = clean_data(df, preset, steps, params, dry_run)

            # 如果不是 dry_run，保存结果
            if not dry_run:
                from strategy_research.kernel.agent.capability import resolve

                save_ohlcv_to_db = resolve("db.save_ohlcv_to_db")
                # 清空旧数据 (ohlcv 是视图不可 DELETE，直接清 price_data)
                conn.execute(
                    "DELETE FROM price_data WHERE strategy_name = ?",
                    [strategy_name]
                )
                # 保存新数据
                save_ohlcv_to_db(workspace, {strategy_name: result_df}, strategy_name)

            conn.close()

            return tool_ok({
                "strategy_name": strategy_name,
                "preset": preset,
                "dry_run": dry_run,
                "report": {
                    "initial_rows": report.initial_rows,
                    "final_rows": report.final_rows,
                    "steps_applied": report.steps_applied,
                    "duplicates_removed": report.duplicates_removed,
                    "missing_filled": report.missing_filled,
                    "outliers_detected": report.outliers_detected,
                    "resampled": report.resampled,
                    "original_freq": report.original_freq,
                    "target_freq": report.target_freq,
                    "params_applied": report.params_applied,
                },
                "message": report.message,
            })

        except Exception as exc:
            logger.exception("clean_data failed")
            return err_actionable(
                f"clean_data failed: {exc}",
                tool="clean_data",
            )
