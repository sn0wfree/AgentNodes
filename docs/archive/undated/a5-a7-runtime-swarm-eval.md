# A5–A7 — Loop 拆分 / Swarm 收敛 / 评测网

> 2026-09-29 · Agent Runtime 控制面收口  
> 状态: **Implemented** (A5 seams · A6 toolset/budget · A7 golden evals)

---

## A5 Loop 拆分

目标：从 `AgentLoop`（113KB 巨石）抽出可测边界，**不重写控制流**。

```text
core/agent/runtime/
├── policy.py     StopPolicy / BudgetPolicy   (A1 预算 + stop 语义)
├── model_io.py   ModelIO                     (chat/stream + retry 边界)
└── telemetry.py  Telemetry                   (emit + trace)
```

| 模块 | 职责 | AgentLoop 接线 |
|---|---|---|
| `StopPolicy` | budget/max_iter/no_progress 判定 | `_check_budget` 委托 |
| `ModelIO` | `chat/achat/stream` 统一出口 | `_stream_chat` / LLM call |
| `Telemetry` | `_emit` / `_trace` | 自有字段 → 委托 |

原则：**委托不搬家**——loop 仍是编排器，runtime 是可单测的纯件。

---

## A6 Swarm 收敛（设计）

```text
现状: Swarm = N × 完整 AgentLoop（工具/上下文重复）
目标: Orchestrator + 轻量子任务 (shared workspace + toolset)
```

- `task` 工具：子任务只拿 **窄 toolset** + 独立 max_iter
- 共享 Workspace/记忆，不复制 session
- 预算：子任务继承父 run 的 token 剩余额度

---

## A7 评测网（设计）

```text
evals/
├── golden_tasks.jsonl     因子/回测诊断任务
├── run_evals.py           跑 AgentLoop / 工具链
└── scorecard.py           工具选择正确率 / 预算 / 完成率
```

改 runtime 必须过 golden tasks。

---

## 验收

```text
pytest tests/test_agent_runtime.py -q
```
