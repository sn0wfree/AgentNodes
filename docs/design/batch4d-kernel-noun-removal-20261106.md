# 批次 4d 设计 · kernel 业务名词清零

> **地位**：批次 4d 的**设计文档（doc first）**。本文定方案，代码随后。
> 三步走第一步（通用平台 + 插件）的最后一层——前三个批次清掉了
> **配置层**（4a）与**逻辑 bug**（4b）与**工具引用**（4c），
> 本批清 **kernel 源码里的业务概念**。
>
> 上位依据：`three-phase-roadmap-20261106.md` §3.2 A2
> 「`kernel/` 不含任何业务域名词」。

---

## §0 结论摘要

**八处硬编码，分三类处置**：

| 类 | 处数 | 处置 | 风险 |
|---|---|---|---|
| **A 可直接删** | 3 | 删除业务枚举成员 / 死代码 / 注释 | 低 |
| **B 改为数据驱动** | 3 | 路由表与示例从 kernel 移到 L2 注册 | 中 |
| **C 保留但重写语义** | 2 | 结构保留、内容改通用 | 低 |

**一个关键实测结论**（决定 A 类是否安全）：

> `Permission` 枚举**不是白名单**，只是 `DEFAULT_RULES` 的构造常量。
> `PermissionEvaluator.evaluate()` 走「last-match-wins 字符串 glob 匹配」，
> 未匹配时 fallback `ASK`。因此**删除业务枚举成员不影响任何已有
> `permissions.yaml`**——那些文件里写的是字符串，不查枚举。

---

## §1 八处清单（实测，带 file:line）

| # | 位置 | 内容 | 类 |
|---|---|---|---|
| 1 | `permission/schema.py:47-50` | `RUN_BACKTEST` / `GET_MARKET_DATA` / `IMPORT_DATA` / `COMPUTE_FACTOR` 四个枚举成员 | A |
| 2 | `permission/evaluator.py:94-109` | 四条对应的 `DEFAULT_RULES` | A |
| 3 | `permission/evaluator.py:246-247` | `_extract_target` 里 `run_backtest` 取 `strategy_name` 的分支 | A |
| 4 | `workflow/validator.py:43-55` | `_get_validator` 的 9 个业务 agent → 校验函数路由 | B |
| 5 | `workflow/validator.py:57-160+` | 9 个 `_validate_*` 方法（researcher / factor_analyst / strategist / risk_controller / anti_overfit / data_quality / portfolio / attribution / backtest_diagnostics）| B |
| 6 | `workflow/node_types.py:375-388` | `_fallback_plan` 完整硬编码量化五步流水线 | B |
| 7 | `agent/prompt_builder.py:348-364` | 13 个业务角色 → `StaticFilePromptBuilder` | C |
| 8 | `agent/role_factory.py:30-48` | `_ROLE_PROMPT_FILES` 14 个业务角色映射 | C |

**外加两处已在 4a 处理但需在此确认**：
- `agent/compact.py:208-215` 的 `_LEGACY_L1_TOOL_LIMITS_DOC` 含 `backtest_run` —— **已标注 DEPRECATED 且零消费**，只需补一句「业务示例」注释或直接删。
- `agent/exec_registry.py:76` / `kernel/swarm/runtime.py:867` 的 docstring 引用已删 kind —— 纯注释。

---

## §2 类 A · 可直接删（permission 三处）

### 2.1 为什么安全

```python
# schema.py —— 枚举只是「默认规则的构造常量」
class Permission(str, Enum):
    READ_FILE = "read"
    ...
    RUN_BACKTEST = "run_backtest"      # ← 删
    GET_MARKET_DATA = "get_market_data" # ← 删
    IMPORT_DATA = "import_data"         # ← 删
    COMPUTE_FACTOR = "compute_factor"   # ← 删
```

```python
# evaluator.py:190 —— 判定只看字符串 glob，不查枚举
matched = self._matching_rules(tool_name, target)
if matched:
    return PermissionDecision(action=matched[-1].action, ...)
# 未匹配 → fallback
return PermissionDecision(action=PermissionAction.ASK)
```

**验证过的行为**：
- `permissions.yaml` 里用户写 `permission: run_backtest` 仍能匹配（字符串比较）
- 删掉枚举成员后，该工具走**兜底 ASK**，与删除前的默认规则 `ASK` **行为一致**
- 唯一差别：`DEFAULT_RULES` 里少四条 `pattern="*"` 的显式 ASK 规则——
  但兜底也是 ASK，等价

### 2.2 但要保留一条

`_extract_target` 的 `run_backtest` 分支如果直接删，`run_backtest` 的 target
会退化成工具名本身。这对 glob 匹配的影响：用户的 `permission: run_backtest,
pattern: "my_strategy_*"` 这类规则会失效。

**处置**：不改 kernel，改为**在 L2 插件域提供 target 提取器**（见 §3.3）。
但这属于 YAGNI——当前没有这种规则在用。**决定：直接删，记入迁移说明。**
如果将来插件需要，由插件自带 extractor。

---

## §3 类 B · 改为数据驱动（三处）

### 3.1 `workflow/validator.py` 的 9 个业务校验器

**现状**：kernel 里硬编码「agent 名 → 校验函数」路由表 + 9 个各自解析
`ic_mean` / `ir_mean` / `changes` 等量化字段的方法。

**问题**：这套逻辑只对量化 agent 有意义。别的业务域的 agent
（法律条款审查、医学病历核对）在这张表里只会拿到 `None`（无校验）。

**方案**：抽出 **`OutputValidatorRegistry`**（kernel，通用机制）：

```python
# kernel/workflow/validator.py（改造后）
class OutputValidatorRegistry:
    """agent 名 → 输出校验器的通用注册表。

    kernel 只提供**机制**：注册、查找、聚合错误/警告。
    具体校验规则是业务内容，由 L2 注册。
    """
    def __init__(self) -> None:
        self._validators: dict[str, Callable[[dict, ValidationResult], None]] = {}

    def register(self, agent_name: str, fn: Callable) -> None: ...
    def get(self, agent_name: str) -> Callable | None: ...
```

然后 9 个业务校验函数整体搬到 `apps/agent_plugins/quant.py`（或
独立 `apps/agent_plugins/quant_validators.py`），在装配期注册。

**装配方式**：与 `CapabilityRegistry` 同构——kernel 声明机制，
L2 在 import 时填充。但为避免 L3→L2 上行，装配根（L1）负责调用
`apps.agent_plugins.populate_validators(registry)`。

> **注意**：这条会新增一条 A2 的检查项——kernel 的
> `ValidatorRegistry` 默认必须**为空**，与插件注册表同口径。

### 3.2 `workflow/node_types.py` 的 `_fallback_plan`

**现状**：`_fallback_plan()` 返回硬编码的五步量化流水线
（提出假设 → 准备数据 → 回测验证 → 稳健性检查 → 结论报告），
工具引用 `factor_analysis` / `get_market_data` / `run_backtest` /
`drawdown_analysis` —— **四个全部不存在**。

**方案**：改为**通用 fallback 模板**：

```python
def _fallback_plan(max_steps: int) -> list[dict[str, Any]]:
    """无 planner 时的通用兜底计划：调研 → 执行 → 验证 → 汇报。

    **不含任何业务工具**：只引用通用能力（read / write / shell / websearch）。
    业务域需要自己的流水线时，应提供 planner，而不是依赖这个兜底。
    """
```

四步通用骨架：
1. 澄清目标（read / websearch）
2. 收集信息（websearch / webfetch / read）
3. 执行任务（write / shell）
4. 汇总汇报（show_report）

**风险**：现有测试可能断言五步的具体标题/工具。需逐一确认并改写
（按 4c 的经验，这是预期内的破坏）。

### 3.3 `workflow/validator.py` 的 `_validate_*` 九个方法

随 §3.1 一起搬走。**不在 kernel 保留任何量化字段名校验**
（`ic_mean` / `ir_mean` / `factor_direction` / `anti_overfit` 等）。

---

## §4 类 C · 保留结构、重写语义（两处）

### 4.1 `prompt_builder.py` 的角色路由

**现状**：13 个业务角色硬编码在 `PromptBuilderFactory` 的构造里。

**关键**：`prompt_builder.py:341-347` 有一条已有的注释反驳"这些是业务角色"：

> 「role_factory 的 14 个角色是**框架多角色编排能力**——researcher→strategist→critic
> 等构成通用的研究/评审角色链，与量化业务无关，因此提示词仍随包分发。」

**这条注释对不对？** 部分对：
- `researcher` / `critic` / `planner` / `evaluator` / `study_reviewer` / `study_collector` —— **通用角色**（研究 + 评审 + 规划 + 收集）
- `data_quality` / `factor_analyst` / `strategist` / `portfolio_construction` / `risk_controller` / `attribution_analyst` / `anti_overfit_analyst` / `backtest_diagnostics` —— **量化专属**

**方案**：分两档
- **通用角色留 kernel**（8 个）：`chat` / `critic` / `planner` / `evaluator` / `researcher` / `study_reviewer` / `study_collector` / `workflow_orchestrator`
- **业务角色迁 L2**（6 个）：`data_quality` / `factor_analyst` / `strategist` / `portfolio_construction` / `risk_controller` / `attribution_analyst` / `anti_overfit_analyst` / `backtest_diagnostics`

**机制**：kernel 的 `PromptBuilderFactory` 支持外部注册：

```python
def register_prompt_builder(role: str, builder: PromptBuilder) -> None:
    """L2 业务角色注册自己的提示词构造器（装配期调用）。"""
```

### 4.2 `role_factory.py` 的 `_ROLE_PROMPT_FILES`

与 §4.1 同步：只留通用角色的映射，业务角色的映射迁 L2。

---

## §5 验收标准

| # | 标准 | 验证方式 |
|---|---|---|
| V1 | kernel 源码零业务名词 | 新增 `check_kernel_nouns.py`（或扩充 `check_semantics_placement`） |
| V2 | `ValidatorRegistry` / `PromptBuilderFactory` 默认空 | 与 `AgentPluginRegistry` 同口径的断言 |
| V3 | 全量测试绿 | pytest |
| V4 | batch_gate 7 步 ALL GREEN | `scripts\batch_gate.cmd` |
| V5 | 删掉 `apps/` 后 kernel 仍可导入并构建通用 agent | 新增 smoke 测试 |

**V1 的词表**（初版，可扩充）：
```
factor, backtest, strategy, portfolio, risk_controller, attribution,
overfit, ic_mean, ir_mean, drawdown, sharpe, market_data, ohlcv,
signal, alpha, sector, regime, position, sharpe
```

**例外（允许出现在 kernel）**：
- 文档字符串里的**历史说明**（"这批工具曾住在 kernel"）
- `PENDING_TOOLS` 之类的**数据清单**（在 L2）
- `exec_registry.py` 里「已删除」的注释

> **这是本批最容易做成假门禁的地方**：词表太严会让注释也报错，
> 太松会让真硬编码漏过。**处置原则：只扫代码（字符串字面量 + 标识符），
> 不扫注释与 docstring。**

---

## §6 执行顺序

| 序 | 动作 | 文件 | 风险 |
|---|---|---|---|
| 1 | 删 permission 三处（枚举 + 默认规则 + extract_target 分支）| `permission/` | 低 |
| 2 | `_fallback_plan` 改通用四步 | `workflow/node_types.py` | 中 |
| 3 | `ValidatorRegistry` 抽取 + 9 个业务校验器迁 L2 | `workflow/validator.py` + `apps/agent_plugins/` | 中 |
| 4 | `PromptBuilderFactory` 加注册口 + 8 个业务角色迁 L2 | `agent/prompt_builder.py` + `apps/agent_plugins/` | 中 |
| 5 | `_ROLE_PROMPT_FILES` 收缩 | `agent/role_factory.py` | 低 |
| 6 | 删 `compact.py` 的 `_LEGACY_L1_TOOL_LIMITS_DOC` | `agent/compact.py` | 低 |
| 7 | 新增 `check_kernel_nouns.py` + 纳入 batch_gate 第 7 步 | `scripts/arch/` | 中 |
| 8 | 全量验证 + 架构图回写 | — | — |

**批内可并行**：1 + 6（独立）；2（独立）；3 + 4 + 5（同一批角色迁移，需串行）；
7 依赖 1–6 全部完成。

---

## §7 风险与已知代价

| # | 风险 | 量级 | 缓解 |
|---|---|---|---|
| 1 | **permission 兼容性**：用户 `permissions.yaml` 里的业务工具名 | 低（已实测：走兜底 ASK，行为一致）| 迁移说明写清 |
| 2 | **`_fallback_plan` 改通用后，量化工作流失去兜底** | 中 | 量化域应提供自己的 planner；`apps/agent_plugins/` 里保留一份 `_quant_fallback_plan` |
| 3 | **9 个校验器迁走，量化 agent 输出不再被校验** | 中 | 同上：随插件注册。未注册时 `get()` 返回 None = 不校验（现行为）|
| 4 | **6 个业务提示词迁走，角色不再可解析** | 中 | 与插件可用性联动：工具不齐 → 插件不可用 → 提示词也不需要 |
| 5 | **词表门禁误报**（注释/文档里的历史说明）| 中 | 只扫代码不扫注释（§5 已定）|
| 6 | **测试破坏面可能很大**（4c 时 14 个）| 中 | 按 4c 的经验逐条按新契约改写，不"改断言让它继续绿" |

---

## §8 明确不做

| 项 | 原因 |
|---|---|
| 不删 `templates/.skills/` 的 26 个量化技能 | 资产层，与 kernel 无关；第三步随工具包整理 |
| 不删 `evals/golden_tasks.jsonl` | 属 RSI 内容（第二步 R3 迁 L2）|
| 不改 `apps/goal/` | goal 是 L2 内容；其 workflow_config 由 R3 处理 |
| 不引入 `check_kernel_nouns.py` 之外的架构改动 | 本批只做名词清零 |
