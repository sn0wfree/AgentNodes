# 架构与 Harness 建议 — 2026-10-08

> ⚠ **历史文档**：v3 时期的 harness 建议。当前状态见 `project-status-and-roadmap-20261028.md`。

> 性质：调研记录（观点 + 证据），非实施方案。
> 视角：架构师 + agent-harness 工程师双重视角。
> 基线：commit `5ef9bc4`；扫描数据引自 `docs/structure-debt-survey-20261008.md`。
> 结论一句话：**测试与 eval 资产已超多数同类项目，但「资产」与「门禁」之间差最后一公里；架构侧最大问题不是文件太大，而是同一抽象（扩展点）有 7 个入口、同一职责（loop 编排）压在一个复杂度 32 的函数里。**

## 0. 扫描基线（证据）

| 维度 | 数据 |
|---|---|
| 体量 | 107,117 行 / 466 py / median 136 行 / max 1775 行 |
| ruff | 71 errors（F401×55 · C901×8 · F811×4 · I001×4），F821 已清零 |
| 本轮修的 bug | 12 个 F821 undefined-name（W5 mixin 拆分漏 import），commit `3bdde1d` |
| 死代码标签 | 17 处 `DELETE-CANDIDATE v0.6` + 守门测试 `tests/test_dead_code_labels.py`，未执行删除 |
| Eval | 70 题真 LLM，mean ≈ 0.957，pass@0.7 ≈ 0.94（nightly-only，无门禁） |
| 测试 | 12,366 case / 544 文件，无 marker 分层 |

---

## 1. 架构视角

### 1.1 扩展机制有 7 种，同一抽象被实现了 4 遍（最深的债）

「在 loop 生命周期的某个点插入行为」这一件事，项目里同时存在：

| 机制 | 位置 | 语义 | 状态 |
|---|---|---|---|
| hooks | `core/hooks/` | 回调 | composite 活；unified + adapter 已标 DELETE（B5/B6） |
| injectors | `core/agent/context_injector.py` | 前置/后置注入，order=0 / order≥100 | 活 |
| strategy steps | `core/agent/strategy/steps/`（9 个 Step） | 编排单元 | 活 |
| plugins | `core/agent/builtin_plugins.py` | 插件 | 活 |
| skills | `core/skills/` | 文档型技能 | 活 |
| mcp | `core/mcp/` | 外部工具协议 | 活 |
| toolsets | `core/agent/toolsets.py`（16 个） | 工具分组注册 | 活 |

**问题**：每加一个横切需求，作者都要先做「该放哪」的判断，而这个判断没有文档；改动时在 4 套机制间跳转是 W5 那 12 个 NameError 的根因之一。

**建议**：
1. 写 `docs/extension-points.md` 明确分工：
   - **steps = 编排**（决定 loop 走哪条路，可改变控制流）
   - **hooks = 观察**（只读旁路，不得改控制流）
   - **injectors = 数据注入**（往 messages 里加内容，不改流程）
   - **skills / mcp / toolsets = 能力面**（提供工具，不参与编排）
2. 重叠的合并（unified/adapter 按 B5/B6 删除即可）
3. 立规则：**不新增第 5 种编排级扩展机制**

### 1.2 Mixin 是借来的架构，不是设计出来的

`loop.py` 拆 6 个 mixin（W5，commit `901c224`）后，本轮修了 12 个 F821 —— **每个都是抽文件时漏 import**：

| 文件 | 缺失 |
|---|---|
| `loop_stream.py` | `uuid`（`53fb948` 修） |
| `loop_events.py` | `logging` + `logger`（3 处 except 分支会炸） |
| `loop_telemetry.py` | `_get_hook_loop` |
| `runner_knowledge.py` | `json` |
| `stub_agents.py` | `os` |
| `runner_langgraph.py` | `time` + 3 处 `StudyGraph` forward-ref |
| `study_helpers.py` | `StudyScheduler` forward-ref |

**这不是执行不仔细，是 mixin 模式的结构性代价**：mixin 之间靠 `self.*` 隐式共享状态，编译期与静态检查全部失效，边界只存在于作者的记忆里。每个新 mixin 都要人工 audit 依赖（历史上还缺过 `_TOOL_MAX_RETRIES`、`HeartbeatTimer`、`ToolContext`、`estimate_tokens`、`_compaction_persister` 等）。

**建议**：
- **不急着重构**（风险高、收益后置），但立增量规则：**新代码不再用 mixin**，改用显式 context 对象（项目已有 `_make_strategy_ctx` 雏形）
- 存量 mixin 逐个迁移时顺手上显式依赖
- 引入 pyright（宽松模式）—— `self.*` 隐式依赖正是类型检查最能抓的场景

### 1.3 `_run_loop` 复杂度 32 是症状，双引擎是病因

> ⚠️ **勘误（2026-10-08 同日）**：本节的 `EngineProtocol` 接口草案与「runner 认识双引擎」前提读码后不成立（分派实际在 `phase_engine.py:128-141`；`_run_loop` 的 32 来自生命周期控制流）。以 `docs/phase-engine-refactor-plan-20261008.md` 为准；本节的优先级判断（study 心脏、先低风险后高风险）仍成立。

`core/study/runner.py::_run_loop` 复杂度 **32**（阈值 15 的 2 倍，全库最高）。它同时承担：

- 双引擎分派（`phase_engine` ⇄ `langgraph_engine`）
- iteration cap / wrap-up nudge
- compaction / inbox drain / injectors
- LLM call / budget check / continuation / resilience
- tool execution / stop

**32 不是写得烂，是职责没处放。**

同族高复杂度：

| 复杂度 | 函数 |
|---|---|
| 24 | `phase_engine.py::run_round_phases` |
| 22 | `api/routers/study.py::study_dispatch_action` |
| 21 | `langgraph_engine.py::build_langgraph` |

**建议**（两步，顺序重要）：
1. **先定义 `EngineProtocol`**（`start_round` / `resume` / `snapshot` / `cancel`），runner 只依赖接口。这一步做完，「双引擎收口（删哪个）」从删除决策降级为**插件选择**，风险大幅下降
2. 再把 `_run_loop` 里 inline 的分支全部 step 化（项目已有 strategy/steps 基建），目标 <80 行

### 1.4 双入口（`chat_loop` vs `role_factory`）应收敛

- `core/agent/chat_loop.py::build_chat_agent_loop` — 3 个测试 + `service.py` + `cli/tui/session.py` + `routers/chat.py`
- `core/agent/role_factory.py` — 22 处引用

A1-A7 运行时缝做完后，chat 模式本质是配置组合：`stream_mode=True + max_iterations=1 + 特定 toolset + 特定 prompt`。**不值一个独立 factory。**

**建议**：收敛为 config preset（如 `AgentConfig.preset("chat")`），chat_loop 退化为薄封装后删除。

---

## 2. Harness 视角

### 2.1 Eval 资产没有接进门禁 — 0.957 目前是「展示数据」不是「质量门」

现状：
- 70 题真 LLM eval 只在 `nightly-eval.yml` 跑，**score 跌 10% 不 block 任何 PR**
- PR 上的 `eval-sim` job 走 `run_evals.py`（无 LLM），**测的是模拟器自己，不是被测系统**

**建议**（按成本递增）：

| 阶段 | 动作 | 成本 |
|---|---|---|
| 短期 | nightly 结果与上次 diff，跌幅 >2% 自动开 issue | 1 天 |
| 中期 | PR 上跑 **10 题黄金子集**（高区分度：injection / lookahead / overfit / safety 类），设为 required check | 2-3 天 |
| 同期 | scorecard 增加结构化 metrics（iterations、tool_calls、usage_tokens、elapsed），**不只一个 score** | 1 天 |

**为什么 metrics 比 score 重要**：score 是结果，metrics 是诊断。score 从 0.957 跌到 0.93 时，diff metrics 才知道是「iteration 变多」还是「工具调用变少」还是「token 爆了」。

**可复现性备注**：LLM 非确定，0.957 有方差。应在 scorecard 记录 model/prompt 版本，长期考虑 N 次取中位数。

### 2.2 12,366 个测试没有 marker 分层 — sharding 是在治标

现状分类靠人记：
- `test_session_triggers.py` 手工排除（写死在 `pytest_shard.py::DEFAULT_SKIP`）
- `test_chat_real_llm.py` 混在日常跑（18.4s，走真 LLM）
- flaky 靠 `--reruns=2` 全量兜底

**LPT 分片解决的是时长均衡，不是语义分类。**

**建议**：`pytest.mark.{unit, integration, e2e, slow, llm}` + CI 三个 lane：

| Lane | 触发 | 内容 | 时长目标 |
|---|---|---|---|
| PR gate | 每次 PR | unit + integration | < 5 min |
| merge 后 | push main | 全量（含 e2e/slow） | < 15 min |
| nightly | 定时 | llm 标记 + 真实 eval | 不限 |

收益：`--reruns` 从「全量兜底」收窄到「只对 e2e 兜底」，flaky 信号不再被噪声淹没；`DEFAULT_SKIP` 这类硬编码 hack 可以删。

### 2.3 12 个 F821 能进 main — 门禁只在 CI 不在本地

`tests.yml` 是本轮才加的，且只在 push/PR 触发。**ruff F821 是 F 规则默认项，本地跑一次就能抓到** —— 但没人跑，于是 W5 拆分的 12 个 NameError 全部进入 main，其中 8 个位于 except/debug 分支（静默路径，测试覆盖不到，只有触发错误时才会炸）。

**建议**：
1. **pre-commit hook**（ruff + 关键文件冒烟），或 husky 等价物
2. **GitHub branch protection**：required status checks + 禁 force push
3. **docstring claim 检查**（新增 CI job）：
   - 证据：`core/workflow/dag.py:85` 标签称 "currently no production callers"，实际 18 个 importer（`goal/dag_renderer.py`、`cli/tui/widgets/dag_view.py` 等）
   - 守门测试 `test_dead_code_labels.py` 只锁**标签存在**，不锁**内容真实** —— 文档在撒谎而 CI 无法发现

---

## 3. 优先级

| 级别 | 动作 | 成本 | 不做的代价 |
|---|---|---|---|
| **P0** | pre-commit + branch protection | 半天 | NameError 类 bug 继续进 main |
| **P0** | nightly eval 跌幅告警 | 1 天 | Eval 资产无法拦截回归 |
| **P1** | 扩展机制分工文档 + 止血规则 | 2 天 | 每个横切需求继续选边站 |
| **P1** | 测试 marker 三层化 | 2-3 天 | sharding/flaky 治理永远在追 |
| **P1** | `EngineProtocol` + `_run_loop` step 化 | 1 周 | 双引擎收口永远是「以后再说」 |
| **P2** | DELETE-CANDIDATE 清账（B1-B12） | 2 天 | 17 个标签挂到下一个大版本 |
| **P2** | pyright 宽松模式 + mixin 停增量 | 渐进 | 隐式 `self.*` 耦合持续累积 |

**顺序说明**：P0 是「堵漏」（半天到一天见效）；P1 里 marker 分层先于 EngineProtocol（前者给后者提供回归护栏）；P2 的 mixin → composition 只在改到时顺手做，不专项重构。

---

## 4. 证据索引

| 结论 | 证据位置 |
|---|---|
| 12 个 F821 | `ruff check src --select F821`（修复于 `3bdde1d`，现为 0） |
| 复杂度 top | `ruff check src --select C901` |
| 17 死代码标签 | `tests/test_dead_code_labels.py::EXPECTED_LABELS` |
| dag.py 标签漂移 | `rg -l "workflow\.dag" src/` → 18 文件 |
| 双引擎规模 | `rg -l phase_engine / langgraph_engine` → 11 vs 6 文件 |
| chat_loop 调用面 | `rg -l chat_loop` → 3 tests + service + tui + routers/chat |
| eval 结果 | `docs/phase-wrapup-20260929.md` |
| CI 现状 | `.github/workflows/tests.yml`（分片 + reruns）、`nightly-eval.yml` |

## 5. 相关文档

- `docs/structure-debt-survey-20261008.md` — 本轮扫描的完整数据
- `docs/architecture-framework.svg` — 分层框架图
- `docs/structure-debt-plan.md` — W1-W5 已完成的拆分记录
- `docs/agent-architecture-review.md` — A1-A7 运行时缝评审
