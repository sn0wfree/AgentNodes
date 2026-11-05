# 逐层讨论记录：编排层 + 引擎层

> 性质：**讨论记录 + 实测发现**（非决策文档；待拍板项见 §5）
> 关联：`architecture-diagram.html`（对照讨论图）· `architecture-v2-and-rsi-20261008.md` · `refactor-master-plan-20261008.md`
> 证据：`scripts/layer_probe.py` · `scripts/engine_probe.py`（只读探针）

## 1. 两个新发现（与既有架构图有出入，需修图）

### 发现 1 · AgentLoop 内部有两条正交拆解轴，职责一一重叠

| mixin（按功能组，W5 拆 loop.py） | 行数 | 9 step（按控制流点，L7） | 关系 |
|---|---|---|---|
| `loop_stream` | 261 | `llm_call` | 同一件事 |
| `loop_tools` | 389 | `tool_execution` | 同一件事 |
| `loop_compaction` | 272 | `compaction` | 同一件事 |
| `loop_progress` | 270 | `progress` + `resilience` | 同一件事 |
| `loop_lifecycle` | 123 | `finalization` | 同一件事 |
| `loop_assembly` | 82 | `pre_run` | 部分重叠 |
| `loop_telemetry` | 93 | （无对应 step） | 基础设施：hooks / trace |

**7 个 mixin 里 6 个与 step 一一对应**；且 step 是薄壳——`execute` 里就一行 `await loop._amaybe_compact(...)` 之类，**真正的逻辑全在 mixin，step 只决定「何时调」**。

**后果**：改一个行为要看两处（step 定何时、mixin 定怎么做）；`bind_agent_loop` 机制说明 step 本质是「回调 mixin 方法」。

**step 的真价值**（不应丢）：**控制流可替换**——换 step 组合 = 换 agent 行为。

**图上的失真**：现有架构图只画了 9 steps，没画 mixin 重叠 → 需补。

### 发现 2 · 「双引擎 + 双契约」在代码里是「一个文件的两条分支」

- `RoundOutcome` / `BranchContext` **定义在 `phase_engine.py`，且全库只在该文件出现（13 处）**；`runner.py` 从未 import
- `_run_langgraph_branch` 与 `_run_phases_branch` **同在 `phase_engine.py`**
- `langgraph_engine.py`（810 行）只被 2 个文件引用（`phase_engine.py` + `runner_langgraph.py`）
- `phase_engine.py` 只被 3 个文件引用

→ **契约目前是「文件内契约」，尚未成为「模块间契约」**。它是为 langgraph 分支独立预留的（正确），但今天不绑定两个组件。

## 2. 编排层 · 三个待议点

| # | 议题 | 事实 | 建议 |
|---|---|---|---|
| **A** | 双层壳（mixin / step）要不要合并？ | 见发现 1 | **不合并**，但写死命名：mixin = 实现层、step = 策略槽位；随 1.1/1.7 迁出「贡献方/装配」类职责后，剩余一一对应的 mixin 再触达式合并 |
| **B** | SessionService 职责过载 | 1450 行 / 24 方法 / 4 种职责（队列 + attempt 生命周期 + Agent 运行 + 压缩摘要） | 拆 `QueueService` / `AttemptRunner` / `HistoryCompactor`（主线 2.4，**建议排在 study 路由之前**——与自主性直接相关） |
| **C** | 「四套编排」口径不实 | StudyRunner 1175（轮循环）· Swarm 756（DAG）· **Goal 1773 是账本** · **Workflow 仅 store 336 + orchestrate_tool 187 → 是「提交 DAG 的工具」** | 文档口径改为「**两个编排器（轮循环 / DAG）+ 一个账本（goal）+ 一个薄工具（workflow）**」 |

## 3. 引擎层 · 三个待议点

| # | 议题 | 事实 | 建议 |
|---|---|---|---|
| **D** | 契约的「家」放错在实现文件 | `RoundOutcome`/`BranchContext` 与实现同居 `phase_engine.py` | **提到 `core/study/contracts.py`**——**唯一「现在做不亏」的引擎动作**：一次成本，未来 langgraph 分支若独立成模块，边界已就位；否则迟早「import 实现模块当契约用」长出反向依赖 |
| **E** | 「引擎层」装的是研究方法论，名字不符 | runner 决定**停不停**（ShutdownReason 22 · max_rounds 13 · stagnation 7）；engine 决定**学到什么**（novelty 13 · claims 13 · journal 19 · review_cycle） | `core/study/` 拆 `method/`（假设—证伪—AEGIS—journal，**项目真正的 IP**）+ `engine/`（phases / langgraph 执行器）；至少先改名 |
| **F** | langgraph 810 行值不值 | 独占能力只有 checkpoint 51 · interrupt 43 · HITL 20；phases 独占顺序阶段拆分 | **先不动**（HITL resume 是硬需求，合并风险高）；按 D 先独立契约，**为将来「二选一」留路** |

## 4. 引擎层与编排层的实际边界（供确认）

### 4.1 职责分布

```
编排层（runner）  →  什么时候停 / 要不要继续 / 预算是多少 / HITL 恢复
                    ShutdownReason(9) · max_rounds · early_stop · stagnation · discard

引擎层（engine）  →  这一轮怎么做 / 学到了什么
                    phases(顺序阶段) | langgraph(DAG+checkpoint)
                    novelty · claims · journal · review_cycle

账本（goal）      →  目标 / 证据 / 矛盾 跨轮持久
```

**方法论（假设—证伪—AEGIS）现在藏在 `phase_engine.py` 里 = 位置错误**（它既不是「引擎」，也不是「编排」，是研究方法本身）。

### 4.2 发现 ⑦ · 引擎层反向依赖编排层的 AgentLoop

| 文件 | 证据 |
|---|---|
| `core/study/phase_engine.py` | `run_agent` ×2 · `executor` ×2 |
| `core/study/langgraph_engine.py` | `AgentExecutor` ×5 · `AgentLoop` ×1 · `executor` ×13 |

→ **引擎层（层位更低）调用编排层里的 AgentLoop（层位更高）= 依赖方向倒置**，与已知的 `core→api` 反向依赖同族。

**含义**：AgentLoop 实际是**「能力」**（被编排层与引擎层共同调用的执行体），不是「编排」。它放在编排层是归属错误。

**建议**：把 AgentLoop（含 9 steps + mixins）移入**能力层**；编排层只留真正的控制循环（SessionService / AutoresearchRunner）。
这样六层的依赖方向就一致了：编排→引擎→能力→数据，AgentLoop 不再被下层反向依赖。

> 关联图：`architecture-diagram.html` v2 的红色虚线箭头与 ⑦ 编号。


## 5. 待拍板（两条）

| # | 问题 | 我的倾向 |
|---|---|---|
| **D** | 契约现在就独立出来吗？ | **该做**——零风险，且是唯一能「现在付出、未来解锁 langgraph 独立」的动作 |
| **E** | `core/study/` 拆 `method/` + `engine/`，现在做还是等引擎层真要动时做？ | **现在做**——研究方法论是本项目 IP，藏在 `phase_engine.py` 是位置错误；且是纯机械改名 + 目录迁移 |

## 6. 待改图（等拍板后一起改）

1. 编排层 AgentLoop 框内**补画 7 个 mixin 与 9 step 的对应关系**（现图失真）
2. 引擎层**把 phase_engine 一个框拆成「契约 / phases 分支 / langgraph 分支」三格**，并标注「契约目前是文件内契约」
3. 编排层**把 goal 从「编排」移出，标为「账本」**；workflow 标为「薄工具」
4. 数据层 goal/hypothesis 框与「方法论层」的归属需与 E 的决定一致
