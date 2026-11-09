# 第一阶段收尾 · 通用 agent 真实可用性验证计划

> 前置：批次 13 已完成（S3-a 六缺口 + S3-b1/b2 部分 + P0/P1 安全修复），
> 全量 6,995 passed / 71 skipped / 0 failed，层方向全 0 违规。
> 本文档回答一个问题：**"能当通用 agent 正常使用"这个完成标志，怎么验？**

---

## §0 计划的前提：系统里有两条并行的执行路径

这是本计划最重要的发现，也是之前所有验收结论都不完整的原因。

### 已拍板的三个决定（2026-11-09）

| 决定 | 选择 | 影响 |
|---|---|---|
| LLM 可用性 | **用户提供 key** | V2 可真跑，V3 决策有证据 |
| 路径收敛方向 | **先验证再决定** | 不预设结论，由 V2 数据说话 |
| V4 时机 | **推迟到第二步 RSI 之前** | V1–V3 完成即进 RSI 准备，架构收敛留到那之前 |

| | 路径 A：`AgentLoop` | 路径 B：`make_react_node` |
|---|---|---|
| 文件 | `kernel/agent/loop.py`（1,388 行） | `kernel/runtimes/nodes/react.py`（282 行） |
| 入口 | `service.py:993` → `arun()` | `Gateway → SessionController` → GraphEngine 节点 |
| 生产消费者 | 旧 `/send_async`、scheduled_research、evals、CLI/TUI | **web chat**（`singleton.py:92` 构造 Gateway） |
| 引擎 | skeleton 状态机（默认）| 手写 for 循环 |
| 权限 | `enforce_permission_sync`（本次 P0 才补上 sync 分支）| `ctx_factory → registry.execute(ctx=)` |
| 压缩 | `make_compactor`（S3-b2 去重后）| `compactor` 注入（同一个 `make_compactor`）|
| 预算 | `BudgetDecider`（S3-a 才接上）| `RunBudget` |
| tool_set 裁剪 | **无** | `task_signature` → `get_definitions_for` |
| no-progress | **有**（含 approval gate）| **无** |
| guardrails | **有** | **无** |
| hooks | **有** | **无** |
| deciders/skeleton | **有** | **无** |
| steering inbox | 有（`_inbox`）| 有（同名机制）|

**结论**：两条路径各有对方没有的能力，**没有一条是"完整通用 agent"**。
所以"能不能当通用 agent 用"必须**分路径验收**，然后决定收敛方向。

---

## §1 四个阶段

### 阶段 V1 · 让验收工具本身可信（0.5 天）

**问题**：`tests/test_capability_smoke.py` 的 E1–E5 走 `loop.registry.execute()`，
而生产路径是 `_execute_tool_call_core`（路径 A）或 `registry.execute`（路径 B）
——E 系列只在**路径 B 的近似**上成立，对路径 A 无证明力。这正是 P0 权限漏洞
藏住的原因。

**动作**：
1. `test_capability_smoke.py` 的 `_run()` 改为调用
   `loop._execute_tool_call_core(tc, LoopResult(), async_mode=...)`，
   `async_mode` 参数化 `[False, True]`。
2. 新增 `test_react_node_capability.py`：对 `make_react_node` 跑同一组
   E1–E5 断言（当前完全没有针对路径 B 的能力冒烟）。
3. 保留 `registry.execute` 作为**第三条**测试维度（它有自己的生产用途：
   `react.py` 与 `ToolRegistry` 的独立调用者）。

**验收**：
- 三条路径 × E1–E5 全绿
- 故意把 `enforce_permission_sync` 注释掉 → 路径 A/B 的测试必须红
  （变异验证，证明断言有效）

**风险**：低。只改测试，不动生产代码。

---

### 阶段 V2 · 真实 LLM 端到端（1 天）

**问题**：所有 E 系列都是 mock LLM。真实模型会不会正确调用工具、
多轮后是否还知道在做什么、长任务会不会跑飞——**没有证据**。

**现状可复用的脚手架**：
- `tests/conftest.py::real_llm_config` / `live_client`（session 级，opt-in）
- `tests/test_react_live.py`：**已覆盖路径 B**（calculator 工具循环 + 八站全链路）
- `tests/test_llm_live.py`：A–J 能力矩阵，但测的是 LLM 节点不是 agent

**动作**：新增 `tests/test_agent_loop_live.py`，补齐**路径 A** 的三件事
（用户定义的完成标志）：

| 用例 | 内容 | 断言 |
|---|---|---|
| **chat** | 多轮对话，第 3 轮引用第 1 轮信息 | 回答含早期信息；无工具调用事件 |
| **执行代码** | 让模型写 `fib.py` 并运行得到结果 | 文件存在、`shell` 被调用、stdout 正确 |
| **coding** | 给一个含 bug 的小文件，让模型修好并跑测试 | 文件被修改、测试通过、`write`+`shell` 都被调用 |
| **权限边界** | 让模型尝试 `sudo` | 被 hard_deny 拦住，模型收到可读错误并改方案 |
| **预算** | `budget_max_tokens=2000` 的短预算 | 触发 `budget` 停止而非跑满 |

**运行方式**：
```
$env:QNODES_LIVE_LLM="1"
py -3.13 -m pytest tests/test_agent_loop_live.py -m llm -v
```

**关键约束**：
- **必须先确认有可用 LLM key**。当前环境 `LLM_API_KEY` / `OPENAI_API_KEY`
  都未设置，`~/.quantnodes/llm.json` 也不存在 → 所有 live 测试会 skip。
  这一阶段的**第一个动作是确认 key**，否则整个 V2 无法执行。
- 每个用例限制 max_iterations ≤ 8、预算上限，避免真实调用烧钱。
- 用例必须幂等且在临时 workspace 内，不污染仓库。

**验收**：5 个用例全绿；记录实际 token 消耗与耗时。

---

### 阶段 V3 · 根据 V2 结果决定收敛方向（0.5 天决策 + 后续实施）

V2 会暴露两类问题，处置不同：

**若路径 A（AgentLoop）在真实任务上不可靠**
→ 说明骨架迁移尚未完成，**回退到 `AGENTNODES_ENGINE=legacy`**
  并定位具体失败点。不要删 legacy。

**若路径 A 可靠，但路径 B（web chat 生产路径）缺能力**
→ 这是当前的预期情况。需要决定：
- **选项 1**：把 web chat 切到 `AgentLoop`（统一到一条路径）
  - 收益：一套实现、一套测试、能力最全（有 guardrails/hooks/deciders）
  - 代价：`AgentLoop` 缺 `task_signature` 工具裁剪与 `RunBudget`，
    需要先补齐；`SessionController` 的图编排语义（GraphEngine 多节点）
    要重新对齐
- **选项 2**：把缺失能力补进 `react.py`
  - 收益：不动 web 路径，风险低
  - 代价：两套实现永久并存，能力持续漂移（就是当前问题的成因）

**我的建议是选项 1，但分两步**：先用 V2 证明 `AgentLoop` 在真实任务上
≥ `react.py`，再迁移。反之则先补 `react.py` 的 guardrails/hooks/no-progress
（工作量小，282 行文件的局部增强）。

**这一步的产出是一份决策文档**，不是代码。

---

### 阶段 V4 · 阶段 3 架构收敛（**推迟到第二步 RSI 之前**）

> **用户已拍板**：V4 不在本计划内执行。V1–V3 完成后直接进入第二步 RSI 准备
> （批次 5 的 R3 + W5），V4 作为「RSI 开工前的最后一次架构收敛」。
> 理由：legacy 引擎是当前唯一回滚路径；且 RSI 会重构大量代码，
> 先做收敛再被 RSI 改动覆盖，等于白做一次。

下表保留为 V4 的待办清单，供 RSI 之前取用：

| 动作 | 收益 |
|---|---|
| 删 `_run_loop_core`（246 行）| `loop.py` 1,388 → ~1,140 |
| 删旧构造参数（73 行签名 + 49 行 fold）| → ~1,020 |
| 拆 `__init__` 赋值块（119 行）到 `apply_spec` | → ~900 |
| 删 `loop_telemetry` / `loop_lifecycle` 薄壳（106 行）| 去除 6 基类 MRO |
| 删剩余 3 个 mixin 或改组合 | → 目标 ≤650 行 |

**明确不做**：在 V2 证明前删 legacy 引擎——那是唯一的回滚路径。

---

## §2 时间与风险

| 阶段 | 工作量 | 风险 | 阻塞项 |
|---|---|---|---|
| V1 | 0.5 天 | 低（只改测试）| 无 |
| V2 | 1 天 | 中（真实网络、费用）| **用户提供 LLM key** |
| V3 | 0.5 天 | 低（只出文档）| V2 结果 |
| ~~V4~~ | ~~1-2 天~~ | — | **推迟到 RSI 之前**（用户决定）|

**本计划总时长**：约 2 天（V1 + V2 + V3）。
V4 另计，在进入 RSI 之前执行。

---

## §3 本计划明确不覆盖

- 第二步 RSI（GEP-RSI 七步闭环、journal/scoreboard 端口）
- 批次 5 的 R3/W5
- llmwikify 反向依赖接入
- 两处 `_tool_call_hash` 算法不一致（独立小项）
- 三个未解体 mixin 的搬家（并入 V4）

---

## §4 第一个动作

**等用户提供 LLM key**（已确认会提供）。

拿到后按此顺序配置（任选其一，优先级从上到下）：
1. 写 `~/.quantnodes/llm.json`（`real_llm_config` 夹具的首选路径）
2. 设 `LLM_API_KEY` + provider 环境变量
3. 设 `AGENTNODES_LLM_CONFIG` 指向自定义配置文件

**同时开始 V1**（不依赖 key）：把 E1–E5 切到生产路径 + 新增
`test_react_node_capability.py`。

V2 编写期间建议用**最便宜的模型**（如 `deepseek-chat`）跑，
单个用例限制 ≤ 8 轮迭代、≤ 2000 token 预算。
