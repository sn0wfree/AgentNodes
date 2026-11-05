# Agentic 设计理念在本项目的可用性梳理

> 依据：`docs/Agentic 设计框架与 Prime Agent 调研报告.md`（2026-08-08）
> 方法：报告中的设计概念与项目实际代码逐条核对，标注现状、差距与可用性评级。
> 目的：作为后续功能选型清单，避免为「新范式」而引入无收益机制。

## 一、CITE 设计模型（报告 §1.3）

| CITE 维度 | 设计要点 | 项目现状 | 差距 | 可用性 |
|---|---|---|---|---|
| **Context** | 分层记忆 | 短期=session+compact 压缩（`compact.py` LLM 摘要）；长期=`PersistentMemory`（语义 embedding + 关键词双通道 + recency boost）；情景=goal evidence（`GoalStore`） | 缺「跨 goal 经验沉淀」：goal 完成后成功/失败教训不回流长期记忆 | ✅ 高（补沉淀即可闭环） |
| | 上下文压缩 | `compact.py` token 预算压缩 + `_llm_summarize_v2` | 无 | ✅ 已有 |
| **Intent** | 目标拆解 | **无**——最大缺口（即 §4 Plan-and-Execute 方案） | 缺 Planner/Evaluator 层 | ✅ 高 |
| | 澄清式提问 | goal 创建走 `replace_goal` + 默认标准，无「与用户确认目标」环节 | 目标模糊时直接硬跑 | 🟡 中（goal 创建前澄清模板） |
| | 进度对齐 | `GoalWorkflowRunner.get_progress()`、goal 事件、directive 中途指令注入（`goal/workflow.py:360`） | 无 | ✅ 已有 |
| **Tools** | 错误处理/降级 | `tool_errors` 装饰器 + fix_hint、瞬态重试、circuit_breaker | 无 | ✅ 强 |
| | 限流/容错 | SubAgentTool 每轮 ≤5、sandbox、PermissionGateway | 无 | ✅ 已有 |
| | 工具发现 | 固定白名单（role_factory）+ todo 清单 | 无「agent 自行发现工具」机制 | 🟡 低（研究场景工具集稳定，没必要） |
| **Experience** | 推理可视化 | SSE 全链路事件（tool_call/result/text_delta/subagent_*）、trace、右面板 agent-driven | 无 | ✅ 强 |
| | 人工介入 | `PermissionGateway` 异步审批（`permission/approvals.py:93`）、checkpoint 暂停/恢复 | 无 | ✅ 已有 |
| | 确认纠正 | claim_validator 声明验证 | 无 | ✅ 已有 |

## 二、框架选型部分（报告 §1.5-1.6）的启示

| 报告观点 | 本项目判断 |
|---|---|
| 运行时与评估层分开选 | 已有自研 trace/event_store；不建议引入 Langfuse 等外部平台（评估是交易策略指标而非 LLM 打分） |
| LangGraph checkpointer 时间旅行 | 项目已有 `checkpoint_store.py`（goal 层）+ `run_store.py`（swarm）+ session 持久化，已覆盖 |
| MCP 协议标准化 | 已接入 `core/mcp/`（mcp_swarm_tools 测试在） |
| 避坑#1「不为多 Agent 而多 Agent」 | 现状是单 AgentLoop + 可选 swarm 多角色，符合 |

## 三、Prime Agent 特性（报告 §3）逐条评估

| Prime 特性 | 项目现状 | 结论 |
|---|---|---|
| **IPython 唯一工具** | 已有 9 个工具组（数据/回测/展示/子 agent） | ❌ **不引入**——领域工具生态而非从零编码 agent；范式变革无收益 |
| **Daemon 后台** | `scheduled_research`（crontab）+ workflow monitor 后台回测 + web/HTTP | ✅ 已有等价物，不需要 |
| **递归子 Agent** | `SubAgentTool`（SwarmWorker 轻量 ReAct、每轮 ≤5、禁止嵌套） | ✅ 已有雏形。差距：无持久化子会话——🟡 中优先级，「禁止嵌套」是安全设计应保留 |
| **自改进 /refine** | 无。Skills 只读（ListSkillsTool/LoadSkillTool），无创建/编辑工具 | 🟡 渐进式：第一步「goal 完成 → 经验/教训写 PersistentMemory」（`PersistentMemory.add` 已可用，零风险）；agent 可写 skills 需权限门禁，暂缓（报告自身也指出「作弊技能」风险） |
| **Goal + Heartbeat + Autonomous** | Goal 系统完整（goal_tools/injection/事件/预算）✅；Heartbeat 仅「工具执行心跳」（`loop.py:187`）≠「定时消息注入」；Autonomous 缺「gate 验证循环」 | 🟡 中：可做 goal 的「回测通过门禁」闭环（已有 metric_targets 雏形） |

## 四、优先级清单（建议落地顺序）

1. 🔴 **§4 Plan-and-Execute**——唯一大缺口，补「目标拆解 + 执行中重规划」；方案已核对可行（见下节引用），代码增量 ~1400 行
2. 🟠 **goal 经验沉淀**——goal 完成态写 PersistentMemory（自改进的安全第一步）
3. 🟠 **SSE 阶段事件**——plan/step/eval 事件入右面板（§4 可观测性配套，复用 agent-driven 机制）
4. 🟡 **目标澄清模板**——goal 创建前 LLM 生成澄清问题列表
5. 🟡 **子 agent 会话持久化**——跨轮复用子任务（保留无嵌套约束）

## 明确不引入

- IPython 唯一工具范式（含 shell 自由执行替代工具生态）
- 自改进 skills CRUD（权限与「作弊技能」风险，待权限门禁成熟）
- Langfuse/LangSmith 等外部评估平台（自研 trace 已覆盖）
- Agent 自行发现工具（工具集稳定，无收益）

## 五、与 §4 Plan-and-Execute 的衔接

「目标拆解」是本梳理唯一的 🔴 缺口，报告 §4 已给出完整改造方案
（`docs/Agentic 设计框架与 Prime Agent 调研报告.md` 第四节：7 个新文件 + 3 个 prompt，
在 `GoalWorkflowRunner → SwarmRuntime → WorkflowController` 之上加动态规划循环，
经代码核对全部假设成立，实施时按「先提交设计文档 → 分 Commit 提交代码」推进）。
