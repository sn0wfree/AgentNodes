# AgentNodes 项目拆分规划（2026-11-04 · W12）

> 决策（用户，2026-11-04）：把 **agent 部分**与**业务部分**剥离。
> agent 部分单独放入 **`AgentNodes`** 项目独立开发；业务部分使用 agent 部分继续开发。
> **第一步：整个项目搬过去，保留历史，然后开始做减法。**
>
> 目标仓库：`D:\Github\AgentNodes`（已存在，**空 git 仓库**，remote `sn0wfree/AgentNodes.git`）
> 来源仓库：`D:\Github\quantnodes-strategy-research`（1594 commits，2026-07-20 → 2026-11-04）

---

## 0. 结论先行：为什么「先整体搬、再减法」是对的

三条取证支撑这个路径：

**① 拆分在技术上可行——agent 核心对业务是「可选依赖」而非「必需依赖」。**
`kernel/agent` 里只有 **10 个文件**引用 `apps`，且每一处都有保护：

| 依赖点 | 保护机制 | 缺 apps 时行为 |
|---|---|---|
| `loop.py:98` `_get_goal_store` | 函数内 lazy import | 仅 goal 注入失效 |
| `loop.py:1223` `HypothesisAutoCreator` | `if not self.enable_hypothesis_auto_create: return` + try | 功能开关关闭 |
| `context_injector.py:118/224/350` | try/except + `if not loop.enable_goal_injection: return` | 注入器降级 |
| `exec_registry.py:69/90` | try/except ImportError | executor 不注册 |
| `dag_config.py:177` `to_study_graph` | 独立方法 | 调用时才需要 |
| `builtin_tools/` 6 个工具 | 整个工具模块 | 工具不注册 |

→ **agent 核心的 82 个文件 / 16,031 行完全不碰 `apps`**。

**② 现状已经是四层架构，物理边界现成。**
`kernel` + `foundation` = 平台侧（agent 框架）；`apps` = 业务侧（研究域）。
不需要重新划线，只需移动目录 + 改包名。

**③ 历史保留成本极低。**
1,594 commits / 19.37 MiB `.git` / 105 个未推送 commit —— 全量迁移只需
`git push --mirror` 或 `git remote set-url` + push，**不需要 filter-repo 重写历史**。

---

## 1. 边界定义

### 1.1 目标形态

```
AgentNodes（agent 框架，独立开发）        quantnodes-strategy-research（业务）
┌─────────────────────────────┐          ┌──────────────────────────┐
│  src/agentnodes/            │          │  src/quantnodes/         │
│    ├─ kernel/   （平台机制）  │  ←依赖→  │    └─ apps/ （研究域）     │
│    ├─ foundation/（基建）     │          │                          │
│    └─ interfaces/（部分）    │          │  依赖 agentnodes 作为库    │
└─────────────────────────────┘          └──────────────────────────┘
```

**依赖方向：业务 → agent，单向。agent 永不依赖业务。**

### 1.2 逐包归属表

按实测（业务语义密度 = 研究域关键词出现密度）：

#### → 归 AgentNodes（agent 框架）

| 包 | 文件 | LOC | 业务词密度 | 备注 |
|---|---:|---:|---:|---|
| `kernel/agent` | 92 | 21,706 | 5.6% | 核心；含 10 个待解耦文件 |
| `kernel/workflow` | 15 | 3,219 | 3.8% | 编译器 |
| `kernel/runtimes` | 10 | 1,626 | 0.6% | GraphEngine |
| `kernel/gateway` | 7 | 1,462 | 1.5% | 协议端点 |
| `kernel/swarm` | 5 | 1,244 | 3.0% | ⚠ 3 条上行 |
| `kernel/scheduled_research` | 6 | 1,194 | 2.8% | ⚠ 3 条上行 |
| `kernel/orchestration` | 7 | 1,131 | 0.6% | |
| `kernel/permission` | 5 | 824 | 1.8% | |
| `kernel/memory` | 3 | 604 | 0.2% | |
| `kernel/events` | 2 | 444 | 0.5% | |
| `kernel/context` | 4 | 407 | 2.0% | |
| `kernel/hooks` | 4 | 260 | 2.3% | |
| `kernel/skills` | 4 | 238 | 2.1% | |
| `kernel/session` | 2 | 229 | 0.0% | |
| `kernel/seq_generator.py` | 1 | 94 | 0.0% | 待退役 |
| `foundation/llm` | 22 | 4,678 | 0.3% | ✅ 干净 |
| `foundation/observability` | 5 | 865 | 0.2% | ✅ |
| `foundation/web` | 6 | 616 | 0.8% | ✅ |
| `foundation/storage` | 7 | 1,570 | 2.6% | ⚠ `data_store.py` 含业务字段 |
| `foundation/utils` | 16 | 2,097 | 11.1% | ⚠ **8 个文件是业务语义**（见 §3.2） |
| `foundation/` 其余 | 6 | 1,023 | <3% | registry/errors/git/… |

#### → 留 quantnodes-strategy-research（业务）

| 包 | 文件 | LOC | 备注 |
|---|---:|---:|---|
| `apps/*` | 609 | 62,311 | 全部研究域 |
| `interfaces/api/routers/{study,goal,hypothesis,validation,study_helpers,slash_commands}.py` | 6 | ~4,700 | 业务路由 |
| `interfaces/cli/commands/{slash_goal,autoresearch,export}.py` | 3 | ~1,700 | 业务命令 |
| `interfaces/mcp/server.py` | 1 | 905 | ⚠ 混合（11 条 apps 依赖） |
| `templates/` | 2 | 526 | 策略模板是业务内容 |

#### → 混合，需逐文件裁定

| 项 | 规模 | 决策 |
|---|---:|---|
| `interfaces/api/session/` | 14 文件 5,924 行 | **agent 侧**（会话编排是平台机制）—— 但本轮先留业务侧，后续再移 |
| `interfaces/api/{app,container,middleware,dependencies,schemas}` | 12 文件 | agent 侧（框架外壳） |
| `interfaces/cli/{tui,theme,onboard,ui}` | ~30 文件 | agent 侧（交互外壳） |
| `interfaces/mcp/server.py` | 1 文件 905 行 | 混合：框架 + 业务工具注册 |
| `kernel/agent/builtin_tools/` | 18 文件 6,728 行 | **业务工具**（回测/因子/数据/goal）→ 应留业务侧 |

> **本轮不裁定混合项。** 第一阶段只做「整体搬家」，第二阶段做减法时逐项处理。

---

## 2. 阶段规划

### 阶段 0 · 准备（本次执行）

**目标**：两个仓库都处于可回滚的干净状态。

1. 把 `refactor/v6-gateway` 的 105 个未推送 commit 推到 origin
   （**必须先推**——否则任何操作都可能丢失这些 commit）
2. 打 tag `pre-split-20261104` 标记当前状态
3. 记录基线：`git rev-parse HEAD`、全量测试失败集、`graphify` 图快照

### 阶段 1 · 整体搬家（保留历史）

**核心手法：`git push --mirror`** —— 复制**所有** refs（分支、tag）到新仓库，
历史 100% 保留，零重写。

```bash
# 1. 在 AgentNodes 仓库加一个 mirror remote
cd D:\Github\AgentNodes
git remote add source D:\Github\quantnodes-strategy-research
git fetch source "+refs/*:refs/*"        # 拉全部 refs

# 2. 推全部 refs 到 AgentNodes 的 GitHub
git push origin "+refs/heads/*:refs/heads/*" "+refs/tags/*:refs/tags/*"
```

之后 `D:\Github\AgentNodes` 与源仓库**历史完全一致**（同 commit hash）。

**关键决策：搬家后，两个仓库的内容完全相同。**
接下来才是「减法」——在 AgentNodes 里删业务，在 quantnodes 里删 agent。
**减法阶段两边都会产生新 commit，原仓库的历史作为共同祖先保留。**

### 阶段 2 · AgentNodes 侧减法（删业务）

在 AgentNodes 仓库上：
1. `git rm -r src/strategy_research/apps`（609 文件 / 62,311 行）
2. `git rm -r src/strategy_research/templates`（业务模板）
3. 删业务专属的 interfaces 文件（路由/命令）
4. 删 `tests/` 里业务专属的测试
5. **包名重命名**：`strategy_research` → `agentnodes`
   （`src/agentnodes/` + `pyproject.toml` 的 `[project] name` + entry points）
6. 改 `docs/` 里指向业务的部分（保留架构文档，删业务设计文档）

### 阶段 3 · 断 agent → 业务的上行依赖（40 条）

`kernel/agent` 的 10 个文件需要解耦。三种手段，按上表逐项：

| 手段 | 适用 | 例子 |
|---|---|---|
| **能力注入（推荐）** | 框架需要业务能力时 | `loop.py` 的 goal 注入 → 改为 `self.goal_context_provider` 回调，业务侧注册实现 |
| **特性开关 + lazy import** | 可选功能 | 已有的 `enable_hypothesis_auto_create` 模式，扩展到 `builtin_tools/` |
| **移出 agent** | 纯业务工具 | `builtin_tools/{backtest,factor,data,data_clean,goal}_tools.py` → 业务侧注册 |

`builtin_tools/` 6 个文件（3,608 行）贡献 32/40 条上行 —— **它们本就是业务工具**，
移到业务侧是最自然的解法，且与 ADR-003「CapabilityRegistry 端口化」是同一件事。

### 阶段 4 · quantnodes 侧减法（删 agent）

在 quantnodes 仓库上：
1. `git rm -r src/strategy_research/{kernel,foundation}`
2. `git rm` agent 专属的 interfaces 文件
3. `pyproject.toml` 改为依赖 `agentnodes`（PyPI 或 git URL）
4. 改所有 `strategy_research.kernel.*` / `strategy_research.foundation.*` import
   → `agentnodes.kernel.*` / `agentnodes.foundation.*`

### 阶段 5 · 验证与门禁迁移

- 层门禁（`test_layer_matrix.py`）拆成两份：
  AgentNodes 版只管 agent 内部（无 L2）；quantnodes 版管业务 + 依赖方向
- `check_dangling_imports.py` 两边各一份
- 全量测试两边各自绿

---

## 3. 三个必须提前认清的障碍

### 3.1 `foundation/utils/` 里混着业务语义（1,190 行）

**这批文件现在住在 L4「零业务知识」层，但内容是纯业务。** 拆分时必须处理：

| 文件 | 行 | 业务词 | 内容 |
|---|---:|---:|---|
| `backtest_metrics.py` | 314 | 29 | sharpe/sortino/calmar/drawdown/equity |
| `backtest_engine.py` | 190 | 31 | Callback 回测引擎 |
| `strategy_engine.py` | 194 | 22 | StrategyEngine（回测主引擎） |
| `backtest_utils.py` | 182 | 11 | 换手率/再平衡 |
| `backtest_models.py` | 86 | 12 | BacktestResult 等 |
| `metrics.py` | 241 | 25 | 绩效指标 |
| `market_detection.py` | 97 | 35 | 市场识别 |
| `covariance.py` | 92 | 13 | 协方差 |
| `backtest_config.py` | 81 | 3 | BacktestConfig |
| `ic_utils.py` | 67 | 3 | IC 相关 |

**决策点**：这些是「通用量化工具」还是「研究域实现」？
- 若 agent 项目也要做量化研究 → 留 AgentNodes 的 `foundation/utils/`（但需改名去掉 backtest 前缀）
- 若 agent 只做 agent 框架 → 全部移到业务侧

**倾向**：随业务侧走（因为 `strategy_engine` 与 `backtest_engine` 是回测执行引擎，
属于研究域执行件而非基础设施）。但这会让 AgentNodes 的 `foundation` 少一大块，
需要业务侧自建 `foundation/utils/`。

### 3.2 `builtin_tools/` 是业务工具却住在 agent 侧（6,728 行）

18 个文件里 6 个是纯业务（回测/因子/数据/数据清洗/goal/回测分析），
贡献 32/40 条上行依赖。**这些不是 agent 能力，是业务能力**。

处理方式与 ADR-003 端口化是同一件事：
AgentNodes 提供 `ToolRegistry` + 插件注册机制，业务侧提供具体工具实现。
**注意**：`goal_tools.py`（574 行）虽是业务，但 `goal` 概念本身可能是 agent 框架的
通用能力（目标驱动循环）—— 需要业务/框架边界再裁定。

### 3.3 `interfaces/mcp/server.py` 是单文件混合体（905 行，11 条 apps 依赖）

MCP server 同时注册 agent 通用工具和业务工具。拆分时需要：
- 框架侧：MCP server 骨架 + 工具注册机制
- 业务侧：业务工具注册调用

**这是整个拆分里最麻烦的单点**，因为它没有目录粒度可切。

---

## 4. 风险与回滚

| 风险 | 缓解 | 回滚 |
|---|---|---|
| 推送后源仓库状态错乱 | 阶段 0 先推 + 打 tag | `git reset --hard pre-split-20261104` |
| `--mirror` 覆盖目标仓库已有内容 | 目标仓库是**空的**（已核实） | 重新 fetch |
| 减法阶段误删 | 每阶段一个 commit，逐阶段可退 | `git revert` |
| 包名重命名遗漏 import | 用 `rewrite_import_prefix.py`（已固化）+ `check_dangling_imports` 验证 | 阶段回退 |
| 两仓库同时改导致历史混乱 | **明确分工**：AgentNodes 只做减法，quantnodes 只做减法，各自提交到各自 origin | 独立回滚 |
| 业务侧功能在拆分中回归 | 减法前先固化基线测试失败集 | 差分验证 |

**回滚总开关**：两仓库都保留 `pre-split-20261104` tag，
任何阶段出问题 → `git reset --hard pre-split-20261104`。

---

## 5. 执行顺序建议

```
阶段 0  推 origin + 打 tag + 记基线           ← 必做，否则不安全
   ↓
阶段 1  push --mirror 整体搬家                 ← 用户指定的第一步
        验证：两仓库 HEAD hash 相同
   ↓
阶段 2  AgentNodes 侧减法：删 apps/templates/业务接口
        验证：agentnodes 侧 import 自洽、门禁绿
   ↓
阶段 3  断 40 条上行依赖（能力注入 / 移出 builtin_tools）
        验证：AgentNodes 侧零 apps 引用
   ↓
阶段 4  AgentNodes 包名重命名 strategy_research → agentnodes
        验证：pip install -e . 后 import 正常
   ↓
阶段 5  quantnodes 侧减法 + 改为依赖 agentnodes
        验证：业务全量测试绿
```

**阶段 4（包名重命名）与阶段 5（业务侧引用改名）必须同时发布**——
中间态两个仓库都不可用。建议先做 2、3（不动包名），验证无误再一起做 4、5。

---

## 5.1 路线图：泛化版「目标/准则/证据」机制（2026-11-05 新增）

**裁决**（用户，2026-11-05）：apps/goal 随减法批次 2 移除，但其背后的
**「objective + criteria + evidence」模式**被认定为可信 agent 的一等能力，
列入本路线图，在框架稳定后**从 golden 归档泛化重建**。

**为什么移除而不是保留**（评估证据，2026-11-05 实测）：

| 证据 | 结论 |
|---|---|
| RSI / Ticket / todos 三条内核任务线**零引用** goal | goal 不是内核组件 |
| goals 表 schema 含 `risk_tier` + 全套预算字段 | 与 Ticket/Assignment 的预算记账重复，且是研究形状 |
| goal 的唯一驱动者是 study（bootstrap 建 goal / budget 写证据 / scheduler 同步） | study 移除后无驱动者，6.1k 行空转 |
| workflow 1,903 行 = 9 个量化 agent 的 DAG | 纯研究业务 |

**重建时的泛化要点**（设计已归档 `testcase/golden/apps/goal/`）：

1. **schema 去研究化**：criteria 改为通用断言（可挂自定义 evaluator 回调），
   evidence 去掉 `run_id/source_provider` 等回测字段，去掉 `risk_tier`
2. **预算字段删掉**——执行预算的事实源是 Ticket/Assignment，不重复记账
3. **保留的机制核心**（约 1-2k 行）：GoalStore 的六表模型（goals/claims/
   criteria/evidence/audits/journal）、乐观锁 `expected_goal_id`、
   3-step SSE 文本响应协议、context 注入协议
4. **register 到 CapabilityRegistry**（`goal.store` 等 kind 回归），kernel
   的注入钩子（loop/context_injector）是惰性的，注册即自动复活，无需改 kernel

**触发条件**：框架核心稳定（批次 2-5 完成、包名重命名后）且出现第一个
真实的「跨会话长期目标」用例。

---

## 6. 本次已完成的取证

| 项 | 结论 |
|---|---|
| 目标仓库状态 | `D:\Github\AgentNodes` 存在，空 git 仓库，remote `sn0wfree/AgentNodes.git`，零 commit |
| 源仓库历史 | 1594 commits，2026-07-20 → 2026-11-04（3.5 个月），.git 19.37 MiB |
| 未推送 commit | 105（相对 origin/refactor/v6-gateway）、321（相对 origin/main）→ **必须先推** |
| tag | v0.2.0 / v0.4.0 / v0.4.1 / v0.5.0 → 随 mirror 一起迁移 |
| 分支 | main / refactor/v6-gateway → 随 mirror 一起迁移 |
| agent 核心对 apps 的依赖 | 10 文件 / 40 处，**全部是可选能力注入**，非核心必需 |
| 干净的 agent 子集 | 82 文件 / 16,031 行，完全不碰 apps |
| 运行时垃圾 | workflows.db / *.duckdb / .coverage / graphify-out 均**未被 git 跟踪**，copy 跟踪文件即自动过滤 |

---

## 附：相关文档

- `docs/structure-review-graphify-20261104.md` —— 四层架构评审（本拆分依据其分层结论）
- `docs/reuse-review-20261104.md` —— 复用性评审（阶段 3 的解耦手段参考其模式匹配表）
- `docs/layered-skeleton-design-20261103.md` —— 四层骨架设计契约（拆分后需重划 L1/L2 边界）
- `docs/physical-layer-migration-plan-20261103.md` —— 物理分层迁移协议（阶段 3/4 的搬迁手法可复用）
