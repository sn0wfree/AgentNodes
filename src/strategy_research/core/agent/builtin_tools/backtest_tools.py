"""回测编排工具: run_backtest（配置加载→数据准备→就绪门→引擎执行）。"""

from __future__ import annotations

import json
import logging
from typing import cast
from pathlib import Path

from strategy_research.apps.backtest import run_backtest_from_yaml
from ..tools import (
    EFFECT_DB,
    EFFECT_FS,
    BaseTool,
    ToolContext,
    ToolError,
)

logger = logging.getLogger(__name__)




# ── 3. RunBacktestTool ──────────────────────────────────────────────


class RunBacktestTool(BaseTool):
    """从策略配置运行回测并写入 runs/ 与 DuckDB。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.2.0
    # 变更: v1.2.0 数据就绪性门禁 (C1~C6 预检, 不可跑时返回诊断不建 run);
    #       执行拆分为 3 步组合 (config_load → data_gate → engine_run),
    #       错误带 step 标识 (docs/run-backtest-data-gate.md)
    #
    # ## 用途
    # 读取 strategies/<name>/config.yaml 运行回测, 产出新 run 写入
    # runs/<name>/ 与 DuckDB。策略配置就绪且数据已入库后验证表现。
    # 数据未入库时先 get_market_data; 只看历史结果用 list_history。
    #
    # ## 参数
    # - strategy_name: 策略目录名 (必填, strategies/<name>/config.yaml 须存在)
    # - action: 运行标注 (审计用, 默认 'agent')
    # - description: 可选描述
    # - yaml_path: 覆盖默认 config 路径 (相对 workspace)
    #
    # ## 示例
    # {"strategy_name": "momentum_20d"}
    #
    # ## 边界
    # 写工具 (effects: db + fs); 前置: price_data 已有该策略数据;
    # 同策略重复运行产生新 run, 不覆盖旧 run。
    #
    # ## 错误处理范式
    # - 缺 strategy_name → error + expected 示例
    # - 策略目录不存在 → fix 提示 list_files 查看 strategies/
    # - 数据未就绪 (缺资产/窗口不足/密度不足/因子语法错) → 执行前拦截,
    #   error 带 readiness 报告 (step=data_gate), 不创建 run
    # - 运行期因子失败 → 返回 factor_failures 摘要 + 落盘 factor_failures.json
    # - 配置 YAML 非法 → fix 指向 config.yaml 检查
    # - 所有失败均可安全重试 (无部分写入遗留)
    #
    # ## 相关工具
    # get_market_data: 数据前置; check_data: 数据就绪性完整报告;
    # clean_data: 行级清洗; list_history/drawdown_analysis/
    # benchmark_comparison: 结果消费
    # ─────────────────────────────────────────────
    """

    name = "run_backtest"
    description = (
        "从 strategies/<name>/config.yaml 运行回测, 新 run 写入 runs/ 与 DuckDB。"
    )
    repeatable = True
    category = "回测"
    effects = frozenset({EFFECT_DB, EFFECT_FS})

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str = "",
        action: str = "agent",
        description: str = "",
        yaml_path: str | None = None,
        background: bool = False,
    ) -> str:
        if ctx.workspace is None:
            raise ToolError(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="run_backtest",
            )
        workspace = ctx.workspace

        if not isinstance(strategy_name, str) or not strategy_name:
            raise ToolError(
                "missing or invalid 'strategy_name'",
                received=strategy_name,
                expected="non-empty strategy name, e.g. 'momentum_20d'",
                fix="list strategies with list_files(workspace=..., path='strategies') and pick an existing name",
                tool="run_backtest",
            )
        action = action or "agent"
        description = description or ""

        if background:
            return self._execute_background(
                ctx, workspace, strategy_name, action, description, yaml_path,
            )
        return self._execute_foreground(
            ctx, workspace, strategy_name, action, description, yaml_path,
        )

    def _execute_foreground(
        self,
        ctx: ToolContext,
        workspace,
        strategy_name: str,
        action: str,
        description: str,
        yaml_path: str | None,
    ) -> str:
        # ── 步骤 1: 配置加载 ────────────────────────────────────
        cfg_step = ConfigLoadStep()
        out = json.loads(cast(str, cfg_step.execute(
            ctx=ctx, workspace=workspace, strategy_name=strategy_name,
            yaml_path=yaml_path,
        )))
        if out.get("status") == "error":
            return json.dumps(out, ensure_ascii=False)
        cfg = out["cfg"]
        resolved_yaml = out.get("yaml_path")

        # ── 步骤 2: 数据准备 ────────────────────────────────────
        # config source=auto/auto+duckdb 语义下引擎会在线获取数据 ——
        # 数据准备先执行（写 DB），门禁随后检查的才是「最终将用于回测
        # 的数据」（docs/run-backtest-data-gate.md）。
        prepare_step = DataPrepareStep()
        out = json.loads(cast(str, prepare_step.execute(
            ctx=ctx, workspace=workspace, cfg=cfg,
        )))
        if out.get("status") == "error":
            return json.dumps(out, ensure_ascii=False)

        # ── 步骤 3: 数据就绪性门禁 (C1~C6, 轻量) ────────────────
        gate_step = DataReadinessStep()
        out = json.loads(cast(str, gate_step.execute(
            ctx=ctx, workspace=workspace, strategy_name=strategy_name, cfg=cfg,
        )))
        if out.get("status") == "error":
            return json.dumps(out, ensure_ascii=False)
        readiness_summary = out.get("report", {})

        # ── 步骤 4: 引擎执行 + 产物落盘 ─────────────────────────
        engine_step = EngineRunStep()
        out = json.loads(cast(str, engine_step.execute(
            ctx=ctx, workspace=workspace, strategy_name=strategy_name,
            cfg=cfg, yaml_path=resolved_yaml, action=action,
            description=description,
        )))
        if out.get("status") == "error":
            return json.dumps(out, ensure_ascii=False)

        run_name = out.get("run", "")
        # v2: artifact paths honor ctx.runs_dir (study scenario); legacy
        # fallback keeps runs/<strategy>/<run> for CLI/tool-driven flows.
        runs_root = ctx.runs_dir if ctx.runs_dir is not None \
            else workspace / "runs" / strategy_name
        artifacts = {
            "equity_curve": f"{runs_root}/{run_name}/equity_curve.csv",
            "metrics": f"{runs_root}/{run_name}/metrics.json",
            "run_card": f"{runs_root}/{run_name}/run_card.json",
        }
        return json.dumps({
            "status": "ok",
            "run": run_name,
            "strategy": strategy_name,
            "metrics": out.get("metrics", {}),
            "run_status": out.get("status", "success"),
            "factor_failures": out.get("factor_failures", []),
            "warnings": out.get("warnings", []),
            "readiness": readiness_summary,
            # 产物引用（相对 workspace）。净值数据本身不进上下文：
            # 需要展示曲线时用 show_chart(source_file=...) 引用文件，
            # 需要排查时用 read_file 读文件内容。
            "artifacts": artifacts,
        }, ensure_ascii=False, default=str)

    def _execute_background(
        self,
        ctx: ToolContext,
        workspace,
        strategy_name: str,
        action: str,
        description: str,
        yaml_path: str | None,
    ) -> str:
        """后台模式：整条工具链在线程中执行，进度写日志，立即返回
        ``{status:"running", task_id, log}``。

        轮询：``run_bg_command(action="wait"|"status"|"log", task_id=...)``。
        完成态 ``status`` 直接带回结果（metrics/artifacts）。
        """
        import threading
        import uuid as _uuid
        from pathlib import Path as _Path

        from .bg_tools import (
            log_progress,
            register_thread_task,
            set_task_result,
        )

        log_path = _Path(str(workspace)) / "bg_tasks" / f"{_uuid.uuid4().hex[:8]}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        owner = getattr(ctx, "session_id", None) or "cli"

        def _run() -> None:
            try:
                log_progress(log_path, "[backtest] step: config load")
                cfg_step = ConfigLoadStep()
                out = json.loads(cast(str, cfg_step.execute(
                    ctx=ctx, workspace=workspace, strategy_name=strategy_name,
                    yaml_path=yaml_path,
                )))
                if out.get("status") == "error":
                    set_task_result(task_id, {"status": "error", **out})
                    return
                cfg = out["cfg"]
                resolved_yaml = out.get("yaml_path")

                log_progress(log_path, "[backtest] step: data prepare")
                prepare_step = DataPrepareStep()
                out = json.loads(cast(str, prepare_step.execute(
                    ctx=ctx, workspace=workspace, cfg=cfg,
                )))
                if out.get("status") == "error":
                    set_task_result(task_id, {"status": "error", **out})
                    return

                log_progress(log_path, "[backtest] step: data readiness gate")
                gate_step = DataReadinessStep()
                out = json.loads(cast(str, gate_step.execute(
                    ctx=ctx, workspace=workspace, strategy_name=strategy_name,
                    cfg=cfg,
                )))
                if out.get("status") == "error":
                    set_task_result(task_id, {"status": "error", **out})
                    return
                readiness_summary = out.get("report", {})

                log_progress(log_path, "[backtest] step: engine running")
                engine_step = EngineRunStep()
                out = json.loads(cast(str, engine_step.execute(
                    ctx=ctx, workspace=workspace, strategy_name=strategy_name,
                    cfg=cfg, yaml_path=resolved_yaml, action=action,
                    description=description,
                )))
                if out.get("status") == "error":
                    set_task_result(task_id, {"status": "error", **out})
                    return

                run_name = out.get("run", "")
                runs_root = ctx.runs_dir if ctx.runs_dir is not None \
                    else workspace / "runs" / strategy_name
                artifacts = {
                    "equity_curve": f"{runs_root}/{run_name}/equity_curve.csv",
                    "metrics": f"{runs_root}/{run_name}/metrics.json",
                    "run_card": f"{runs_root}/{run_name}/run_card.json",
                }
                log_progress(
                    log_path, f"[backtest] done run={run_name} status={out.get('status', 'success')}",
                )
                set_task_result(task_id, {
                    "status": "ok",
                    "run": run_name,
                    "strategy": strategy_name,
                    "metrics": out.get("metrics", {}),
                    "run_status": out.get("status", "success"),
                    "factor_failures": out.get("factor_failures", []),
                    "warnings": out.get("warnings", []),
                    "readiness": readiness_summary,
                    "artifacts": artifacts,
                })
            except Exception as exc:  # noqa: BLE001
                logger.exception("background run_backtest failed")
                log_progress(log_path, f"[backtest] failed: {exc}")
                set_task_result(task_id, {
                    "status": "error", "error": f"{exc}",
                })

        thread = threading.Thread(target=_run, daemon=True, name=f"bg-backtest-{strategy_name}")
        task_id = register_thread_task(
            thread, log_path, f"run_backtest(background=True) {strategy_name}",
            owner=owner,
        )
        thread.start()
        return json.dumps({
            "status": "running", "task_id": task_id,
            "log": str(log_path),
            "fix": "轮询: run_bg_command(action='wait', task_id=...) 观察窗 ×3；"
                   "完成态 status 直接带回结果",
        }, ensure_ascii=False)


# ── RunBacktestTool 组合子步骤 ───────────────────────────────────────
# 每步是 BaseTool 子类（自动获得 tool_errors 标准化），但不注册进
# ToolRegistry（不占 LLM schema）。RunBacktestTool 顺序编排，错误带
# step 标识，精确定位失败环节（docs/run-backtest-data-gate.md）。


class ConfigLoadStep(BaseTool):
    """子步骤: 读取并解析 strategies/<name>/config.yaml。"""

    name = "config_load"

    def execute(
        self,
        ctx: ToolContext,
        workspace,
        strategy_name: str,
        yaml_path: str | None = None,
    ) -> dict:
        from strategy_research.apps.config_runner import load_yaml_config

        if yaml_path is not None:
            yaml_path = str(workspace / yaml_path)
        else:
            # v2: ctx.strategy_dir overrides the legacy strategies/<name> layout
            base = ctx.strategy_dir if ctx.strategy_dir is not None \
                else workspace / "strategies" / strategy_name
            yaml_path = str(base / "config.yaml")

        if not Path(yaml_path).exists():
            raise ToolError(
                f"配置文件不存在: {yaml_path}",
                received=strategy_name,
                expected="strategies/<name>/config.yaml",
                fix="check that the strategy directory exists; use list_files(workspace=..., path='strategies')",
                tool="run_backtest",
                step="config_load",
            )
        try:
            cfg = load_yaml_config(yaml_path)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(
                f"配置 YAML 非法: {exc}",
                received=strategy_name,
                fix="fix syntax in strategies/<name>/config.yaml",
                tool="run_backtest",
                step="config_load",
            ) from exc
        return {"cfg": cfg, "yaml_path": yaml_path}


class DataPrepareStep(BaseTool):
    """子步骤: 数据准备（执行 config_runner.load_data）。

    source=auto/auto+duckdb 语义下引擎会在线获取数据并写入 DuckDB；
    提前到这里执行，门禁（DataReadinessStep）随后检查的才是最终将
    用于回测的数据。幂等（fetch 用 INSERT OR REPLACE）。
    """

    name = "data_prepare"

    def execute(
        self,
        ctx: ToolContext,
        workspace,
        cfg: dict,
    ) -> dict:
        from strategy_research.apps.config_runner import load_data

        try:
            df = load_data(cfg, workspace)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(
                f"数据准备失败: {exc}",
                fix="检查网络/数据源可用性，或先用 get_market_data 手动补齐",
                tool="run_backtest",
                step="data_prepare",
            ) from exc
        if df is None or df.empty:
            raise ToolError(
                "数据为空: 在线获取失败且本地无该策略数据",
                fix="get_market_data(codes=[...], strategy_name='<当前策略名>') 手动获取后重试",
                tool="run_backtest",
                step="data_prepare",
            )
        return {"rows": int(len(df)), "cols": list(df.columns)}


class DataReadinessStep(BaseTool):
    """子步骤: 数据就绪性门禁（C1~C6，轻量只读）。不可跑时拦截。"""

    name = "data_gate"

    def execute(
        self,
        ctx: ToolContext,
        workspace,
        strategy_name: str,
        cfg: dict,
    ) -> dict:
        from strategy_research.apps.data_readiness import check_data_readiness

        try:
            report = check_data_readiness(workspace, strategy_name, cfg=cfg)
        except Exception as exc:  # noqa: BLE001
            # 检查自身异常不阻塞主流程（降级为 warn 并继续）
            return {"report": {"ok": True, "checks": [
                {"id": "C0", "name": "就绪性检查", "status": "warn",
                 "detail": f"检查不可用: {str(exc)[:150]}",
                 "fix_hint": "继续执行（门禁降级）"},
            ]}}
        if not report.ok:
            fails = [c for c in report.checks if c.status == "fail"]
            # 措辞带后果说明: 门禁拦的是「结果不可信」而非「崩溃」
            details = "; ".join(f"{c.id} {c.detail}" for c in fails[:5])
            raise ToolError(
                f"数据未就绪，共 {len(fails)} 个阻断项（回测结果将不可信，"
                f"已在创建 run 之前拦截）: {details}",
                extra={
                    "readiness": report.to_dict(),
                    "hint": (
                        "按 readiness.checks[*].fix_hint 逐项处理，例如 "
                        "get_market_data(...) 补齐数据后重试；行级问题用 "
                        "check_data(strategy_name=..., include_cleaning=True) "
                        "查看 C7 并 clean_data 清洗"
                    ),
                },
                tool="run_backtest",
                step="data_gate",
            )
        return {"report": report.to_dict()}


class EngineRunStep(BaseTool):
    """子步骤: 引擎执行 + 产物落盘（metrics/factor_failures/equity_curve/DB）。"""

    name = "engine_run"

    def execute(
        self,
        ctx: ToolContext,
        workspace,
        strategy_name: str,
        cfg: dict,
        yaml_path: str | None,
        action: str,
        description: str,
    ) -> dict:

        try:
            result = run_backtest_from_yaml(
                workspace_path=workspace,
                strategy_name=strategy_name,
                yaml_path=yaml_path,
                action=action,
                description=description,
                # v2: pass through study-scoped layout overrides
                strategy_dir=ctx.strategy_dir,
                results_tsv=ctx.results_tsv,
                runs_dir=ctx.runs_dir,
            )
        except Exception as exc:  # noqa: BLE001
            raise ToolError(
                f"backtest raised: {exc}",
                received=strategy_name,
                fix="check that strategies/<name>/config.yaml exists and is valid YAML",
                tool="run_backtest",
                step="engine_run",
            ) from exc

        if not result.get("success", False):
            err_msg = result.get("error", "unknown backtest failure")
            extra: dict = {
                "run": result.get("run", ""),
                "metrics": result.get("metrics", {}),
            }
            fix_msg = "check strategies/<name>/config.yaml and runs/<name>/logs for details"
            if (
                "数据为空" in err_msg
                or "empty" in err_msg.lower()
                or "cannot open database" in err_msg.lower()
            ):
                fix_msg = (
                    "data is empty. Workflow: 1) get_market_data("
                    "codes=['600519.SH'], start_date='2023-01-01', "
                    "end_date='2023-12-31', strategy_name='<name>') fetches "
                    "and persists OHLCV to DuckDB in one step; 2) "
                    "run_backtest(strategy_name='<name>') again"
                )
                extra["workflow"] = ["get_market_data", "run_backtest"]
            raise ToolError(
                err_msg,
                received=strategy_name,
                fix=fix_msg,
                tool="run_backtest",
                step="engine_run",
                extra=extra,
            )

        return {
            "run": result.get("run", ""),
            "status": result.get("status", "success"),
            "metrics": result.get("metrics", {}),
            "factor_failures": result.get("factor_failures", []),
            "warnings": result.get("metrics", {}).get("warnings", []),
        }
