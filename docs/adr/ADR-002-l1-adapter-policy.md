# ADR-002：L1 哑适配器策略 — 不追求零直连，锁定编排决策类

日期：2026-11-03　状态：**已接受**　Owner：接口 Owner
输入：`docs/w1-2-audit-l1-l2-20261103.md`
关联：`docs/unified-entry-design.md` · 架构图 v8 ① 渠道段

## 背景

W1-2 审计了 L1（api / cli / mcp）→ L2（研究域）的全部导入，141 条语句。
架构图与 `api/app.py` 的叙事是「哑适配器：只构造 WorkRequest + 订阅事件流」。
实测 D 类（直接使用研究域能力）占 62%，**超出 30% 阈值，叙事部分失真**。

## 决定

### D1. 不追求「零直连」——账本直读是合理设计

三类实测占比：

| 类 | 内容 | 占比 | 判定 |
|---|---|---:|---|
| T | 类型 / 枚举 / 常量 / 异常 | 32% | ✅ 合法 |
| **D-ledger** | **`GoalStore` / `StudyStore` 等账本读取** | **48%** | ✅ 合法 |
| D-orch | `GoalWorkflowRunner` / `CheckpointStore` / `cmd_goal_*` / workflow config | 14% | ❌ 越界 |
| O | re-export / 渲染 | 6% | ✅ 合法 |

**理由**：路由需要读账本做展示（`GET /api/goal/{id}` 必须查 `GoalStore`）。
把简单读查询改造成「提交 WorkRequest → 等事件流回传」是净负收益：
增加一跳异步、引入超时语义、调试成本上升，只为了形式上的「适配器纯度」。

### D2. 收敛目标 = D-orch 类（约 20 条）

必须经 Gateway 提交的是**编排决策**，不是数据读取：

| 符号 | 位置 | 现行为 |
|---|---|---|
| `GoalWorkflowRunner` | `api/routers/workflow.py:91`、`slash_commands.py:621`、`cli/commands/slash_goal.py` | 路由/CLI 直接构造并驱动 workflow |
| `CheckpointStore` | `cli/commands/slash_goal.py` ×4 | CLI 直接操作 checkpoint |
| `cmd_goal_*` ×7 | `cli/__init__.py:1005` | CLI 直接注册研究域命令 |
| `build_autoresearch_workflow_config` | `slash_commands.py:578` | 路由内构造 workflow 配置 |
| `load_goal_workflow` / `list_goal_workflows` | 多处 | 路由内加载 workflow 定义 |

→ 新工作项 **W3-8「L1 编排决策类收敛」**，进 B3 执行批。

### D3. 门禁规则分档（W4-1）

`test_layer_matrix` 的 L1→L2 计数应分两档：

| 档 | 内容 | 棘轮 |
|---|---|---|
| `L1>L2(ledger)` | 账本/类型/常量读取 | 不设棘轮（允许增长，只要不超过 L2 只读接口） |
| `L1>L2(orchestration)` | 编排决策驱动 | 棘轮 20 → 0 |

实现方式：门禁按被导入符号名判定（`*Runner` / `*Store` 之外的驱动类），
或维护显式符号白名单。

## 影响

- 架构图 v8 ① 段注记「哑适配器」需修正为「哑适配器 + 账本直读」——待图 v8.4。
- W3-8 落地后 D-orch 20 → 0。
- L1 的 188/113 条边中，约 86% 属合法类别，**不是债务**。

## 验证

- W3-8 验收：`L1>L2(orchestration)` 棘轮 20 → 0；`pytest tests/test_layer_matrix.py` 绿。
- 本 ADR 阶段无代码改动。

## 重开条件

若出现以下情况，重新评估是否收敛账本直读：
1. L2 账本接口出现跨进程/跨服务形态（届时「直读」不再可行）；
2. 账本直读导致 L1 层出现业务校验逻辑（当前没有，都是透传）。
