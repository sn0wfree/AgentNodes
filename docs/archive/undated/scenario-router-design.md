# Scenario Router — 根据研究目标自动编排 Agent 子集（设计稿）

**状态**：框架已确认，待实施
**日期**：2026-08-27
**优先级**：先于 agent 精简与 adaptive_retry 完整版实施
**范围修订（同日）**：经反事实验证讨论后，将 adaptive_retry 中最薄的半片提前合并进本轮——
"max_iter 占位检测 → 当轮短路 finalize"（见 §4b），使最痛的"失败传染"问题在本轮即获缓解；
adaptive_retry 的其余部分（跨轮 exclude_agents / 重跑抑制）仍留下一轮。

---

## 1. 背景与动机

### 1.1 问题实锤（来自 study_f48295053041 复盘）

| 指标 | 数值 | 说明 |
|---|---|---|
| event 总数 | **95,693** | 其中 ~89% 为 text_delta token 流 |
| agent loop | Round 2 = 8 次，Round 3 = 11 次 | 全部 `Reached max_iterations=N without a final answer` |
| 研究推进 | best_metrics={}, last_keep_run_dir=null | 0 推进、0 keep |
| 最终结局 | watchdog stale 3642s → interrupted | 浪费 1+ 小时 LLM 算力 |

全量检查其余 9 个研究：`early_stopped:idle=3 rounds, best=0.0`（早停保护正常工作，未进入多 agent 循环，与本问题无关）。**study_f48295053041 是唯一完整走过多 agent 流程的案例，也是问题集中爆发的案例。**

### 1.2 用户核心诉求

1. **全自动，不是按钮**：不希望"AI 规划"变成 UI 上的一个按钮；用户输入研究目标后系统应自动完成意图理解与 agent 编排。
2. **两步走的总体方向**：先流程自适应（本轮），后精简 agent 集合（下轮）。
3. **本设计范围**：只做"入口处根据研究目标自动决定跑哪些 agent"（即 scenario_router）；中途失败反馈调整（adaptive_retry）是独立需求，另行设计。

---

## 2. 设计哲学（经过三轮迭代收敛）

### 2.1 迭代过程

| 版本 | 设计 | 问题 |
|---|---|---|
| v1 | LLM 从 5 个固定场景 tag 中分类 → tag 映射硬编码子集 | 分类死板，tag 选错整个子集错 |
| v2 | 删除场景层，LLM 完全自由选择 | 缺少锚定，冷启动质量不可控 |
| **v3（定稿）** | **场景降级为 prompt 中的 few-shot 示例（仅参考非限制），核心是自由编排 + 原则护栏** | — |

### 2.2 v3 三层 Prompt 结构

```
┌─ 层 1：原则（硬约束 —— 代码侧同时强制执行）────────────────┐
│ 1.【最小流水线】researcher / strategist / backtest /         │
│    risk_controller 四件套必选                                 │
│ 2.【按需增选】其余 agent 仅当目标需要时加入，逐个说明理由      │
│ 3.【节俭】满足目标前提下选最少                                │
│ 4.【不确定从紧】拿不准就不选（下轮可加）                      │
└───────────────────────────────────────────────────────────┘
┌─ 层 2：典型场景示例（few-shot —— 仅参考非限制）────────────┐
│ · 调参找最优        → 四件套 + anti_overfit_analyst           │
│ · 发现/验证新因子   → 四件套 + factor_analyst + attribution    │
│ · 多标的组合配置    → 四件套 + portfolio_construction          │
│ · 复盘/归因分析     → 四件套 + attribution_analyst             │
│ · 风险评估          → 四件套 + anti_overfit_analyst            │
│                                                              │
│ （prompt 明示："以上是常见编法，不是菜单——                   │
│   你的选择应基于目标本身，可以自由组合或新增"）               │
└───────────────────────────────────────────────────────────┘
┌─ 层 3：事实输入 ─────────────────────────────────────────┐
│ · 可用 agent 目录：8 个 agent 的 id / 类型 / 职责一句话       │
│ · objective（用户研究目标原文）                               │
│ · strategy_name / metric_targets                             │
└───────────────────────────────────────────────────────────┘
```

### 2.3 决策权分布

| 内容 | LLM 决定 | 代码强制 |
|---|---|---|
| 选哪些 agent | ✅ 自由 | 越界纠正（见 §4） |
| 四件套必选 | 原则引导 | ✅ 强制补齐 |
| 单标的排除 portfolio_construction | 原则引导 | ✅ 强制剔除 |
| 依赖闭包 | 不参与 | ✅ registry.complete_dependencies |
| max_agents 上限 | 不参与 | ✅ 截断 |
| reasoning 可解释性 | ✅ 输出 | 持久化留存 |

**关键点**：原则写给 LLM 看（引导倾向），代码做最终守门员（纠正越界）。LLM 选择 vs 最终执行的差异会被记录——这本身就是最直接的埋点。

---

## 3. 接口设计

### 3.1 输入

```python
@dataclass(frozen=True)
class RouteInput:
    objective: str                       # 用户输入的研究目标原文
    strategy_name: str | None            # 用于单/多标的推断
    workspace_path: Path | None          # 策略目录扫描（portfolio_construction 取舍）
    metric_targets: list[dict]           # [{"name": "calmar", "op": ">=", "value": 1.0}, ...]
```

### 3.2 输出

```python
@dataclass(frozen=True)
class RouteResult:
    selected_agents: list[str]           # 本轮要跑的 agent id（经代码校验修复后）
    llm_selected: list[str]              # LLM 原始选择（埋点对比用）
    repaired: bool                       # 是否发生过代码修复
    repair_notes: list[str]              # 修复动作明细（日志/埋点）
    reasoning: str                       # LLM 给出的人类可读理由
    confidence: float                    # LLM 自评 0-1
    source: str                          # "llm" | "keyword_fallback" | "default_graph"
```

### 3.3 LLM 输出 Schema

```json
{
  "selected_agents": ["researcher", "strategist", "backtest",
                       "risk_controller", "anti_overfit_analyst"],
  "reasoning": "目标是调参找最优组合……不需要 factor_analyst（无新因子假设）……",
  "confidence": 0.85
}
```

无 `scenario_tag` 字段、无 excluded 逐项列表。调用参数：`temperature=0`，
`response_format={"type": "json_object"}`。

### 3.4 LLM 接入方式

复用项目现有 LLM 栈（零新依赖）：

- 客户端：`core/llm/openai_client.py::OpenAICompatClient.chat()`（同步，自带 retry；
  `core/llm/openai_client.py:299`）
- 配置：`core/llm/config.py::LLMConfig`（provider 支持 auto/openai/deepseek/qwen/
  minimax/kimi/nvidia/custom）

---

## 4. 代码侧校验管线（原则执行者）

```python
def _validate_and_repair(llm_selected, inp) -> RouteResult:
    notes = []
    # 1. 过滤未知 agent id（LLM 幻觉防御）
    # 2. 强制补齐四件套（原则 1 不依赖 LLM 自觉）
    # 3. 依赖闭包补全（复用 AgentPluginRegistry.complete_dependencies）
    # 4. 单标的 → 强制剔除 portfolio_construction（覆盖 LLM 决定）
    #    推断方式（简化版）：扫描 strategy.py 是否含权重分配特征
    #    （weight / risk_parity / rebalance 关键字）→ multi；否则 single
    # 5. max_agents 上限截断（默认 8，即现有全集）
    # 6. selected 为空 / JSON 解析失败 → 回落 DEFAULT_STANDARD_GRAPH 全集
```

任何修复动作写入 `repair_notes` 并 log warning——差异率即埋点。

---

## 4b. 合并项：max_iter 占位检测 → 当轮短路（防失败传染）

反事实验证表明：仅做入口选择无法阻止 study_f48295053041 式的 0 推进——
即使只跑 5 个 agent，若 upstream 输出是 `"Reached max_iterations=N ..."`
占位文本，下游照旧被污染、轮末仍走完整 finalize。因此本轮合并一个最薄的
运行时防护：

```python
# scenario_router.py 内的纯函数（供 runner 调用，也可单测）
def detect_max_iter_placeholders(agent_outputs: dict) -> list[str]:
    """返回产出 max_iter 占位文本（而非结构化 JSON）的 agent id 列表。"""
```

接入点在 `runner._run_loop` 拿到 `_run_one_round` 结果之后、进入
review/finalize 之前（**不动 langgraph_engine / phase_engine**）：

- 检测到占位输出 → 记 warning + 发 SSE `study_phase(status="short_circuited")`
- 结果标记 `verdict="discard"` + `reason="upstream_failed: <agents>"`
- **跳过 `_run_review_cycle` 等下游 finalize 步骤**（省一轮无意义的 LLM 评审——
  对垃圾输出做评审本身就是 study_f48295053041 冗余的一部分）
- 探测函数同时导出，供下一轮 adaptive_retry 的跨轮 exclude 直接复用

规模 ~40 行 + 测试。这一步**治标不治本**：单个 agent 内部为何 max_iter
（prompt 结构性问题）不在本设计范围。

---

## 5. 兜底链

```
route()
 ├─ 尝试 LLM（1 次，带 retry 由 client 内部处理）
 │    ├─ 成功 → _validate_and_repair → RouteResult(source="llm")
 │    └─ 异常/超时/解析失败 ↓
 ├─ 关键词 fallback（~30 行，5 组同义词簇，命中即映射子集）
 │    └─ RouteResult(source="keyword_fallback")
 └─ 关键词也不命中 → DEFAULT_STANDARD_GRAPH 全 8 agent
      └─ RouteResult(source="default_graph")
```

**永不抛异常**：编排是研究的入口前置步骤，失败必须优雅降级而不是阻断启动。
本模块同步避开 tech-debt ⑤ 的教训（plan-dag 同步 LLM 阻塞事件循环）：
接入点的执行上下文若为 asyncio 事件循环，必须以 `asyncio.to_thread` 包裹 `route()`
（详见 §8 风险 R1）。

---

## 6. 接入点（单点，最小侵入)

```python
# core/study/bootstrap.py :: init_study_dir —— 在构造 graph 之前
if graph is None:
    result = await asyncio.to_thread(     # 防事件循环阻塞（R1）
        scenario_router.route,
        RouteInput(objective=objective, strategy_name=strategy_name,
                   workspace_path=ws, metric_targets=metric_targets or []),
    )
    if set(result.selected_agents) != set("全部"):
        graph = build_graph_from_selection(result.selected_agents)
    # default_graph 情形沿用现有 DEFAULT_STANDARD_GRAPH，不额外生成
```

拓扑生成不交 LLM：选中集合确定后，边的连接复用
`builtin_plugins.standard_pipeline_adjacency()` 中的已知邻接关系裁剪得到——
LLM 只决定**集合**，拓扑由确定性代码生成（可验证、可复现）。

**不改动的文件**：langgraph_engine.py / phase_engine.py / runner.py /
graph_templates.py / 前端全部。MINIMAL_GRAPH 与 EXPLORE_GRAPH 作为显式模板保留不动。

---

## 7. 文件清单与工作量

| 类别 | 文件 | 动作 | 规模 |
|---|---|---|---|
| 新建 | `src/agentnodes/core/study/scenario_router.py` | dataclass + PROMPT 常量 + route() + 校验管线 + 关键词 fallback + detect_max_iter_placeholders | ~240 行 |
| 修改 | `src/agentnodes/core/study/bootstrap.py` | init_study_dir 接入 route()（单点） | ~15 行 |
| 修改 | `src/agentnodes/core/study/runner.py` | _run_loop 中 max_iter 短路钩子（§4b） | ~35 行 |
| 新建 | `tests/test_scenario_router.py` | 5 场景锚定 + 越界修复 ×4 + 兜底 ×2 + JSON 解析失败 + 单/多标的剔除 + 占位检测 | ~190 行 |

**合计 ~475 行 / 预估 3 天 / 单 PR**

### 验证标准

1. ✅ `tests/test_scenario_router.py` 全过（含 LLM mock）
2. ✅ 现有后端测试零回归（567+ 通过基线）
3. ✅ 前端 vitest 850 通过不破坏；build/tsc 干净
4. ✅ 默认创建 study 时 graph.json 反映路由结果；单标的策略不含 portfolio_construction
5. ✅ LLM 不可用时启动不被阻断（source 正确降级为 fallback/default_graph）
6. ✅ 含 max_iter 占位输出的轮次：review/finalize 被跳过、verdict=discard、reason 含 upstream_failed

---

## 8. 风险与约束

| # | 风险 | 对策 |
|---|---|---|
| R1 | route() 在 asyncio 循环内同步调 LLM → 重演 plan-dag 阻塞（tech-debt ⑤） | 接入点一律 `asyncio.to_thread` 包裹 |
| R2 | LLM 幻觉输出未知 agent id / 空 JSON | 校验管线第 1/6 步 + 兜底链 |
| R3 | LLM 延迟拖慢 study 创建体感 | 一次调用封顶（client 内 retry 不叠加外层重试）；超时预算 ~30s 后落 keyword fallback |
| R4 | 单/多标的推断误判（扫描 strategy.py 关键词法是启发式） | 推断错误的影响面被限定为 portfolio_construction 一个 agent 的取舍；下轮 strategy_meta 完整版取代该启发式 |
| R5 | 团队结构变化（未来 agent 增减）导致目录漂移 | agent 目录由 registry 动态生成注入 prompt，非硬编码 |

---

## 9. 明确不做（边界）

- ❌ adaptive_retry 完整版（跨轮 exclude_agents / 重跑抑制）：下一轮设计；
  本轮仅合并其中最薄的"当轮短路"半片（§4b）
- ❌ 精简 agent 集合（data_quality→dq_check / factor_analyst 合并等）：独立轮次
- ❌ strategy_meta 完整版：本轮仅内置简化版单/多标的启发式（§4 第 4 步）
- ❌ overfit_eval / dq_check 等职责拆分工具
- ❌ UI 改动（无按钮、无提示条——全自动对用户透明）
- ❌ langgraph_engine / phase_engine / runner / graph_templates 改动
- ❌ stagnation 检测（runner 现有 idle=N 早停机制继续负责，避免双定义）
- ❌ event_log 重放类集成测试（过度工程）

---

## 10. 后续路线（本设计落地之后）

| 轮次 | 内容 | 依赖 |
|---|---|---|
| 下轮 | adaptive_retry：识别 max_iter 占位输出 → exclude_agents（中间反馈自适应） | 本设计的 RouteResult 结构 |
| 再下轮 | agent 集合精简：data_quality→dq_check.py、anti_overfit 数值扫描拆 overfit_eval.py、factor_analyst 职责契约调整 | adaptive_retry 先行（避免精简后失败面仍传染） |
| 按需 | strategy_meta 完整版：吸收本设计中的单/多标的启发式 + frequency/objective_category 推断，作为自适应基础设施统一收口 | 前两轮稳定 |

### 决策指标（上线 1-2 周观察）

| 指标 | 含义 |
|---|---|
| `source` 分布（llm / keyword_fallback / default_graph） | LLM 主路径健康度 |
| repaired=True 比例 + 高频 repair_notes 模式 | 原则被违反的方式 → 调 prompt |
| LLM selected vs final 差异率 | prompt 质量 |
| 路由前后 round 的 agent-loop 平均耗时 / max_iter 失败率 | 实际收益证据 |

## 11. 关联文档

- `docs/archive/2026-08/study-subsystem-tech-debt-20260827.md` — ⑤ plan-dag 同步阻塞（R1 来源）
- `docs/archive/2026-08/study-subsystem-tech-debt-20260827.md` — legacy 能力盘点（DAGPlanner 已有 LLM 规划能力，本设计与之关系：plan-dag 是人工触发的预览工具，本设计是启动路径的默认行为；二者共用校验思路但不共享代码路径）
- `/tmp/opencode/research-report/auto-orchestration-research.md` — 业界调研（Magentic-One 双 Ledger / Deep Agents interrupt_on / Auto with Approval 哲学），供后续 adaptive_retry 与 orchestrator 轮次参考
