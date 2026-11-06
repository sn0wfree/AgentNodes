"""量化研究插件集（L2 · 业务内容）。

**归属（2026-11-06 批次 4 · 三步走第一步）**：原住 ``kernel/agent/builtin_plugins.py``
（L3），但插件定义是**业务内容**——研究员/因子分析师/策略师这些角色属于
特定领域，不是平台原语。按「平台 + 插件」定位迁到 L2。

**第三阶段状态**：这些插件引用的工具（``factor_analysis`` / ``run_backtest`` /
``get_market_data`` / ``check_data`` / ``compute_factor`` / ``search_symbol`` /
``list_data_sources`` / ``clean_data`` / ``strategy_compare`` /
``drawdown_analysis`` / ``benchmark_comparison`` / ``list_goals``）的实现
**已随批次 4/5 直删且未恢复**（3,091 行 / 21 个工具可从
``9e2432de^`` 恢复，见 ``docs/design/progress-and-plan-20261106.md`` §7.0）。

因此本模块的插件**默认不加载**：``available_plugins()`` 只返回工具齐备的插件，
当前环境下一个都不齐 → 返回空元组。第三阶段恢复工具包后，同一份定义自动生效。

**为什么不删除这些定义**：它们是第三步「核心工具包 + 知识库 = 特质 agent」的
现成素材（角色提示词、依赖拓扑、工具清单都已写好）。删掉等于把第三步的设计
文档一并扔掉。保留 + 显式标注缺什么，比删掉更有用。
"""
from __future__ import annotations

from agentnodes.kernel.agent.plugin import AgentPlugin

#: 本插件集引用、但当前环境**没有实现**的工具。
#: 与 ``tools.py`` 里的定义逐项对应，用于两件事：
#:   1. ``available_plugins()`` 判定哪些插件可加载
#:   2. ``scripts/arch/check_tool_refs.py`` 的豁免依据（必须是**显式清单**，
#:      不允许通配豁免——否则这道门禁就是假的）
#:
#: 恢复路径：``git show "9e2432de^:src/agentnodes/apps/builtin_tools/XXX.py"``
PENDING_TOOLS: frozenset[str] = frozenset({
    "benchmark_comparison",
    "check_data",
    "clean_data",
    "compute_factor",
    "drawdown_analysis",
    "factor_analysis",
    "factor_cross_sectional_analysis",
    "factor_ic_decay",
    "factor_quintile_returns",
    "factor_turnover",
    "get_market_data",
    "import_data",
    "list_data_sources",
    "list_goals",
    "pattern_recognition",
    "search_symbol",
    "strategy_compare",
})

_RESEARCHER_TOOLS = (
    "read", "list_history", "factor_analysis", "websearch", "webfetch",
    "get_market_data", "search_symbol", "show_chart",
)

BUILTIN_PLUGINS: tuple[AgentPlugin, ...] = (
    AgentPlugin(
        id="researcher", name="Researcher", category="research",
        description="量化策略研究员，基于历史结果提出研究假设与方向",
        prompt_file=".prompts/researcher.md",
        tools=_RESEARCHER_TOOLS,
        requires=(), provides="researcher_output",
        default_timeout=180, default_max_iterations=20, default_max_retries=3,
        optional=False, keywords=("研究", "假设", "方向", "hypothesis"),
    ),
    AgentPlugin(
        id="data_quality", name="Data Quality", category="evaluation",
        description="数据质量检查专家，验证数据完整性、NaN、除权等",
        prompt_file=".prompts/data_quality.md",
        tools=("read", "websearch", "webfetch", "get_market_data",
               "list_data_sources", "check_data", "clean_data", "run_bg_command"),
        requires=("researcher",), provides="data_quality_output",
        default_timeout=120, default_max_iterations=12, default_max_retries=2,
        keywords=("数据", "质量", "清洗", "NaN", "data quality"),
    ),
    AgentPlugin(
        id="factor_analyst", name="Factor Analyst", category="execution",
        description="因子分析专家，本地算子挖掘与 Alpha Zoo 因子验证",
        prompt_file=".prompts/factor_analyst.md",
        tools=("read", "compute_factor", "factor_analysis",
               "get_market_data", "run_bg_command"),
        requires=("researcher",), provides="factor_analyst_output",
        default_timeout=180, default_max_iterations=15, default_max_retries=3,
        keywords=("因子", "IC", "alpha", "选股", "factor"),
    ),
    AgentPlugin(
        id="explore", name="Explore", category="research",
        description="探索性研究 agent，并行尝试新颖研究方向并沉淀知识",
        prompt_file=".prompts/researcher.md",
        tools=_RESEARCHER_TOOLS,
        requires=("researcher",), provides="explore_output",
        default_timeout=180, default_max_iterations=20, default_max_retries=2,
        keywords=("探索", "新颖", "尝试", "explore"),
    ),
    AgentPlugin(
        id="strategist", name="Strategist", category="execution",
        description="策略集成专家，将因子整合为策略并修改 strategy.py",
        prompt_file=".prompts/strategist.md",
        tools=("read", "write", "run_backtest", "git_diff", "websearch",
               "webfetch", "get_market_data", "show_chart", "show_report",
               "run_bg_command"),
        requires=("researcher",), provides="strategist_output",
        default_timeout=240, default_max_iterations=25, default_max_retries=3,
        optional=False, keywords=("策略", "集成", "信号", "strategy"),
    ),
    AgentPlugin(
        id="portfolio_construction", name="Portfolio Construction",
        category="execution",
        description="组合构建专家，风险平价 / 逆波动率 / 最大分散化配权",
        prompt_file=".prompts/portfolio_construction.md",
        tools=("read", "get_market_data"),
        requires=("strategist",), provides="portfolio_construction_output",
        default_timeout=120, default_max_iterations=12, default_max_retries=2,
        keywords=("组合", "权重", "配置", "portfolio"),
    ),
    AgentPlugin(
        id="backtest", name="Backtest", category="tool",
        description="回测执行（python executor：run_backtest_script）",
        prompt_file="",
        tools=(),
        requires=("strategist",), provides="backtest_result",
        executor_type="python", python_function="run_backtest_script",
        default_timeout=300, default_max_iterations=1, default_max_retries=1,
        optional=False, keywords=("回测", "backtest"),
    ),
    AgentPlugin(
        id="risk_controller", name="Risk Controller", category="evaluation",
        description="风险控制官，阈值检查、VaR/CVaR、压力测试",
        prompt_file=".prompts/risk_controller.md",
        tools=("read", "factor_analysis", "get_market_data"),
        requires=("backtest",), provides="risk_controller_output",
        default_timeout=180, default_max_iterations=12, default_max_retries=2,
        optional=False, keywords=("风险", "回撤", "VaR", "risk"),
    ),
    AgentPlugin(
        id="attribution_analyst", name="Attribution Analyst",
        category="evaluation",
        description="归因分析专家，Brinson 归因与 Fama-French 分解",
        prompt_file=".prompts/attribution_analyst.md",
        tools=("read", "factor_analysis"),
        requires=("risk_controller",), provides="attribution_analyst_output",
        default_timeout=180, default_max_iterations=12, default_max_retries=2,
        keywords=("归因", "分解", "因子暴露", "attribution"),
    ),
    AgentPlugin(
        id="anti_overfit_analyst", name="Anti-Overfit Analyst",
        category="evaluation",
        description="过拟合检测专家，6 项鲁棒性测试与保留/放弃判定",
        prompt_file=".prompts/anti_overfit_analyst.md",
        tools=("read", "list_history", "factor_analysis"),
        requires=("risk_controller",), provides="anti_overfit_analyst_output",
        default_timeout=180, default_max_iterations=12, default_max_retries=2,
        keywords=("过拟合", "鲁棒", "验证", "overfit"),
    ),
    AgentPlugin(
        id="backtest_diagnostics", name="Backtest Diagnostics",
        category="evaluation",
        description="回测诊断专家，错误分类与修复建议",
        prompt_file=".prompts/backtest_diagnostics.md",
        tools=("read", "run_backtest", "git_diff", "show_chart",
               "show_report", "run_bg_command"),
        requires=("backtest",), provides="backtest_diagnostics_output",
        default_timeout=120, default_max_iterations=12, default_max_retries=2,
        keywords=("诊断", "错误", "调试", "diagnostics"),
    ),
    AgentPlugin(
        id="decide", name="Decide", category="tool",
        description="验收决策（evaluator：decide keep/discard）",
        prompt_file="",
        tools=(),
        requires=("backtest",), provides="decision",
        executor_type="evaluator", python_function="decide",
        default_timeout=60, default_max_iterations=1, default_max_retries=1,
        keywords=("决策", "验收", "keep", "discard"),
    ),
    AgentPlugin(
        id="critic", name="Critic", category="evaluation",
        description="方案评审 agent，对研究计划提出批评与改进建议",
        prompt_file=".prompts/critic.md",
        tools=("read", "list_history"),
        requires=("researcher",), provides="critic_output",
        default_timeout=120, default_max_iterations=8, default_max_retries=2,
        keywords=("评审", "批评", "critic"),
    ),
    AgentPlugin(
        id="planner", name="Planner", category="research",
        description="研究计划生成器，目标 → 3-8 步研究子图",
        prompt_file=".prompts/planner.md",
        tools=("read", "websearch", "webfetch", "list_goals",
               "get_market_data"),
        requires=(), provides="plan_output",
        default_timeout=180, default_max_iterations=8, default_max_retries=3,
        keywords=("计划", "plan"),
    ),
    AgentPlugin(
        id="evaluator", name="Evaluator", category="evaluation",
        description="进度评估器，continue / replan / stop 决策",
        prompt_file=".prompts/evaluator.md",
        tools=("read", "list_history", "factor_analysis"),
        requires=("researcher",), provides="evaluator_output",
        default_timeout=120, default_max_iterations=8, default_max_retries=2,
        keywords=("评估", "evaluate"),
    ),
    AgentPlugin(
        id="study_reviewer", name="Study Reviewer", category="evaluation",
        description="轮间评审 agent，偏差检测 / 信息缺口 / 待办更新",
        prompt_file=".prompts/study_reviewer.md",
        tools=("read",),
        requires=("researcher",), provides="review_output",
        default_timeout=120, default_max_iterations=8, default_max_retries=2,
        keywords=("评审", "review"),
    ),
    AgentPlugin(
        id="study_collector", name="Study Collector", category="research",
        description="知识收集 agent，检索并沉淀研究知识到 knowledge.md",
        prompt_file=".prompts/study_collector.md",
        tools=("read", "websearch", "webfetch", "list_history"),
        requires=(), provides="knowledge_output",
        default_timeout=120, default_max_iterations=8, default_max_retries=2,
        keywords=("知识", "收集", "knowledge"),
    ),
)

BUILTIN_PLUGIN_IDS: tuple[str, ...] = tuple(p.id for p in BUILTIN_PLUGINS)


def available_plugins(
    installed_tools: frozenset[str] | set[str] | None = None,
) -> tuple[AgentPlugin, ...]:
    """只返回工具齐备的插件（当前环境下）。

    :param installed_tools: 已注册工具名集合；None = 用
        ``build_default_registry()`` 现算一次

    **为什么要有这个函数**：插件的 ``tools`` 是声明，不是事实。声明了
    ``factor_analysis`` 但实现已删，装配出来就是一个调用必败的壳——
    这正是 W1 要根治的「幽灵工具」病。本函数把同一个判据用在插件层：
    **引用了不存在的工具 → 该插件不可用**，而不是让它带着空工具集跑。

    当前环境下 ``PENDING_TOOLS`` 里的工具全部缺失 → 所有插件都不可用
    → 返回空元组。第三阶段工具包恢复后自动生效，无需改这里。

    **三层校验，缺一不可**（2026-11-06 修 bug）：

    1. ``tools``：每个工具名都必须在 registry 里
    2. ``python_function``：非 LLM 插件声明的执行器/评估器必须已注册
    3. ``prompt_file``：LLM 插件声明的提示词文件必须存在

    只查第 1 层会漏掉最危险的情况——``tools=()`` 的空工具集插件
    （``backtest`` / ``decide``）看起来"没有悬空引用"，实则执行器
    （``run_backtest_script`` / ``decide``）根本不存在。**空集合不是通过，
    是没有可检查项——那恰恰是最该拦的。**
    """
    if installed_tools is None:
        from agentnodes.kernel.agent.builtin_tools import (
            build_default_registry,
        )

        installed_tools = {
            t.name for t in build_default_registry().all_tools()
        }
    have = set(installed_tools)
    return tuple(p for p in BUILTIN_PLUGINS if _is_plugin_usable(p, have))


def _is_plugin_usable(plugin: AgentPlugin, have: set[str]) -> bool:
    """三层校验：tools + python_function + prompt_file。"""
    # ① 工具
    if any(t not in have for t in plugin.tools):
        return False

    # ② 执行器 / 评估器（非 LLM 插件）
    fn = plugin.python_function
    if fn:
        from agentnodes.kernel.agent.exec_registry import (
            get_evaluator,
            get_python_executor,
        )

        lookup = (
            get_evaluator if plugin.executor_type == "evaluator"
            else get_python_executor
        )
        try:
            if lookup(fn) is None:
                return False
        except Exception:  # noqa: BLE001 —— 查不到即不可用
            return False

    # ③ 提示词文件（LLM 插件）
    if plugin.executor_type == "llm" and plugin.prompt_file:
        from pathlib import Path

        prompts_dir = (
            Path(__file__).resolve().parents[2] / "templates" / ".prompts"
        )
        if not (prompts_dir / plugin.prompt_file.split("/")[-1]).exists():
            return False

    return True


def standard_pipeline_plugin_ids() -> tuple[str, ...]:
    """Plugin ids matching the DEFAULT_STANDARD_GRAPH + backtest/decide."""
    return (
        "researcher", "data_quality", "factor_analyst", "strategist",
        "portfolio_construction", "backtest", "risk_controller",
        "attribution_analyst", "anti_overfit_analyst", "backtest_diagnostics",
        "decide",
    )


def standard_pipeline_adjacency() -> dict[str, list[str]]:
    """Upstream adjacency for the standard pipeline (matches the 9-agent
    autoresearch YAML preset)."""
    return {
        "researcher": [],
        "data_quality": ["researcher"],
        "factor_analyst": ["researcher", "data_quality"],
        "strategist": ["researcher", "data_quality", "factor_analyst"],
        "portfolio_construction": ["strategist"],
        "backtest": ["portfolio_construction"],
        "risk_controller": ["backtest"],
        "attribution_analyst": ["backtest", "risk_controller"],
        "anti_overfit_analyst": ["backtest", "risk_controller",
                                 "attribution_analyst"],
        "backtest_diagnostics": ["anti_overfit_analyst"],
        "decide": ["backtest", "anti_overfit_analyst",
                   "backtest_diagnostics"],
    }


__all__ = [
    "BUILTIN_PLUGINS",
    "BUILTIN_PLUGIN_IDS",
    "standard_pipeline_plugin_ids",
    "standard_pipeline_adjacency",
]
