# 四层骨架设计 — llmwikify 借鉴 · 依赖方向机器可证

日期：2026-11-03　分支：`refactor/v6-gateway`　状态：设计稿（待拍板实施）

用户方向：借鉴 llmwikify 评审（`D:/Github/llmwikify/docs/architecture/architecture-review-2026-11.html`）
的分层骨架——**四层单向依赖 + 原生数据传递关系 + 机器校验**。本文给出本项目的落地方案。

先说结论：**骨架已经存在，缺的是「可证」**。对全库 3,056 条跨层 import 的 AST 审计
（`tmp/layer_audit.py`，2026-11-03）显示：按四层全序衡量，**上行违规仅 5 处**。
llmwikify 的 `check_architecture.py 0 violation` 我们用一天可以拿到。

---

## 1. 现状审计（2026-11-03 · 全库 AST）

### 1.1 跨层 import 矩阵（行=调用方层，列=被调方层）

| 从 \ 到 | L1 interfaces | L2 domain | L3 runtime | L4 foundation |
|---|---:|---:|---:|---:|
| **L1 interfaces** | 1208 | 89 | 60 | 42 |
| **L2 domain** | **0** ✅ | 5822 | 104 | 56 |
| **L3 runtime** | **1** ⚠ | **0** ✅ | 849 | 7 |
| **L4 foundation** | **0** ✅ | **2** ⚠ | **2** ⚠ | 188 |

关键事实：
- **L2→L1 = 0**：研究域从不 import 接口层。
- **L3→L2 = 0**：平台机制从不 import 研究域——2026-10-30 接缝加固（9 触点白名单）的直接成果。
- L2 内部 5,822 条边：同层自由（study↔goal↔alpha_zoo 互引合法）。

### 1.2 全部 5 处上行违规（修复清单）

| # | 位置 | 方向 | 定性 | 修法 |
|---|---|---|---|---|
| V1 | `core/agent/event_store.py:610` → `api.session.projector.Projector` | L3→L1 | 讨论纪要发现①的原条目，至今未清 | 反转为注册制：api 层启动时把 Projector 注入 event_store（同 `register_compaction_persister` 模式） |
| V2 | `core/storage/platform_store.py:385` → `core.orchestration.contracts.check_event_version` | L4→L3 | 事件信封版本校验放错了层 | `check_event_version`/`PROTOCOL_VERSION` 下沉 foundation（随 event schema 进 `core/storage/`），orchestration.re-export 保持兼容 |
| V3 | `core/utils/backtest_metrics.py:14` → `core.backtest_models` | L4→L2 | 文件放错层：回测指标是领域数学不是通用工具 | `backtest_metrics.py` 移入 L2（`core/backtest_engine/` 或 core 根域位），utils 留 re-export 一版过渡 |

## 2. 层定义与成员表

四层全序（高层可依赖一切低层；同层自由；禁止上行）：

| 层 | 名字 | 一句话 | 成员（物理位置 → 层） |
|---|---|---|---|
| **L1** | interfaces 入口 | 哑适配器：构造请求 + 呈现事件流 | `api/` · `cli/` · `core/mcp/` · `core/web/` · `templates/` |
| **L2** | domain 研究域 | 我们的 IP：方法论 + 账本 + 市场数据 | `core/study` `goal` `hypothesis` `alpha_zoo*` `backtest*` `validation` `portfolio` `data_source` `data_import` `data_readiness` `db` `compute_factor` `factor_validate` `config_runner` `strategy_acceptance` `autoresearch` `engine`(回测引擎) `export` `run_card` `workspace_setup` `preflight` |
| **L3** | runtime 平台机制 | 业务无关的编排与防护 | `core/gateway` `orchestration` `runtimes` `workflow` `context` `agent` `swarm` `events` `session` `memory` `skills` `tools` `hooks` `permission` `registry` `seq_generator` `scheduled_research` |
| **L4** | foundation 基础设施 | 零业务知识的机制件 | `core/llm` `storage` `observability` `utils` `config_loader` `errors` `git` |

命名对照 llmwikify：它的 `apps`↔我们的 `domain`（业务层=研究域）、`kernel`↔我们的 `runtime`（框架层=平台运行时）。
不采用它的目录名——我们是研究工具，业务层叫 domain、机制层叫 runtime 更诚实。

**存疑成员的裁决**（审计时定位）：
- `core/mcp` 物理在 core/ 但它是工具协议入口 → L1（成员表解决物理与逻辑错位）。
- `core/engine`（engine/base.py 回测引擎，含 log_stall 数据通道）是研究执行件 → L2，不是 GraphEngine 所在层。
- `core/scheduled_research` 是 automation 调度（平台机制，经 Gateway 提交）→ L3；它内含 cli.py 属入口皮肤，成员表按包取层，包内 cli 模块在门禁中按 L1 处理（豁免清单）。
- `core/db`（market.duckdb 工具）= v6.5 ⑤ 层研究域数据面 D-3 → L2。
- `scripts/`（dev 工具）不在门禁范围。

## 3. 依赖矩阵与数据契约（箭头传什么）

| 箭头 | 允许 | 传递的契约（已存在） |
|---|---|---|
| L1→L2/L3/L4 | ✅ | **下行**：`WorkRequest`（E1，kind/source/session_key/payload/queue_mode/budget/idempotency_key/deadline）· `RunHandle` receipt 两态。**上行**：SSE 事件流（事件信封 = PROTOCOL_VERSION + 词汇表）· receipt/poll |
| L2→L3/L4 | ✅ | `BranchContext`/`RoundOutcome`（orchestration.contracts）· RunContext state 键（run_budget/sink/cancel/steer_inbox/trace）· `emit` 事件 · P-3 工件登记 + P-5 指针 · budget_gate · ContextHub collect/render |
| L3→L4 | ✅ | LLM client 协议（OpenAICompatClient/LLMSpec）· storage 原语（resolve_db_path/connect/PlatformStore）· trace/logging（configure_logging/dual_log） |
| L4→任何 | ❌（现违规 3 处，见 §1.2） | foundation 不得知道 run/session/event 的语义 |
| 任何→L1 | ❌（现违规 1 处，V1） | 入口层是末端，无下游可调 |

## 4. 机器门禁（check_architecture.py 的等价物）

新增 `tests/test_layer_matrix.py`：

1. **成员表**：§2 的表在门禁内为唯一事实源（dict，长前缀匹配）。
2. **方向规则**：AST 解析全库 import（相对导入解析为绝对），断言 `target_layer_index >= importer_layer_index`（同层或向下）。
3. **豁免白名单**：初始为空（审计后仅需修 3 个文件）；白名单条目必须带理由与到期刀号，冻结计数（同 c1_gate 哲学）。
4. **KPI 输出**：违规数 + 跨层边矩阵打印，violation=0 是目标态；测试在违规数 > 当前基线时红（先立基线再清零，避免一次性修不完卡门禁）。
5. 与既有 16 道 test_architecture_gates 的关系：矩阵是**全局总表**，16 道是**局部细则**（如 gateway 零 FastAPI、method→engine 禁）——并存，细则不重复矩阵已覆盖的方向断言。

实施注意（Windows/项目惯例）：AST 扫描复用 `tmp/layer_audit.py` 已验证的解析逻辑（相对导入 level 处理、__init__ 包归并）；模板/脚本目录排除。

## 5. 与既定规则的关系（不推倒重来）

- **不违背 2026-10-26 三条落位规则**：业务域目录不动；gateway 零 FastAPI；orchestration 只放契约。本设计 = 契约 + 门禁先行，**零物理搬家**。
- **物理四目录重组（src/interfaces/…/foundation/）明确不做**：llmwikify 评审自己的结论就是「骨架是对的、问题在接缝」「不必推倒重来」；我们的 8,000+ 条同层/下行 import 全部要改路径，收益为零风险巨大。若未来要物理化，按 target-directory-structure 的刀号逐刀走。
- llmwikify 的接缝教训（存储被 95 处直连击穿）对本项目的提示：L2 对 L3/L4 的 104+56 条边**不走白名单审批会再膨胀**——矩阵门禁 + 边计数冻结（同期 c1_gate 模式）就是防膨胀机制，后续可在图 v7 上挂边数 KPI。

## 6. 实施批次

| 批 | 内容 | 验收 |
|---|---|---|
| **G1** | `tests/test_layer_matrix.py` 落地：成员表 + 方向断言 + 基线（违规=5）+ 白名单机制 | 门禁红/绿行为正确；基线=当日审计值 |
| **G2** | 修 V1（注册制反转 Projector）· V2（check_event_version 下沉）· V3（backtest_metrics 归位） | 门禁基线降到 0；受影响模块测试绿 |
| **G3** | 图 v7 体检页：矩阵上墙 + 每层挂 LOC/文件数/覆盖率 + violation KPI hero（形式借 llmwikify 评审页） | 图与门禁同源（KPI 从门禁脚本生成） |

估工：G1 半天 · G2 一天 · G3 半天。
