# 结构评审 — graphify 重扫（2026-11-04 · W9）

> 对象：`1f57465b`（物理分层收官态）
> 方法：graphify 全量 AST 重扫（45,982 节点 / 89,897 边）+ 自建 AST 探针交叉验证
> 结论一句话：**四层骨架成立，跨包零 import 死锁；但"分层正确"掩盖了三处结构性欠账——
> 一个 53 模块的概念耦合团、L1 里驻扎着 5.9k 行平台机制、以及 70% 的上行债集中在一个包里。**

---

## 0. 先说方法论：46 ≠ 46

重扫后我先做了一件事——把 graphify 的违规集合与 `test_layer_matrix` 门禁的违规集合做**逐条集合比较**，
而不是比总数。结果推翻了看起来最漂亮的那个数字：

| 口径 | L3→L2 计数 |
|---|---:|
| 门禁 `test_layer_matrix`（AST + PEP 328 解析，语句级） | **46** |
| graphify `imports`/`imports_from` 边（符号级） | **46** |
| 交集 | 37 |
| **门禁独有 / graphify 独有** | **9 / 0** |

**数值相同纯属巧合。** 真实一致率 37/46 = 80.4%。

graphify 系统性漏报了 238 条函数体内 import（lazy 覆盖率 72.5%，eager 覆盖率 99.6%）：

```
eager  总数 1171  覆盖 1166 ( 99.6%)  漏   5
lazy   总数  865  覆盖  627 ( 72.5%)  漏 238
```

一个已确认的具体漏点：`apps/study/scheduler.py:779` 的
`from strategy_research.interfaces.api.routers.web_session import _get_db`
在 graphify 里 **0 条边**——而这正是门禁 L2→L1=1 的唯一来源。

> **方法论结论（建议写进 `scripts/README.md`）：**
> graphify 不能作为循环依赖 / 上行违规的唯一判据。它的 AST 抽取对模块顶层 import 可靠（99.6%），
> 对函数体内 import 有 ~27% 的系统性盲区。**门禁（`test_layer_matrix`）是上行违规的权威口径，
> graphify 的正确用途是拓扑、热点、扇入扇出与体量**。两者只能比方向与簇分布，绝不能比绝对值，
> 更不能因为"数字一样"就宣布交叉验证通过。

上一轮（v8.4 架构图刷新）里"graphify 交叉验证"的表述需要按此修正——当时确实一致（都指向
core 残渣归零），但一致的是**方向**，不是**计数**。

---

## 1. 层矩阵：骨架是健康的

### 1.1 graphify 口径（仅 import 语句，符号级）

| from \ to | interfaces | apps | kernel | foundation |
|---|---:|---:|---:|---:|
| **interfaces** | — | 140 | 116 | 54 |
| **apps** | 0 | — | 40 | 104 |
| **kernel** | 2 | 46 | — | 82 |
| **foundation** | **0** | **0** | **0** | — |

### 1.2 门禁口径（真相）

| 基线 | 条数 | 性质 |
|---|---:|---|
| L2>L1 | 1 | seam 触点（study/scheduler → api.routers.web_session） |
| L3>L1 | 2 | seam 触点（event_store→projector · memory_manager→web_session_db） |
| L3>L2 | 46 | ADR-003 保留（CapabilityRegistry 端口化未做） |
| **L4>L2 / L4>L3** | **0 / 0** | ✅ foundation 彻底纯净 |

**两条硬结论：**

1. **foundation 行全零**——L4 已彻底无业务知识。这是本次迁移最干净的成果。
2. **apps→interfaces 在门禁里 = 1，在 graphify 里 = 0**——差的那 1 条就是上面那个 lazy 漏报。
   所以"apps 零上行"是**错的**，准确说法是"apps 上行 1 条，且是 seam 触点"。

### 1.3 体量（复核一致，架构图 v8.4 数据正确）

| 层 | 包 | 文件 | LOC | 占比 |
|---|---:|---:|---:|---:|
| interfaces | 4 | 117 | 29,511 | 21.4% |
| apps | 31 | 609 | 62,311 | 45.1% |
| kernel | 16 | 168 | 34,609 | 25.1% |
| foundation | 13 | 64 | 10,744 | 7.8% |
| templates | 1 | 2 | 526 | 0.4% |
| **合计** | | **960** | **137,701** | |

apps 内部集中度：`alpha_zoo` 25,669 行 = apps 的 **41.2%**（408 个 YAML 定义）；
`study` 11,034 · `goal` 6,146。kernel 内部：`agent` 21,660 行 = kernel 的 **62.6%**，
其中 `builtin_tools/` 6,728 行（agent 的 31.1%）。

---

## 2. 循环依赖：跨包零死锁，但概念耦合严重

这是本次评审最有价值的一节。我做了**三张不同的图**，因为"环"在三种粒度下含义完全不同：

### 2.1 包级 SCC —— 1 个 14 包巨环，但是**假环**

包级图上有一个 14 包强连通分量：
`apps/goal ↔ apps/study ↔ interfaces/api ↔ interfaces/cli ↔ interfaces/mcp ↔
kernel/agent ↔ kernel/gateway ↔ kernel/memory ↔ kernel/runtimes ↔
kernel/scheduled_research ↔ kernel/session ↔ kernel/swarm ↔ kernel/workflow`

我做了运行时反证：**10 种导入顺序全部成功，0 失败**。原因——包级图把
"`from pkg import Name`（穿透 `__init__`）"和"`import pkg.sub`（直达子模块）"混为一谈，
前者会触发 `__init__` 执行，后者不会。**包不是可靠的模块边界。**

### 2.2 模块级 SCC（含 lazy 边）—— 6 个真环，最大 53 模块

这才是真实的概念耦合图：

```
SCC[53]  apps.autoresearch, apps.goal.*, apps.study.*,
         interfaces.api.*, kernel.agent.*(loop 系 + builtin_tools),
         kernel.gateway.*, kernel.swarm.*, kernel.workflow.*
SCC[3]   interfaces.api.session.web_session_db ↔ kernel.agent.event_store ↔ kernel.agent.memory_manager
SCC[2]   apps.hypothesis.registry ↔ apps.hypothesis.store
SCC[2]   apps.goal.context ↔ apps.goal.store
SCC[2]   kernel.agent.sandbox ↔ kernel.agent.sandbox.static_sandbox
SCC[2]   interfaces.cli ↔ interfaces.cli.commands.merged
```

**53 个模块互相缠绕**——这解释了为什么改动 `apps/study` 或 `kernel/agent` 总是提心吊胆。

### 2.3 模块级 SCC（仅 eager 顶层 import）—— 4 个环，全部在**单包内**

这是唯一会导致 `ImportError` 的图：

| 环 | 成员 | 成因 |
|---|---|---|
| SCC[4] | `kernel.agent.{loop, loop_lifecycle, loop_progress, loop_tools}` | Mixin 拆分后各 Mixin 反向 `from .loop import LoopResult` |
| SCC[3] | `apps.study{.__init__} → {runner, scheduler} → back to study` | `runner.py:23-24` `from . import budget, state_utils` 穿透 `__init__` |
| SCC[2] | `interfaces.api.session.{loop_events, service}` | 互取对方私有 helper |
| SCC[2] | `kernel.agent.sandbox{.__init__, static_sandbox}` | `static_sandbox.py:19 from . import legacy` 穿透 `__init__` |

**关键结论：跨包 eager 环 = 0。** 任意两个包之间不存在 import 期死锁。这是分层迁移的实打实成果——
迁移前 `core/` 一坨时不可能达到这个状态。

### 2.4 环的种子：13 处 `from . import <name>`

这 4 个 eager 环里有 2 个直接由这个反模式造成，全仓共 13 处：

```
apps/study/runner.py:23              from . import budget
apps/study/runner.py:24              from . import state_utils
apps/study/scheduler.py:583          from . import state_store
apps/study/scheduler.py:612          from . import round_manifest
apps/study/bootstrap.py:18           from . import scenario_router
kernel/agent/sandbox/static_sandbox.py:19   from . import legacy
kernel/agent/context_injector.py:255 from . import loop
kernel/agent/compaction_runtime.py:24 from . import compact
kernel/agent/loop_compaction.py:13   from . import compact
kernel/agent/loop_compaction.py:14   from . import loop
kernel/agent/executor.py:237,275     from . import exec_registry
apps/strategy_acceptance/cli.py:21   from . import decide, load_config
```

`from . import X` 语义是「从包对象取属性 X」→ 必然先完整执行 `__init__.py` → 而 `__init__.py`
又 import 了本模块 → 环。**改成 `from .budget import ...`（直达子模块）即可打断，
零行为变化。** 这是本次评审里性价比最高的改动：13 行、零风险、消掉 2 个真环。

---

## 3. 三处结构性欠账

### 3.1 欠账一：L1 里驻扎着 5.9k 行平台机制

`interfaces/api/session/` 有 **5,924 行**（= interfaces 的 20.1%），14 个文件：

| 文件 | 行 |
|---|---:|
| `service.py` | 1,450 |
| `projection_handlers.py` | 791 |
| `web_session_db.py` | 706 |
| `backfill_event_log.py` | 371 |
| `history_convert.py` | 358 |
| `projection_store.py` / `store.py` | 354 / 352 |
| 其余 6 个 | 1,142 |

**它是"哑适配器"吗？不是。** 依赖画像：

```
api/session 依赖 kernel      15 处
api/session 依赖 foundation  10 处
api/session 依赖 apps         4 处
```

`service.py`（1,450 行）依赖 `kernel/agent` ×5、`kernel/workflow` ×1、`foundation/llm` ×2
——这是**会话编排机制**，不是 HTTP 适配。而 `projector.py` 与 `web_session_db.py` 各被
`kernel/` 反向引用 1 次（`event_store.py:610`、`memory_manager.py:160`），
构成上面 SCC[3] 那个真环。

**这是 L1 判据的失效**：L1 的定义是"哑适配器，只做协议转换"。但 `api/session/` 做的是
投影、事件回填、会话编排——**平台机制错放在入口层**。它应该下沉到 `kernel/session/`
（现有的 4 个文件、407 行的正确位置），只让 `routers/` 留在 L1。

顺带暴露：`routers/` 与 `session/` 双向互引 35 处（session→routers 6 · routers→session 29），
且 `session/ service.py` 取的是 `routers/chat.py` 的 **私有** helper `_build_llm_config`、
`routers/web_session.py` 的 `_get_db`——**L1 内部靠私有函数互相缠绕**。

### 3.2 欠账二：70% 的上行债压在一个目录

L3→L2 的 46 条语句分布在 13 个文件，但**极度倾斜**：

| 文件 | 条数 |
|---|---:|
| `kernel/agent/builtin_tools/factor_tools.py` | 11 |
| `kernel/agent/builtin_tools/data_tools.py` | 8 |
| `kernel/agent/builtin_tools/backtest_tools.py` | 4 |
| `kernel/agent/builtin_tools/data_clean_tools.py` | 4 |
| `kernel/agent/builtin_tools/goal_tools.py` | 4 |
| `kernel/agent/context_injector.py` | 3 |
| `kernel/swarm/runtime.py` | 3 |
| 其余 6 个文件 | 9 |

`builtin_tools/` 一个目录贡献 **32/46 = 69.6%**。

这其实**印证了 ADR-003 的判断是对的**：这不是"kernel 偷偷依赖 apps"，而是
「**工具实现本身就是研究域能力**」——`compute_factor`、`run_backtest`、`clean_data`
这些工具天然要调 L2 的业务件。真正的解法是 ADR-003 已记录的 CapabilityRegistry 端口化
（L3 定义端口，L2 注册实现），而不是把这些工具搬去 apps。

**所以 46 条不该被读成 46 个问题，它是一个问题（端口化）的 46 次投影。**

### 3.3 欠账三：53 模块的概念耦合团没有断口

SCC[53] 把 `apps.goal / apps.study / apps.autoresearch / kernel.agent.loop 系 /
kernel.workflow / kernel.swarm / kernel.gateway / interfaces.api` 绑成一团。
里面有几条明确的**高权重耦合边**（按符号级边计）：

| 边 | 符号级边 | 性质 |
|---|---:|---|
| `kernel.workflow → kernel.swarm` | 37（`AgentResult` ×28） | **契约错位**：`AgentResult` 定义在 `swarm/runtime.py:70`，却是 workflow 的核心数据 |
| `apps.study → kernel.agent` | 27 | 研究域用 agent 能力（合法） |
| `interfaces.api → apps.study` | 39 | 入口调研究域（合法） |
| `kernel.gateway → kernel.orchestration` | 42 | **同层内最高**，网关与编排的边界模糊 |
| `kernel.gateway → kernel.runtimes` | 16 | 控制面直取执行面 |
| `kernel.agent.loop ↔ loop_*`（4 模块） | 双向 | Mixin 反向依赖基类 |

**最干净的一刀是 `AgentResult`**：它定义在 `kernel/swarm/runtime.py`，但被
`kernel/workflow/{executor,graph_compiler,node_types,store,types,definition}.py` 6 个文件、
`apps/goal` 2 个文件引用——**使用面远大于定义面**。把它移到
`kernel/workflow/types.py`（现有 12 行，已是 `AgentCall/AgentStatus/SwarmHook` 的
re-export 契约位）或新建 `kernel/contracts/`，`workflow → swarm` 的 28 条边立刻消失，
`swarm → workflow` 只剩 1 条（`runtime.py:23` 的 `topological_layers`，是真需要）。

这是与 W4b（`foundation/protocol.py` 下沉）完全同构的修法，且已验证可行。

---

## 4. 分层设计本身的评审

### 4.1 判据成立的

| 判据 | 证据 |
|---|---|
| foundation 零业务知识 | 上表 L4 行全零；晋升门槛（去 run/session/研究语义）成立 |
| 依赖方向单向 | 跨包 eager 环 = 0；任意两包无 import 死锁 |
| kernel 三子带划分可用 | 控制面 Ce 33/Ca 144 · 执行面 Ce 12/Ca 93 · 能力面 Ce 243/Ca 176，**扇入扇出分布与"控制面被依赖、能力面重依赖"的直觉一致** |
| 深度保持策略有效 | 迁移后包内相对导入零改写，`__file__` 定位仅 6 处需修 |

kernel 三子带的实测数据值得记一笔：

| 子带 | 包数 | Ce | Ca | 解读 |
|---|---:|---:|---:|---|
| 控制面 | 5 | 33 | 144 | 被依赖多、自己依赖少 → 稳定，符合"控制面"定位 |
| 执行面 | 3 | 12 | 93 | 同上，且最轻 |
| 能力面 | 5 | 243 | 176 | 重依赖（agent 241）→ 是耦合的源头，不是被依赖的底座 |

`kernel/context` 407 行、`kernel/memory` 1.5k 行这类"薄包"的存在是合理的——
它们是能力面的接缝件，不是为凑数。

### 4.2 判据失效的

**L1 = 哑适配器的判据已经不成立**（见 3.1）。`interfaces/api/` 29.5k 行里，
`routers/` 7.5k 是真适配器，`session/` 5.9k 是机制，其余 16k 是 `app/schemas/user_db/...`。

需要把 L1 判据从「目录位置」改成「**是否含状态/编排**」：
- 无状态、只做协议转换 → L1 ✅
- 持有 DB 连接、维护投影、启动后台任务 → L3

按这个判据，`api/session/` 应该整体下沉。这是一次边界重划，不是搬家。

### 4.3 一条被忽略的事实：`apps/backtest_engine` 是死代码

5 个文件 / 395 行，**外部引用 0 个**（全仓唯一"命中"是 `kernel/agent/tools.py:116` 的一句
注释，说 `backtest_engine` 要等 P0-3）。它有自己的 3 个测试文件（`test_backtest_engine.py`
等），所以不会在覆盖率里暴露为空洞。

同类：`apps/backtest_models.py` 只被 `backtest_engine/` 自己用（3 条边）。
**二者构成一个自闭合的孤岛。** 要么接线，要么删；挂着就是持续的认知成本。

---

## 5. 分层之外的两个数字异常

1. **apps 31 包**，但 `alpha_zoo` 一个占 41.2% 的 LOC。31 个"包"里真正有分量的不到 12 个，
   剩下是 `data_import.py`、`preflight.py`、`run_card.py` 这类单文件"包"。
   包数作为进度的度量在这里失真——建议用「**每包 ≥300 行**」作统计口径，
   得到实际约 12 个实质模块。

2. **48 个 `__init__.py` 里有 301 条再导出 import**，最多的是
   `interfaces/cli/__init__.py`（60 条 / 1,112 行）。每一个都是 §2.4 那个环种子的潜在位点。
   `cli/__init__.py` 有 1,112 行本身就说明它**不是包初始化，是一个模块**。

---

## 6. 建议的处理顺序

按「收益 / 风险」排序，前两项可以直接做，后两项需要设计：

| # | 事项 | 规模 | 风险 | 收益 |
|---|---|---:|---|---|
| **1** | 13 处 `from . import X` → `from .X import ...` | 13 行 | 极低（纯语法，零行为变化） | 消掉 **2 个 eager 真环** |
| **2** | `AgentResult`/`SwarmPreset` 下沉到 `kernel/workflow/types.py` 或 `kernel/contracts/` | ~30 行 + 6 处 import | 低（与 W4b protocol.py 同构，已验证） | 消掉 **28 条 workflow→swarm 边**，SCC[53] 缩小 |
| **3** | `interfaces/api/session/` 5.9k 行下沉到 `kernel/session/`，只留 `routers/` 在 L1 | 14 文件 | 中（需重划 L1/L3 判据，35 处互引要先解） | L1 判据恢复成立；消掉 SCC[3] |
| **4** | CapabilityRegistry 端口化（ADR-003 正题） | 大 | 高 | 46 条 L3→L2 清零 |
| **5** | `apps/backtest_engine` 孤岛：接线 或 删除 | 395 行 | 低 | 减认知负担 |

**建议先做 1 + 2 + 5**（一次提交完成，风险极低），把「eager 环归零 + 高权重耦合边消失 +
死代码清理」拿下。3 和 4 属于设计变更，各自单独立项。

---

## 7. 文档同步

本次评审产出两条需要回写既有文档的结论：

1. **`scripts/README.md`** — 加一条工具选型规则：graphify 用于拓扑/热点，
   `test_layer_matrix` 用于上行违规判据，**禁止用 graphify 计数替代门禁基线**。
   附 72.5% lazy 覆盖率的实测依据。
2. **`docs/layered-skeleton-design-20261103.md` §2** — L1 判据补一句修正：
   L1 的判据不是"目录在 `interfaces/` 下"，而是"**无状态、无编排、只做协议转换**"；
   按此判据 `api/session/` 归属存疑，记入待办。

---

## 附：本次用到的探针（`tmp/`，可按需固化）

| 脚本 | 用途 |
|---|---|
| `w9_struct_audit.py` | 包级耦合 / 扇入扇出 / 不稳定度 |
| `w9_calibrate.py` | 三种度量口径下的层矩阵对照 |
| `w9_final_scc.py` | **eager / lazy 双图 SCC**（核心方法） |
| `w9_coverage.py` | graphify 对 lazy import 的覆盖率量化 |
| `w9_setdiff.py` | **门禁 vs graphify 违规集合逐条比对**（推翻"46=46"） |
| `w9_mod_scc.py` | 模块级 SCC（包级假环的证伪工具） |
| `w9_runtime.py` / `w9_cycle_runtime.py` | 导入顺序矩阵（运行时反证假环） |

其中 `w9_final_scc.py`（eager/lazy 双图）和 `w9_setdiff.py`（集合比对）建议固化进
`scripts/arch/`——它们填补了"包级假环"与"计数巧合"两个容易骗人的坑。
