# 四层骨架设计 — llmwikify 借鉴 · 依赖方向机器可证

日期：2026-11-03　分支：`refactor/v6-gateway`　状态：设计稿 v3.1（层名定稿 · Gateway 归类 · kernel 三子带 · v7.2 归位）

用户方向：借鉴 llmwikify 评审（`D:/Github/llmwikify/docs/architecture/architecture-review-2026-11.html`）
的分层骨架——**四层单向依赖 + 原生数据传递关系 + 机器校验**。本文给出本项目的落地方案。

> **勘误（v1→v2）**：v1 文档引用的首次审计有脚本缺陷——相对导入按 PEP 328
> 解析时多剥一层，把大量包内引用误判为「未知」而跳过，得出「仅 5 处上行」的
> 过于乐观结论。修正解析后（`tmp/layer_audit.py` 当前版），真实基线为
> **107 处上行、聚成 4 个结构簇**。这恰好演示了「机器可证」的价值：同一份
> 代码，错误的证明工具给出 5，正确的给出 107——数字必须由可信的门禁产出。

---

## 1. 现状审计（2026-11-03 · 全库 AST · 解析修正版）

### 1.1 跨层 import 矩阵（行=调用方层，列=被调方层）

| 从 \ 到 | L1 interfaces | L2 domain | L3 runtime | L4 foundation |
|---|---:|---:|---:|---:|
| **L1 interfaces** | 1402 | 315 | 193 | 77 |
| **L2 domain** | **2** ⚠ | 6673 | 66 | 129 |
| **L3 runtime** | **3** ⚠ | **92** ⚠ | 1449 | 108 |
| **L4 foundation** | **0** ✅ | **4** ⚠ | **6** ⚠ | 338 |

正面事实（仍然成立）：
- **L4→L1 = 0**：基础设施从不 import 接口层。
- 研究域→平台 66 条边全部是既定 9 触点白名单语义（接缝契约已门禁）。

### 1.2 上行违规：107 条、4 个结构簇

| 簇 | 数量 | 构成 | 定性 | 归属 |
|---|---:|---|---|---|
| **A. 工具层绑定领域能力**（L3→L2） | **92** | `core/agent/builtin_tools` 59 · `context_injector`→goal 7 · `scheduled_research/cli`→study 6 · `swarm/runtime` 6 · `dag_config`→study.graph 4 · `exec_registry`→backtest 4 · `loop`→goal.store 4 · `executor`→study.bootstrap 2 | 不是错误，是「工具=运行时通往领域的桥」的结构形态；直接 import 使 CapabilityRegistry（L2 统一注册 ⏳）失去意义 | **G2b**：CapabilityRegistry 端口化（roadmap 切 0.6/0.7 正题） |
| **B. 文件放错层**（L4→L2） | 4 | `core/utils/backtest_engine.py` · `strategy_engine.py` → backtest_models | 回测执行件住在 utils | **G2a**：归位 `core/backtest_engine/` |
| **C. 依赖倒置缺失**（L4→L3 + L3→L1 + L2→L1） | 9 | `llm/config`→agent.compact 2 · `llm/parser`→agent.structured_output 2 · `storage/platform_store`→orchestration.contracts 2 · `agent/event_store`→api.projector 1 · `agent/memory_manager`→api.web_session_db 2 · `study/scheduler`→api.routers.web_session 2 | 讨论纪要发现①的全部原条目 + structured_output 放错层 | **G2a**：注册反转 / 下沉 / 注入 |
| — | — | — | — | 合计 **107**（92+4+6+3+2） |

## 2. 层定义与成员表

四层全序（高层可依赖一切低层；同层自由；禁止上行）。**层命名 2026-11-03 用户拍板：采用 llmwikify 原名
interfaces / apps / kernel / foundation**（物理目录不动——`core/study` 等包位置不变，只有逻辑层名）：

| 层 | 名字 | 一句话 | 成员（物理位置 → 层） |
|---|---|---|---|
| **L1** | interfaces 入口 | 哑适配器：构造请求 + 呈现事件流 | `api/` · `cli/` · `core/mcp/` |
| **L2** | apps 研究域 | 我们的 IP：方法论 + 账本 + 市场数据 | `core/study` `goal` `hypothesis` `alpha_zoo*` `backtest*` `validation` `portfolio` `data_source` `data_import` `data_readiness` `db` `compute_factor` `factor_validate` `config_runner` `strategy_acceptance` `autoresearch` `engine`(回测引擎) `export` `run_card` `workspace_setup` `preflight` **`tools`**(研究数据工具实现 · v7.2 归位) · `templates/` |
| **L3** | kernel 平台机制（三子带 · v7.2） | 业务无关的编排与防护 | **控制面**（②Gateway 对应 · ~4.1k）：`gateway` `orchestration` `events` `session` `scheduled_research` ｜ **执行面**（③对应 · ~6.0k）：`runtimes`(GraphEngine) `workflow`(编译器) `swarm`(编译器) ｜ **能力面**（AgentLoop 域 · ~24.6k，agent 22k 独大）：`agent` `context` `hooks` `permission` `memory` `skills` `seq_generator`(待退役) |
| **L4** | foundation 基础设施 | 零业务知识的机制件 | `core/llm` `storage` `observability` `utils` `web`(搜索机制) **`registry`**(通用注册基类 · v7.2 归位) `config_loader` `errors` `git` |

### 2.1 Gateway 组件归类（2026-11-03 用户要求 · 逐一裁定）

控制面 = **kernel 为主 + 一个 apps 寄宿件**。归层依据是「知道什么」：知道 run/session/协议 = kernel；
知道研究语义（停因/directive/方法论）= apps。

| Gateway 组件 | 层 | 裁定理由 |
|---|---|---|
| 协议端点（薄·无状态） | **kernel** | kernel 的前门：契约校验 + 幂等去重 + 转发 mailbox；不知道任何研究语义 |
| mailbox | **kernel** | 纯排队机制（session lane / cap20 / steer / 老化 / deadline） |
| SessionController | **kernel** | turn 准入 + P-4 身份 + fence + 4 队列模式——全是 run 语义，无研究语义 |
| 容量组闸 ⏳ | **kernel** | 并发闸机制（chat 宽/study 窄/cron 隔离） |
| ModelHub | **kernel** | provider/model 解析唯一入口（底层协议在 foundation.llm） |
| ContextHub | **kernel** | 装配机制 collect→arbitrate→render；业务 Provider 的数据由 apps 侧供给 |
| EventStore | **kernel** | 事件语义（emit/subscribe/replay/PROTOCOL_VERSION）；其下的 SQLite/连接原语在 **foundation.storage** |
| Scheduler（唯一）⏳ | **kernel** | automation/cron 调度机制 |
| RunContext | **kernel** | per-run 约束包（budget 快照/sink/cancel/steer_inbox/trace 的 state 键） |
| **Director** | **apps** ⚠ | 停因→动作、propose_next、study.directive——**研究语义策略件**。物理位置本就在 `core/study/director.py`（L2），由 **runner（apps）在轮末调用**，不违反 kernel→apps 禁令；它出现在 Gateway 叙事里是「编排位置」而非「依赖层」——控制面经事件流消费其产出 |

**两条轴的澄清**（归类时最容易混的点）：
- **编排位置 ≠ 依赖层**。Director 画在 Gateway 里（控制面叙事的「策略」格，决策 3），
  但它的层归属是 apps——控制面是它的**展示位**，不是它的**依赖目标**。
- **kernel 不得 import apps** 是硬禁令（门禁簇A 的 92 条就在修这个）；apps 寄宿在
  kernel 组件中的唯一合法形态 = kernel 只见契约/事件，不见实现。

**存疑成员的裁决**（审计时定位；v7.2 复核逐包内容后修正两处）：
- `core/mcp` 物理在 core/ 但它是工具协议入口 → L1（成员表解决物理与逻辑错位）。
- `core/web`（web 搜索后端机制，无 UI）→ **L4**（v1 文档误归 L1，已更正）。
- `templates/`（策略模板，研究领域内容）→ **L2**（v1 文档误归 L1，已更正）。
- `core/tools`（data_clean / data_transforms，研究数据工具实现）→ **L2 apps**（v7.2 修正：v1 误归 kernel——「agent 用它」≠「它是 agent 框架件」；grep 证实仅 config_runner/data_readiness/db 三个 apps 侧文件引用，归位违规中性）。注意与 `core/agent/tools`（BaseTool/ToolContext 框架，L3）是两个东西。
- `core/registry.py`（Generic typed Registry 基类，DSH 式）→ **L4 foundation**（v7.2 修正：零语义原语；唯一引用方 agent/builtin_tools/subagent_tool 为 L3→L4 下行，合法）。
- `core/hooks`/`permission`/`memory`/`events` 复核后**留在 kernel**：分别带 agent 生命周期 / 工具审批 / 跨会话 / 事件信封语义——下沉会让 foundation 假厚。
- `core/engine`（engine/base.py 回测引擎，含 log_stall 数据通道）是研究执行件 → L2，不是 GraphEngine 所在层。
- `core/scheduled_research` 是 automation 调度（平台机制，经 Gateway 提交）→ L3；包内 `cli.py` 是入口皮肤，簇 A 已计入。
- `core/db`（market.duckdb 工具）= v6.5 ⑤ 层研究域数据面 D-3 → L2。
- 顶层 `strategy_research/__init__.py` 与 `core/__init__.py`：re-export 门面，不参与方向断言。
- `scripts/`（dev 工具）不在门禁范围。

### 2.2 foundation 的晋升门槛与反向红线（v7.2）

**为什么 foundation 薄是对的**：全库 133.6k 行中 foundation 仅 10.8k（8%）——llmwikify 为 8.5k/80k ≈ 11%，同量级。
foundation 只收「零 run/session/研究语义」的纯原语；一个 agent 平台的大部分机制天然带 run 语义（这是产品形状，不是分类错误）。
**厚了才是病**：llmwikify 的 95 处击穿正是「把带语义的件放进了低层」导致 Protocol 形同虚设。

**晋升触发条件**（什么件够格下沉到 foundation）：
1. 去掉 run/session/研究语义后可作为独立库使用；
2. 现有候选（触发即动）：`ToolContext` 把 session/workspace 注入改为纯参数后 → foundation；
   `hooks` 若泛化为通用回调合成器（llmwikify 先例：callback 13 钩子在 foundation）→ foundation。

**反向红线**：禁止为了「层数平衡」把带 run/agent 语义的件推进 foundation——foundation 假厚比真薄更糟（语义漏下去 = 击穿的开端）。

## 3. 依赖矩阵与数据契约（箭头传什么）

| 箭头 | 允许 | 传递的契约（已存在） |
|---|---|---|
| L1→L2/L3/L4 | ✅ | **下行**：`WorkRequest`（E1，kind/source/session_key/payload/queue_mode/budget/idempotency_key/deadline）· `RunHandle` receipt 两态。**上行**：SSE 事件流（事件信封 = PROTOCOL_VERSION + 词汇表）· receipt/poll |
| L2→L3/L4 | ✅ | `BranchContext`/`RoundOutcome`（orchestration.contracts）· RunContext state 键（run_budget/sink/cancel/steer_inbox/trace）· `emit` 事件 · P-3 工件登记 + P-5 指针 · budget_gate · ContextHub collect/render |
| L3→L4 | ✅ | LLM client 协议（OpenAICompatClient/LLMSpec）· storage 原语（resolve_db_path/connect/PlatformStore）· trace/logging（configure_logging/dual_log） |
| L3→L2 | **临时白名单（92 条）** | 工具/注入器 → 领域能力；G2b 收敛为 `CapabilityRegistry.resolve(kind, **criteria)` 端口（roadmap 切 0.6/0.7），收敛后白名单清零 |
| L4→任何 / 任何→L1 | ❌（现违规 13 条，簇 B/C） | foundation 不得知道 run/session/event 的语义；入口层是末端 |

（表中 L2=apps 研究域 · L3=kernel 平台机制，命名见 §2。）

## 4. 机器门禁（check_architecture.py 的等价物）

新增 `tests/test_layer_matrix.py`：

1. **成员表**：§2 的表在门禁内为唯一事实源（dict，长前缀匹配）。
2. **方向规则**：AST 解析全库 import（相对导入按 PEP 328：锚=包，剥 `level-1` 层），断言 `target_layer_index >= importer_layer_index`（同层或向下）。
3. **基线棘轮**：分簇基线 `{"L3>L2": 92, "L4>L3": 6, "L4>L2": 4, "L3>L1": 3, "L2>L1": 2}`，只许下降不许上升（同 c1_gate 哲学）；G2a/G2b 每清一簇就下修基线，最终到 0。
4. **KPI 输出**：跨层边矩阵打印 + 违规总数，供图 v7 引用。
5. 与既有 16 道 test_architecture_gates 的关系：矩阵是**全局总表**，16 道是**局部细则**（如 gateway 零 FastAPI、method→engine 禁）——并存，细则不重复矩阵已覆盖的方向断言。

## 5. 与既定规则的关系（不推倒重来）

- **不违背 2026-10-26 三条落位规则**：业务域目录不动；gateway 零 FastAPI；orchestration 只放契约。骨架 = 契约 + 门禁先行，除簇 B 的 2 个文件外**零物理搬家**。
- **物理四目录重组（src/interfaces/…/foundation/）明确不做**：llmwikify 评审自己的结论就是「骨架是对的、问题在接缝」「不必推倒重来」；改 8,000+ 条 import 路径收益为零风险巨大。若未来要物理化，按 target-directory-structure 的刀号逐刀走。
- llmwikify 的接缝教训（存储被 95 处直连击穿）对本项目的提示：**簇 A 的 92 条边就是我们的「95 处直连」**——形态一模一样（Protocol/Registry 声称可替换，调用点却直接 import 实现）。G2b 用 CapabilityRegistry 端口化收口，是本轮借鉴的核心正刀。

## 6. 实施批次

| 批 | 内容 | 验收 |
|---|---|---|
| **G1** | `tests/test_layer_matrix.py` 落地：成员表 + PEP 328 正确解析 + 分簇基线棘轮（107）+ KPI 矩阵打印 | 门禁红/绿行为正确；基线=当日审计值 |
| **G2a** | 快赢 15 条：簇 B 两个文件归位；event_store→Projector、memory_manager→web_session_db、study/scheduler→web_session 三处注册反转/注入下沉；platform_store→check_event_version 下沉事件信封版本；llm/parser→structured_output 下沉；llm/config→compact 解耦 | 基线降到 92；受影响模块测试绿 |
| **G2b** | CapabilityRegistry 端口化收簇 A（roadmap 切 0.6/0.7 的正题，大刀单独规划） | 基线降到 0 |
| **G3** | 图 v7 体检页：矩阵上墙 + 每层挂 LOC/文件数/覆盖率 + violation KPI hero（形式借 llmwikify 评审页） | 图与门禁同源（KPI 从门禁脚本生成） |

估工：G1 半天 · G2a 一天 · G2b 单独规划（3–5d，随切 0.6/0.7）· G3 半天。
