# crewAI 可借鉴项 — 取舍判断与落位设计

> **性质**：**判断层（取舍 + 落位）**。不含事实重述——事实见
> `crewai-survey-20261116.md`（§2 事实核查、§3 官方机制、§4 边界对照）。
> **上位调研**：`crewai-survey-20261116.md`。
> **同批参考**：`system-one-survey-20261109.md` §3.1.1（「代码里存在」≠
> 「运行时在用」的触达面判据）、`system-one-decision-layer-design-20261109.md`
> （本仓「能力已建但无消费者」的同型缺口）。
> **约束来源**：`.rules/rules.md` R-001（四层单向依赖）、
> `AGENTS.md` 原则 2/3（简洁优先、精准修改）。
> 核验时间：**2026-11-16**，结论均对 HEAD 实测。

---

## §0 一句话结论

**只借鉴一件事：节点级产物契约（B1）。** 其余五项要么是扩展现有机制
（A 类，低优先），要么与本仓核心价值冲突（C 类，明确不做）。

理由不是「crewAI 这个功能好」，而是：**本仓已经有六处「有能力、有产出、
但没有消费者」的接线缺口**，而补一个节点契约能让它们同时有落点。
这与 `system-one-survey` §3.1.1 得出的是同一个结论。

---

## §1 判断方法：为什么不按「功能好坏」排序

### 1.1 三条筛选轴

| 轴 | 判据 | 反例（不该借鉴的典型） |
|---|---|---|
| **落位是否存在** | 本仓有没有挂载点？没有就是新建子系统 | 借「task guardrails」但没有 task 层 → 无处挂 |
| **是否与核心价值冲突** | 会不会削弱「机器可证 / 事件可回放 / 可复现」 | manager agent 自主分派 → 执行路径不可预测 |
| **触达面是否真实** | 按 `system-one-survey` §3.1.1 的做法：代码存在 ≠ 运行时在用 | 借「给 critic 加校验」但 37 preset 零编排 |

### 1.2 本仓已有的同型缺口（这是本方案的真正动机）

沿用 `system-one-survey` §3.1.1 的核实方法，本次又确认两处**同型**问题：

| # | 能力 | 有实现 | 无消费者/无接线 |
|---|---|---|---|
| 1 | `SwarmPreset.branches`（skip/retry） | ✅ `types.py:114` + `runtime.py:711` `_evaluate_branches_after_layer` 活的 | ❌ **37 个 preset YAML 零使用**（只有 6 个 `goal_*` 用 `goal:` / `completion:`） |
| 2 | skill 按需加载 | ✅ `workspace_tools.py:281` `load_skill`（注释：「避免大全文直接进 prompt」） | ❌ 但 `prompt_builder.py:493` / `harness_resolver.py:144` 的 `_load_skills` **直接注入全文**，两条路径并存 |
| 3 | guardrail 检测 | ✅ `guardrails.py:20` 正则表 + `loop.py:1153` 发事件 + `on_guardrail_finding` hook | ❌ 文件头注释明写 **observation-only**、「never alters the message path」——**无任何环节能据此阻断或重做** |
| 4 | 节点产物契约 | — | ❌ `workflow/` 与 `swarm/` 中 `expected_output` / `output_schema` / `guardrail` **全部零命中** |
| 5 | critic / study_reviewer | ✅ prompt 存在 | ❌ 零编排、零消费者（`system-one-survey` 已记） |
| 6 | `risk_controller` | ✅ 13/37 preset 使用 | ❌ 校验器校验另一个字段（`quant_validators.py:64`），恒 no-op |

**关键观察**：缺口 1–3 都是「能力建好了，但没接线」；缺口 4 是**接线点本身不存在**。
所以先补 4，1–3 才有地方落地。

---

## §2 采纳清单（A 类：直接可借鉴）

> 这三条都是**扩展现有机制**，不新建子系统。但按 §1 的筛选轴，
> 它们全部**依赖 B1 先落地**——否则改完仍然没有挂载点。故列为 P2/P3。

### A1 guardrail 从「只观察」升级到「可拦截 + 回传修正」

| 项 | 内容 |
|---|---|
| crewAI 机制 | `guardrails=[fn]` 返回 `(False, error)` → **error 回传给 agent，它自己改完重试**，`guardrail_max_retries` 默认 3；多个 guardrail 顺序串联，后者接收前者输出 |
| 本仓现状 | `guardrails.py:5` 明确 observation-only；`loop.py:1153` 仅 `_emit`；hook `on_guardrail_finding` 只读 |
| 差距 | **有检测、有事件、有 hook，但没有执行权**。且失败语义只有「记录」，没有「回传」 |
| 借鉴点 | 只有一条值得抄：**把校验失败变成下一轮的输入，而不是判死刑**。这是「校验器从裁判变教练」 |
| 落点 | `GuardrailFinding` 加 `severity`；`scan_*()` 加 `enforce: bool = False` 参数（默认 False = 现行为零变化）；`enforce=True` 时把 finding 作为修正提示注入下一轮 tool_result |
| 不可借鉴 | 不建议照搬「guardrail 链式串联」——本仓 finding 是**并发列表**（一次扫描多个 pattern），链式语义反而不自然 |
| 前置 | **B1**（否则没有 task 边界概念，只能挂在 tool 结果上，粒度不对） |
| 优先级 | P2 |

### A2 skill 的「仅本次执行有效」作用域

| 项 | 内容 |
|---|---|
| crewAI 机制 | 渐进披露两段式：先给 name+description → 命中才载入完整指令 → **仅该次执行有效**；支持 `@org/name@1.2.0` 版本锁定 |
| 本仓现状 | 渐进披露**已有**（`load_skill`，`workspace_tools.py:264` 注释明写动机）；`Skill` 模型（`skills/models.py:24`）有 `name/category/description/tags/path/content` |
| 差距 | ① 无作用域——加载过的 skill 留在上下文持续占 token；② 无版本概念 |
| 借鉴点 | 作用域。版本锁定对本仓当前用途（本地 `.prompts` / `skills/` 目录）收益低 |
| 落点 | `Skill` 加 `scope: Literal["session","step"] = "session"`；`load_skill` 支持 `step` 作用域时把内容挂 step 局部上下文，step 结束即释放 |
| 优先级 | P3（先量一下 skill 全文平均占多少 token，再决定值不值得做） |

### A3 memory 的衰减公式与 scope 树

| 项 | 内容 |
|---|---|
| crewAI 机制 | `decay = 0.5^(age_days / half_life_days)`，`recency_half_life_days` 默认 30；记忆按**层级 scope** 组织（`/agent/x`、`/project/y`）；带 `source`（provenance）与 `private` 标记；靠**去重合并**而非过期删除控制体积 |
| 本仓现状 | `kernel/memory/persistent.py:141` `PersistentMemory`；`find_relevant:238` = `_find_semantic:267` + `_find_keyword:318`；有 `memory_type` 字段 |
| 差距 | 有类型分类，**无 scope 树、无显式衰减曲线**；`MemoryEntry` 无 provenance 字段 |
| 借鉴点 | 衰减公式（可解释、可调）比「按类型硬删」好；scope 树比 `memory_type` 字符串更可扩展 |
| 落点 | `MemoryEntry` 加 `scope: str = "/"`、`source: str = ""`；`find_relevant` 排序显式引入半衰期项 |
| 不可借鉴 | LanceDB 后端。本仓 SQLite + FTS5（`memory/fts5.py`）已够用，换后端是净亏 |
| 优先级 | P3 |

---

## §3 核心提案：B1 节点级产物契约（P0）

### 3.1 问题陈述

本仓的校验发生在这两处，**中间是空的**：

```
工具执行        ← tool_exec.py:71 字符串前缀判成败
   ↑
（空）          ← 节点产物没有任何契约
   ↓
上层汇总        ← claim_validator / validation_gate（统计/趋势层）
```

`workflow/node_types.py` 的五个 dispatcher（`llm_agent` / `planner` /
`evaluator` / `python` / `tool`）统一经 `_envelope()`（:230）返回
`AgentResult(summary, artifacts, metrics)` —— **产物形状完全由 prompt 自行决定，
代码侧零约束**。

唯一沾边的是 `_dispatch_llm_agent`（:288-293）：尝试 `json.loads(answer)`，
成功就把结果塞进 `artifacts["parsed"]`，失败静默跳过。**这不是契约，是尽力而为。**

### 3.2 为什么这是「接线点」而不是「新功能」

一旦节点能声明「我的产物必须满足 X」，以下六件事同时获得落点：

| 现有缺口 | 有了产物契约之后 |
|---|---|
| critic / study_reviewer 的 verdict 无人消费 | verdict 有 schema，下游节点可 `condition` 消费 |
| `risk_controller` 校验字段漂移 | 校验器直接对 schema 校验，不再猜字段名 |
| `branches` 零使用 | 分支条件读的是**已校验的结构化字段**，不再读自由文本 |
| guardrail 无处挂（A1） | 挂在节点边界，与 crewAI 的 task 边界同构 |
| `structured_output.py` 4 层降级 | 契约命中时不需要降级；降级退化为「对自由文本的兜底」 |
| evaluator 要起完整 AgentLoop | 产物契约让「只判断」与「带工具跑」可区分 |

### 3.3 落位：不改 `WorkflowNode` schema

**关键事实**：`WorkflowNode.config` 是 `dict[str, Any]`（`definition.py:71`），
`from_dict` / `to_dict` 原样透传。**因此产物契约可以零 schema 变更地加进去。**

```python
@dataclass
class WorkflowNode:
    id: str
    type: str
    label: str = ""
    config: dict[str, Any] = field(default_factory=dict)   # ← 契约住这里
```

```json
{
  "id": "risk_review",
  "type": "llm_agent",
  "config": {
    "role": "risk_controller",
    "prompt_text": "...",
    "contract": {
      "required": ["risk_passed", "risk_rating"],
      "schema": {
        "risk_passed": {"type": "bool"},
        "risk_rating": {"type": "str", "enum": ["Green", "Yellow", "Red"]}
      },
      "on_violation": "retry",
      "max_retries": 3
    }
  }
}
```

**为什么不做成 `WorkflowNode` 的一等字段**：`definition.py` 的 `from_dict`
是白名单式构造，加字段要同步改 `to_dict`、`segment_cut`、以及可能的
序列化测试。`config` 已经是指定扩展位，走 config 是既有惯例（`role` /
`prompt_text` / `max_iterations` 都住这里）。

### 3.4 `on_violation` 的三种语义

| 值 | 行为 | 对应 crewAI |
|---|---|---|
| `fail`（默认） | 节点 `AgentStatus.ERROR`，错误进 `_PERMANENT_NODE_ERROR_MARKERS` 判据不匹配 → 可重试（走现有 `_retryable_node_error`） | 无对应（crewAI 更宽松） |
| `retry` | 把 schema 违例摘要作为修正提示**重跑该节点**，最多 `max_retries` | ✅ `guardrails` + `guardrail_max_retries` |
| `degrade` | 保留产物但标 `artifacts["_contract_violation"]`，下游可读到 | crewAI 的 `expected_output`（软约束） |

**默认 `fail` 而非 `retry`**：本仓有「不静默降级」的传统（理念六）。
retry 会在 LLM 不稳定时放大成本，应显式开启。

### 3.5 最小实现范围（防膨胀）

```python
# kernel/workflow/contract.py —— 唯一新增文件，约 120 行
@dataclass(frozen=True)
class NodeContract:
    required: tuple[str, ...] = ()
    schema: dict[str, dict] = field(default_factory=dict)   # JSON-Schema 子集
    on_violation: str = "fail"                              # fail|retry|degrade
    max_retries: int = 3

def parse_contract(config: dict) -> NodeContract | None: ...
def validate(result: AgentResult, contract: NodeContract) -> list[str]: ...

# node_types.py `_envelope()` 之后单点接入
```

**用 JSON-Schema 子集，不引 `jsonschema` 依赖**：只需支持
`type` / `enum` / `required` / `items` 四项即可覆盖本仓全部真实需求
（`risk_passed: bool`、`risk_rating: enum`、`topics: array`、`verdict: enum`）。
若将来需要完整 schema，再评估引依赖。

### 3.6 验收门

| # | 验收项 | 判据 |
|---|---|---|
| 1 | 零行为变化 | 未声明 `contract` 的节点，全量测试**零差异** |
| 2 | 契约可诊断 | 违例时错误消息含「缺哪个字段 / 哪个字段类型不对 / 期望 enum 实际值」 |
| 3 | 不掩盖失败 | `on_violation=fail` 时节点必须是 `AgentStatus.ERROR`，不能降级为 SUCCESS |
| 4 | 门禁通过 | `check_dangling_imports.py --layers` + `layer_matrix.py` 登记 |
| 5 | 首个真实用例 | 给 `risk_controller` 挂契约，且**修完 `quant_validators.py:64` 字段漂移**后校验首次真正生效 |

---

## §4 明确不做（C 类）

| 项 | 为什么不做 |
|---|---|
| **role / goal / backstory 内联进 preset** | 本仓已用 `.prompts/*.md` 外置。硬塞三要素是**同一信息写两遍**，preset 变胖。可读性该用文档解决，不该用重复字段解决（`AGENTS.md` 原则 2） |
| **hierarchical process（manager agent 自主分派）** | `swarm/` 与 `workflow/` 中 `manager` / `delegat` **零命中**——这是有序设计，不是遗漏。自主分派让执行路径不可预测，与「机器可证 / 事件可回放」直接冲突 |
| **再引入一套并行编排 API** | `guides/code-map.md §4` 已记**五处双重实现**待收敛。再叠一层是倒退 |
| **JSONC 格式** | 本仓 YAML + JSON 混用已成事实，迁移零收益 |
| **LanceDB / Chroma 记忆后端** | SQLite + FTS5 已够；换后端要重跑全部记忆测试 |
| **litellm 依赖** | 本仓 7 个 provider 适配器是显式白名单，`model_hub.py:54` 对未注册 provider **显式拒绝而非降级**。引 litellm 会让这条契约失真——与 crewAI 引 litellm 换取「100+ 模型」的取舍正好相反，本仓的取舍是对的 |

---

## §5 风险清单

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| **R1** | 契约变成「第二个 prompt」 | 同一要求在 prompt 和 contract 里各写一遍，漂移后难判谁对 | **契约只校验结构，不描述语义**。语义仍在 prompt；contract 只回答「字段在不在、类型对不对」 |
| **R2** | `retry` 放大 LLM 成本 | 模型不稳定时 N 倍调用 | 默认 `fail`；`retry` 必须显式开启；`max_retries` 硬上限 3，对齐 crewAI 默认 |
| **R3** | 违规检测误报导致节点级联失败 | 上游一个字段缺失 → 下游全 ERROR | 首个用例（`risk_controller`）**先只加 `degrade`**、观察一轮事件，再决定是否切 `fail` |
| **R4** | JSON-Schema 子集不够用 | 后续要 `oneOf` / `$ref` 等 | 明确边界：四项之外的需求**先证明真实存在**再加；不预防性实现 |
| **R5** | 与 `_dispatch_llm_agent` 现有 `json.loads` 逻辑重复 | 两处解析不一致 | 契约校验**复用** `artifacts["parsed"]`，不重解析；解析失败时契约报「产物不是 JSON」 |
| **R6** | 只覆盖 `workflow`，不覆盖 `swarm` | preset 路径仍然无契约 | 明确范围：**首批只做 `workflow`**。`swarm` 走 `AgentResult` 同一信封，后续可平移，但不预做 |

---

## §6 落地批次

| 批次 | 内容 | 行为变化 | 回滚 |
|---|---|---|---|
| **B1-a** ✅ | `kernel/workflow/contract.py` + `_envelope` 单点接入 + 单测 | **零**（未声明 contract 不生效） | 删文件 + 回退一处调用 |
| **B1-b** | `risk_controller` 挂契约（`degrade` 模式）+ 事件落盘 | 零（只多事件） | 删 preset 里的 `contract` 段 |
| **B1-c** | 修 `quant_validators.py:64` 字段漂移，校验首次生效 | 有（校验开始真跑） | 回退校验器 |
| **B1-d** | 观察一轮后，`risk_controller` 切 `fail` | 有 | 改回 `degrade` |
| **A1**（P2） | guardrail 加 `enforce` 开关 + 回传修正 | 零（默认 False） | 开关 |
| **A2/A3**（P3） | skill 作用域 / memory 衰减 | 有 | 各自开关 |

> **B1-a 实施记录（2026-11-16）**：
> - 新增 `kernel/workflow/contract.py`（`NodeContract` / `parse_contract` /
>   `validate_result` / `enforce_contract`）。
> - 接入点在 `node_types.py:dispatch_node`——**唯一**让五个 dispatcher 汇合的
>   位置，故未改任何 `_dispatch_*`。`enforce_contract` 以 `run(ctx)` 闭包
>   包住 handler，因此 `retry` 能改写 `ctx.objective` 而不碰 dispatcher 签名。
> - 与 §3.5 设计的**两处偏离**：
>   ① `retry` 仅对 `llm_agent` / `planner` / `evaluator` 生效，
>   `python` / `tool` 自动降级为 `fail`——后两者不读 `ctx.objective`，
>   盲重试会跑出完全相同的确定性结果；
>   ② `payload_of()` 优先读 `artifacts["parsed"]`，回落到 `artifacts`——
>   同时覆盖 `llm_agent`（已 `json.loads`）与 `python`/`tool`（结构化数据直存）
>   两种形状，满足 §5 R5「不重解析」。
> - 新增 `node.contract_violation` 事件（`emit_event` 失败被吞，观察不阻断派发）。
> - 单测 `tests/test_workflow_output_contract.py`：33 个用例。
>   回归：`-k "workflow or swarm or contract"` → **744 passed / 4 skipped /
>   4 xfailed / 0 failed**。门禁：`check_dangling_imports --layers` 上行违规 0，
>   `check_path_literals` 0 问题。
> - 注意：`tests/test_workflow_node_contracts.py` 是**既有**文件，测的是
>   `exec.node_max_retries` / `node_timeout_seconds` 那批 B3 契约，与本方案的
>   产物契约无关；新测试另起文件名以避混淆。

**顺序不可交换**：B1-c 依赖 B1-a/b（没有契约就没有校验对象）；
A1 依赖 B1-a（否则 guardrail 无处挂）。

### 6.1 如果只做一批

**做 B1-a。** 它行为变化为零、可完全回滚、约 120 行，且它是
`branches` / `risk_controller` / `critic` / guardrail / `structured_output`
五处缺口**共同的接线点**。即使后续全部放弃，这个文件本身没有副作用。

**（2026-11-16 已实施，见上表实施记录。）**

---

## §7 与既有机制的关系

| 既有机制 | 关系 |
|---|---|
| `_dispatch_*` 五个 dispatcher | **不动**。契约是 `_envelope` 之后的统一后置校验，不侵入各 dispatcher |
| `structured_output.py`（4 层降级） | **不动**。契约复用其 `artifacts["parsed"]`；长期看契约命中会让降级路径调用量下降，但不改它 |
| `guardrails.py`（observation-only） | **不动**。A1 是给它加 `enforce` 参数，默认 False，现行为零变化 |
| `claim_validator` | **不动**。它是**统计层**（`confidence = verified/total`），契约是**结构层**。两者层次不同，不重复 |
| `validation_gate`（RSI） | **不动**。那是进化门禁，与节点产物无关 |
| `SwarmPreset.branches` | **不改代码**。等 B1 落地后，分支条件可改为读已校验字段（属后续批次） |
| `CapabilityRegistry` | **不冲突**。契约是 kernel 内部结构校验，不需要业务注册 |

---

## §8 待拍板的开放问题

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| **Q1** | 契约住 `config` 还是 `WorkflowNode` 一等字段？ | config / 一等字段 | **config**：零 schema 变更，且 `role`/`prompt_text` 已有先例 |
| **Q2** | `on_violation` 默认值 | `fail` / `retry` / `degrade` | **`fail`**（不静默降级传统）；首个用例单独用 `degrade` 观察 |
| Q3 | 是否引 `jsonschema` 依赖 | 引 / 自实现子集 | **自实现四项子集**，真实需求证伪后再引 |
| **Q4** | 只覆盖 `workflow` 还是同期覆盖 `swarm`？ | 只 workflow / 两者 | **只 workflow**。`swarm` 后续平移，不预做 |
| Q5 | 首批挂几个节点？ | 1 个 / 全部 37 preset | **1 个**（`risk_controller`）。先证明机制可行 |
| **Q6** | 本方案是否值得做 | 全做 / 只做 B1-a / 不做 | **只做 B1-a**。它是五处缺口共同接线点，行为变化为零 |
| Q7 | A2/A3 是否同期做 | 同期 / 推迟 | **推迟**。先量 skill 全文占多少 token、memory 检索命中率，再决定 |

### 8.1 如果只能回答一个问题

**Q6**。本方案的论证依赖一个可证伪的前提：**把契约补上之后，那些
「有产出无消费者」的东西真的会被消费**。这个前提在 B1-a 落地后即可检验——
若给 `risk_controller` 挂上契约后，`branches` 仍然零使用、critic 仍然零编排，
那说明缺口不在契约层，而在**编排决策**层，本方案的价值判断就需要修正。

**不要因为「crewAI 这么做」就做。**

---

## §9 明确不做（与 §4 区分：§4 是「不借鉴 crewAI 的 X」，此处是「本方案自己不做的 Y」）

1. **不做契约的代码生成**（不根据 schema 自动生成校验器 / Pydantic 模型）。
2. **不做跨节点契约推导**（不从下游 `required` 反推上游该产什么）。
3. **不把契约写进 prompt**（避免 R1 的「第二个 prompt」）。
4. **不在 B1 期间改 `structured_output.py`**（两件事混做无法归因）。
5. **不支持嵌套对象契约**（首批只支持扁平字段 + 数组，覆盖真实需求即可）。
6. **不让契约有副作用**（校验只返回值，不修改 `AgentResult`；`degrade` 例外，仅加标记字段）。
