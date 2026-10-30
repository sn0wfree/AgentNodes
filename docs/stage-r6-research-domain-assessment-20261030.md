# R6 评估：研究域与导演层 —— 分档落地（2026-10-30）

> 计划依据：`roadmap-next-20261030.md` R6（原估 4-6d：方法论归位、
> RunContext、Director、preset Tier 0 治理分级）。
> **勘查结论：四项风险与收益差异极大，必须分档。** 只有两项值得做，
> 一项应延后（无用户），一项应降级为「不做」（纯改名收益）。

---

## 1. 逐项勘查

### 1.1 方法论归位 `core/study/method/` —— ⏸ 延后（不是不做，是有前置）

**事实**：

| 项 | 证据 |
|---|---|
| `phase_engine.py` 36KB，16 个函数/类，全是 round 生命周期 | 大纲：`RoundOutcome`/`BranchContext`/`run_round_phases` + 12 个私有步骤 |
| **`phase_engine ↔ runner` 循环导入** | `phase_engine` imports `.runner`；`runner` imports `.phase_engine` |
| `graph_preset` 同时依赖两者 | `graph_preset` imports `.phase_engine` + `.runner` |
| 外部消费方 3 处 | `orchestration/branch_contracts.py`、`graph_preset.py`、`scenario_router.py`、`runner.py` |

**判断**：这不是「换个目录」——搬家必须先**解开 phase_engine↔runner 的
循环导入**，而这一步会动 runner 54KB 的调用面。收益是「目录名更贴合架构
图」，风险是研究主链回归。

**结论**：**延后**，前置条件写清：先拆循环导入（单独一刀，`method/` 与
`engine/` 的解耦本身就是那一刀），再谈目录归位。**本刀不做。**

### 1.2 RunContext —— ⏸ 不做（实质已存在，包对象是纯改名）

**事实**：图上 RunContext = 「预算快照 · EventSink · CancelScope ·
TraceView · 注入通道」。逐项核对，**全部已以散装 state 键存在**：

| RunContext 字段 | 现状 |
|---|---|
| 预算快照 | `state["run_budget"]`（`RunBudget`，fanout 会切片，`graph_engine.py:331-341`） |
| EventSink | 引擎构造注入 `sink`（`graph_engine.py:111`） |
| CancelScope | fanout 的 `threading.Event` + `cancel_timeout`（`graph_engine.py:330`） |
| 注入通道 | `state["steer_inbox"]`（R2.2 落地） |
| TraceView | `observability` + `emit_with_trace`（study 侧） |
| per-run 身份 | `state["run_id"]` / `state["session_id"]` / `state["fence_domain"]` |

**判断**：把 6 个散装键包成一个 `RunContext` 对象只改访问语法
（`state["run_budget"]` → `ctx.budget`），**零行为收益**，diff 覆盖
引擎 + 两个节点 + 全部相关测试。

**结论**：**不做**。图上的 RunContext 格改为「机制已存在（散装 state 键）」。
重开条件=出现第 7 个约束需要贯穿时（再散装就真的失控了）。

### 1.3 Director —— ✅ 该做（唯一有真实缺口的一项）

**事实**：`propose_next` / `class Director` **全库 0 处**。图上它是
「停因 → 动作，产出 BranchContext 以 followup 提交 mailbox」，
红线条「不读预算、不得自行开 run」。

**这是唯一真正缺失的能力**，也是「自主研究 agent」与「跑批脚本」的分水岭。

**但范围必须收窄**：图上 Director 有无约束的「策略」语义；本项目实际
需要的只有**一个确定性规则**——study 停止后，根据停止原因决定下一步
（继续 / 换场景 / 等人工）。不做自由发挥的 LLM 导演（那是新功能不是重构）。

### 1.4 preset Tier 0 治理分级 —— ⏳ 本刀不做（无消费方）

**事实**：`preset_loader._reject_unsupported()` 已拒绝 `redirect`、警告
`budget_token`（B3 修复的 8 项空声明之一）。但「Tier 0 冻结 = preset
版本化 + 改动即拒」**无任何消费方**：research preset 在代码里
（`graph_preset.py`），不是可加载的文件；swarm preset 37 个全是
`branches=False` 的扁平配置。

**结论**：不做——冻结机制要保护的是「可被外部修改的 preset 文件」，
当前没有这类对象。重开条件=preset 真正外置为可编辑文件时。

---

## 2. 本刀范围：只做 Director（确定性版）

### 2.1 契约

```python
# core/study/director.py
@dataclass(frozen=True)
class Directive:
    action: str        # continue | rescope | escalate | stop
    reason: str        # 触发的停因
    study_id: str
    payload: dict      # 传给下一轮的调整（如换场景名）

def propose_next(study: Any, stop_reason: str, *, metrics=None) -> Directive
    """确定性的「停因 → 下一步」映射。**不读预算**（红线），不执行，
    只产出建议；执行仍由 scheduler 的 submit 决定。"""
```

停因映射表（确定性，无 LLM）：

| 停因 | action | 理由 |
|---|---|---|
| `budget:turns` / `budget:time` | `escalate` | 预算耗尽→需要人决定加预算还是收工 |
| `early_stop:idle_rounds`（连续无改进） | `rescope` | 换场景/换目标 |
| `aegis:regression`（新颖性/回归门连拒） | `rescope` | 当前方向走不通 |
| `completed` | `stop` | 正常收工 |
| `cancelled` / `interrupted` | `stop` | 人不让跑了 |
| 其他 | `escalate` | 未知停因交给人 |

### 2.2 接线（红线：不自行开 run）

`scheduler._run_one_study` 收尾时调 `propose_next`，把结果**只发事件**
（`study.directive`），**不自动 submit**。人工/上层决定是否按建议执行。
这样：(a) 兑现「产出建议」；(b) 严格不越红线；(c) 零行为变化（默认不自动跑）。

若要「自动接力」，那是后续的显式开关（`SR_STUDY_AUTO_DIRECTIVE=1`），
不在本刀默认打开。

## 3. 测试计划（`tests/test_r6_director.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 预算耗尽 → escalate | action/reason 正确 |
| 2 | 连续无改进 → rescope | — |
| 3 | AEGIS 回归 → rescope | — |
| 4 | completed → stop | — |
| 5 | 未知停因 → escalate（不猜） | — |
| 6 | 不读预算（红线） | Directive 不含预算字段；调用不触发 budget 查询 |
| 7 | 不自行开 run | propose_next 前后 mailbox 深度不变 |
| 8 | scheduler 发 study.directive 事件、不自动 submit | 事件存在；`_queued_study_ids` 不增长 |

## 4. 验收对照

| 门 | 判定 |
|---|---|
| 行为等价 | 只新增事件，无既有路径改变；无自动 submit |
| 测试不减 | inventory 0 removed |
| 失败集不扩大 | c1_gate 对基线 |

## 5. 对架构图的影响

| 格 | 原标注 | 新标注 |
|---|---|---|
| Director | ⏳ 虚线待建 | **✅ 确定性版（propose_next + 事件，不自动开 run）**；LLM 自由导演 ⏳ 无需求 |
| RunContext | ⏳ 虚线待建 | **机制已存在（散装 state 键）**，包对象 ⏹ 不做 |
| 方法论归位 | ⏳ 切 C1 | **⏸ 延后**，前置=先解 phase_engine↔runner 循环导入 |
| preset Tier 0 | ⏳ | **⏹ 不做**（无可被外部修改的 preset 文件） |
