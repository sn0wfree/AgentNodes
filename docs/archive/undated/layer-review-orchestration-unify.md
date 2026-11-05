# 编排层讨论：三个编排器能否统一？

> 关联：`layer-review-orchestration-engine.md`（编排层+引擎层发现）· `architecture-diagram.html` · `refactor-master-plan-20261008.md`
> 证据：`scripts/orch_probe.py` · `scripts/orch_probe2.py`（只读探针）
> 性质：讨论记录 + 提案（待拍板 3 条见 §6）

## 1. 结论

| 命题 | 判定 |
|---|---|
| 三个编排部件**合并成一个** | ❌ **不应**——它们是**嵌套层级**，不是并列；合并 = 造上帝编排器 |
| 三个编排部件**统一成一个标准协议** | ✅ **可以**，但**统一的对象不是「编排」，是「运行期环境」** |

**关键事实**：三者的**驱动源根本不同**（等人 / 自主 / 等 LLM），把「自主研究」与「请求-响应」塞进同一抽象会丢语义。真正在重复实现的是**预算 / 事件 / 追踪 / 取消 / 快照**——今天四套并行。

## 2. 为什么不能合并成一部件（实测）

| | SessionService | AutoresearchRunner<br>`core/study/runner.py` | AgentLoop |
|---|---|---|---|
| 循环单位 | **消息** | **研究轮** | **ReAct 迭代** |
| 驱动源 | 用户请求（被动） | **自主**（停因驱动） | LLM 响应 |
| 持久化 | session sqlite | study sqlite | 无状态 |
| 结果类型 | `dict` | `str`（停因） | `LoopResult` |
| 取消语义 | `CancelledError`(5) | `ControlToken`(9) | 无(0) |

**嵌套关系（层级）**：

```
SessionService（会话循环） ──▶ AgentLoop（迭代循环）
AutoresearchRunner（轮循环） ──▶ phase_engine（一轮） ──▶ AgentLoop
```

AgentLoop 是叶子；SessionService 与 AutoresearchRunner **不是同类**。

## 3. 真重复：运行期环境四套并行（实测计数）

| 机制 | SessionService | AutoresearchRunner | AgentLoop | 现状 |
|---|---|---|---|---|
| 事件 emit | 24 | 23 | 7 | 各写各的 |
| 追踪 trace | 4 | 28 | 56 | 三套 |
| 预算 budget | 0 | 1 | 0 | 几乎不共享 |
| 取消 | `CancelledError` | `ControlToken` | 无 | 三套语义 |

**`ControlToken` 只在 study 侧（5 文件）；chat 侧用 `asyncio.CancelledError`；AgentLoop 侧没有取消概念。**

## 4. 两个附带发现

### 发现 A · chat 路径**没有预算门**（运行时风险，非结构债）

- `strategy/budget.py`（118 行）实现 tokens/cost/time 三维预算
- **SessionService 对 `budget` 提及 0 次；`chat_loop.py` 0 次**
- chat 路径只受 `max_iterations`(16) 与 `_CHAT_ATTEMPT_TIMEOUT`(8) 约束
- → **长会话可能烧 token/钱而无闸**（study 路径有闸）

### 发现 B · 命名与位置脱节

- `core/study/runner.py` 里的类叫 **`AutoresearchRunner`**——与文件名、层名都不一致
- 同名文件 `core/autoresearch.py`（1393 行）是**阶段函数包**，不是类
- `AutoresearchRunner` 被 20 个文件提到，但**没有一个是外部 API 调用点**（外部走 `integration.py` / `scheduler.py`）
- → 与「引擎层改名/拆目录」议题（E）合并处理

## 5. 提案：薄协议 + 厚环境

```python
# core/orchestration/context.py（新，中立层）
@dataclass
class RunContext:
    run_id: str
    kind: str                    # session | study_round | agent
    budget: BudgetView           # tokens/cost/time 统一一处记账
    events: EventSink            # 统一 emit → EventStore
    trace: TraceView
    cancel: CancelScope          # 统一取消/暂停语义
    snapshot: Callable[[], dict] # 统一快照（从事件 fold）

class Orchestratable(Protocol):
    """三个编排器的公共最小面——只承诺生命周期，不承诺语义。"""
    name: str
    def start(self, req: RunRequest) -> RunHandle: ...
    def cancel(self, reason: str = "") -> bool: ...
    def status(self) -> RunStatus: ...
```

**协议刻意只 3 个方法**——不承诺「怎么编排」。理由：
- 「自主研究」与「请求-响应」差异留在实现里，接口只保证**可启动 / 可取消 / 可查状态**
- 换 AgentLoop（研究别的 agent 形态）或换 study executor → 新实现满足同一面
- **预算/事件/追踪/取消只有一份实现**（这才是重复的消除）

**放哪**：`core/orchestration/`（中立层，编排/引擎/能力皆可用），**不塞进 `core/agent/`**（会变成 agent 私有）。

## 6. 待拍板（3 条）

| # | 问题 | 我的倾向 |
|---|---|---|
| **1** | 统一「运行期环境（RunContext）」而非「统一编排协议」 | **同意**（前者消除真重复，后者制造伪抽象） |
| **2** | `RunContext` 放 `core/orchestration/`？ | **是**（中立层，勿入 agent 私有） |
| **3** | chat 无预算门立刻补？ | **立刻补**（0.5d，是运行时风险不是结构债） |

## 7. 迁移路径（并入主线，不新开线）

| 刀 | 内容 | 估工 | 依赖 |
|---|---|---|---|
| **0.10** | `RunContext` 骨架 + 三处**只接线不改行为**（SessionService / AgentLoop 也拿到 budget·event·cancel 句柄） | 1.5d | — |
| **1.8** | **chat 路径补三维预算门** | 0.5d | 0.10 |
| **2.7** | 取消语义统一（`ControlToken` → 通用 `CancelScope`） | 1d | 0.10 |

> 0.10 的关键是**只接线、不改行为**——先让三者共用同一份环境实例，再逐步把「各自实现」替换掉。
