# AgentNodes 领域词汇表

> 架构评审与深化讨论的统一命名。新增概念或澄清模糊术语时**当场更新本文件**。
> 架构术语（module / interface / implementation / depth / seam / adapter /
> leverage / locality）见 codebase-design skill，不在本表重复。

## 执行与隔离

### IsolatedToolExecutor

一次隔离工具调用的执行器（L3 深模块）。单一 interface 接收一个工具调用请求，
内部完成：隔离决策（生产开关 / 平台 / 能力 / 准入 / allowlist）→ 剩余 deadline
预算 → 父进程规则闸 → 在隔离 worker 内执行 → E5 实际状态记录与终态重注入 →
结构化结果返回。调用方只看到一次调用与结果，不感知内部编排。

- **不是** `IsolationWorker`（那是进程载体 / adapter，负责 spawn/kill/确认）。
- **不是** `should_isolate`（那是 executor 内部的一个决策步骤，Q10 后不再是独立公开接口）。
- 名字来源：2026-11-20 架构深化复审候选 1+2 的 grilling 决策 Q6。

### IsolationResult / IsolatedOutcome

- `IsolationResult`：隔离 seam（worker 通道）的**传输结果**，字段含 status /
  actual_state / call_id / discarded_rows 等。
- `IsolatedOutcome`：IsolatedToolExecutor 的**结果语义**，含派生的
  `result_rejected`（执行器据此禁止重试）。二者分层不同，不做 alias。

### IsolationAdmission

工具"超时能力声明 → 有效能力"的判定点（L3 深模块）。单一公开面
`check_isolation_admission`（能否声明 isolatable）与 `effective_capability`
（读声明 + 非法值回落 + 准入回落后的实际有效能力）。四级能力常量
（`TIMEOUT_CAPABILITY_*`）是本模块的公共词汇，由 core.py re-export 给
loop 消费者，保持 import 面稳定。

- 不是 `IsolatedToolExecutor`（那是执行器，这里是准入判定）。
- 名字来源：2026-11-20 第二轮复审候选 1+2 的 grilling 决策 Q7。
