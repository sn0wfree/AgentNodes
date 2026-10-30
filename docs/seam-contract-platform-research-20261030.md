# 接缝契约：平台 ↔ 研究域（2026-10-30）

> 状态：**分而不拆**（决策见本文 §5）。本文件是拆分就绪的判断依据——
> 当 §5 的触发条件出现时，按此文档可在 3-5 天内完成包/仓库拆分，
> 因为接缝已被门禁看守。
> 配套门禁：`tests/test_architecture_gates.py`
> 设计依据：`docs/seam-hardening-design-20261030.md`

---

## 1. 方向规则（一条）

```
   ┌─────────────────────────────────────────┐
   │  研究域（IP，可替换）                    │
   │  core/study · goal · hypothesis          │
   │  core/backtest · autoresearch            │
   └───────────────┬─────────────────────────┘
                   │  只许这 9 个触点 ↓
   ┌───────────────▼─────────────────────────┐
   │  平台（业务无关，可复用）                 │
   │  gateway · orchestration · runtimes      │
   │  context · storage · llm · memory        │
   │  agent（能力层）· observability · utils   │
   └─────────────────────────────────────────┘

   反向（平台 → 研究域）：**零引用，门禁强制**
```

平台严格子层（`gateway/orchestration/runtimes/storage/context/llm/memory`）
import 研究域五成员（`study/goal/hypothesis/backtest/autoresearch`）即测试失败。

`agent` 不在严格集：`builtin_tools/` 是能力适配器，合法包装域工具
（如 `run_backtest` → `core.backtest`）——组合根职责，不是反向依赖。

## 2. 研究域 → 平台的 9 个触点（白名单）

新增触点 = **有意决策**：改 `STUDY_PLATFORM_TOUCHPOINTS` + 在此登记理由。
门禁会拦住无声的耦合扩张。

| 文件 | 子层 | 用途 | 是否平台公共面 |
|---|---|---|---|
| `graph_preset.py` | runtimes | `GraphEngine` 阶段图编排 | ✅ 公共 |
| `phase_engine.py` | agent | `build_agent_loop`（能力工厂） | ✅ 公共 |
| `dag_planner.py` | agent | `AgentPlugin`/`Registry`/`DAGConfig` | ✅ 公共 |
| `runner.py` | agent, observability, utils | 能力工厂 / trace / bg_proc | ✅ 公共 |
| `runner_knowledge.py` | agent | 能力工厂 | ✅ 公共 |
| `scheduler.py` | gateway, orchestration, utils, observability | 统一入口适配器 / mailbox 契约 / bg_proc / 诊断事件 | ✅ 公共 |
| `scenario_router.py` | llm | 场景路由自建 provider 客户端 | ✅ 公共 |
| `state_utils.py` | observability | trace 上下文 | ✅ 公共 |
| `store.py` | storage | sqlite 工具（域自己的库） | ✅ 公共 |

**全部 9 个触点都只用公共面**——这是当前能说「拆分就绪」的核心事实。

## 3. 禁触清单（研究域不得依赖的平台内部）

| 禁触对象 | 原因 | 替代 |
|---|---|---|
| `agent/loop*.py` 的 mixin 与内部状态 | 执行原语内部，随实现演进 | 用 `build_agent_loop` 门面 |
| `agent/compaction_*` 内部 | 压缩策略会变 | 用 `SessionController(compact_config=...)` |
| `mailbox._queue` / `_steer` 等私有字段 | 队列实现细节 | 用 `poll_due/steer_inbox/drain_steer` |
| `session_controller._turn_counters` 等私有 | 控制器内部 | 用 `session_status()` / `wait_turn()` |
| `fence._active` 私有 dict | 栅栏内部 | 用 `claim/validate/release/active_run` |
| `platform_store._*_conn` 私有 | 连接管理 | 用 `emit/replay/register_artifact/iter_session_ids` |
| `llm/model_catalog` 内部表 | 模型目录实现 | 用 `ModelHub.resolve()` |

## 4. 数据面接缝（这条比代码接缝更硬）

研究域**不许私开存储通道**（v6.3 图接缝规则）：

| 数据 | 唯一通道 | 说明 |
|---|---|---|
| 运行事实 | `EventStore` / `PlatformStore.emit` | P-1：一切进展写成事件 |
| 大产物 | `ArtifactChannel.maybe_artifact` | P-3：>64KB 落盘 + 登记，事件带指针 |
| 身份 | P-4 四层派生 id | turn→run→子→artifact |
| 指针 | P-5 四件套 `{uri,sha256,bytes,schema_v}` | 根=workspace |
| 研究账本（goal/hypothesis/study） | **研究域自己的库**（goals.db） | 域内真源，不属平台数据面 |

`hanging_events` 曾放在 `core/study/`，但它服务平台防护层（LLM 墙钟、
agent 熔断）——已迁至 `core/observability/`。**判断标准：消费方在哪一层，
模块就属于哪一层**，不能因为数据落在 goals.db 就归给研究域。

## 5. 拆分触发条件（满足任一才启动）

| # | 条件 | 量化 |
|---|---|---|
| 1 | 第二个产品想用这个 agent 平台 | 出现即拆 |
| 2 | 平台与研究需要独立发布节奏 | 平台连续 2 个月零改动而研究高频迭代 |
| 3 | ≥2 人分工，各自 owner 一侧 | 团队扩员（原决策 7 的重开条件） |
| 4 | 平台协议面稳定 | 9 个触点连续 N 个月签名不变 |

**当前判定：不拆**。理由：平台还没有第二个消费者（公共 API 的形状需要
≥2 个消费者校准）；单人项目（双仓库的同步开销换不来团队边界收益）；
数据面刻意共享（B4 定案买的是零 UI 迁移+回放一切）。

## 6. 触发后怎么拆（预期 3-5 天）

| 步 | 内容 |
|---|---|
| 1 | 建 `platform` 命名空间包，把 7 严格子层 + agent/observability/utils 搬入 |
| 2 | 研究域改为依赖 `platform` 包的公共面（9 个触点已全部合规） |
| 3 | `api/` 按路由拆分：chat 归平台、study 归研究 |
| 4 | `cli/webui` 拆分：TUI 归平台；study 面板/命令归研究 |
| 5 | 测试搬迁：100 个 R/B 系列测试大多走 Gateway（平台侧） |
| 6 | **前置**：每 session 分库（D2）——数据自包含后才是真隔离 |

第 6 步是关键：**代码拆了但共用一个 SQLite，就不是真的两个项目**。
所以 D2（分库）其实是大拆分的前置，而非可选项。

## 7. 挂起项与本文档的关系

| 挂起项 | 关系 |
|---|---|
| P5-1c 删旧 chat 分支 | 在接缝附近（chat 路由），拆分前应清掉 |
| 方法论归位 `core/study/method/` | 纯域内搬家，不影响接缝 |
| 解 `phase_engine↔runner` 循环导入 | 域内解耦，会让接缝更清晰 |
