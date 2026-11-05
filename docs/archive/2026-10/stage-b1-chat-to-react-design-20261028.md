# B1 chat 链迁 react 节点 — 调查与设计 — 2026-10-28

> C1 执行体收敛第一刀。原则：**先量清缺口再动手**，不为了「看起来统一」
> 把 chat 功能做薄。

## 1. 调查结论：这不是「换执行器」，是「搬外围能力」

chat 链的构造参数（`build_chat_agent_loop`，`chat_loop.py:129`）有 15 项，
react 节点当前只覆盖 LLM↔工具循环本身：

| chat 链能力 | react 节点现状 | 缺口级别 |
|---|---|---|
| LLM↔工具循环 | ✅ 有（含 OpenAI 工具消息排序） | — |
| 流式输出 | ✅ 有（P5-1b `text_delta`/`thinking_delta`） | — |
| 预算（turn/token/time） | ✅ 有（`RunBudget` + `BudgetLimits`） | — |
| 工具事件（tool.started/completed） | ✅ 有 | — |
| **系统提示 + 工具清单** | 🟡 只有 `cfg.system` 字符串 | 需接 `assemble_loop_parts` |
| **上下文注入**（goal/memory） | ❌ 无 | 已有 ContextHub，接线即可 |
| **mid-loop compaction** | ❌ 无 | 需移植 `loop_compaction` |
| **steering inbox**（中途插话） | ❌ 无 | 需移植 |
| **auto-create hypothesis** | ❌ 无 | 需移植 |
| **权限网关**（ASK + allowlist） | ❌ 无 | 需移植（安全相关，不可丢） |
| **子 agent 委派**（`subagent_tool`） | ❌ 无 | F1-F4 fanout 可替代，但语义不同 |

**AgentLoop 共 40 个方法 / 1161 行**；chat 链用到的核心循环之外，
还有 6 类外围能力要搬。

## 2. 关键判断：B1 的真实工作量

原估 2-3d **偏乐观**。真实拆解：

| 阶段 | 内容 | 工量 |
|---|---|---|
| B1-a | 系统提示接线（`assemble_loop_parts` → react config） | 0.5d |
| B1-b | 上下文注入（ContextHub provider → messages） | 0.5d |
| B1-c | mid-loop compaction 移植 | 1d |
| B1-d | 权限网关移植（**安全关键，必须有测试**） | 1d |
| B1-e | steering inbox + hypothesis | 0.5d |
| B1-f | 等价性验证（chat 全链路：流式/工具/多轮/压缩/权限） | 1d |
| **合计** | | **4.5d** |

比 C1 总估（6-9d）里的 chat 部分多出约 1.5d——因为原估把「外围能力」
当成了可选项。

## 3. 建议的推进方式（增量，每步可独立验收）

**不做「一次性替换」，做「并行接管 + 逐项等价」**：

1. **B1-a/b**：react 节点补系统提示与上下文注入 → 用环境开关让 chat
   走 react（`SR_CHAT_ENGINE=react`），默认仍 AgentLoop；
2. **B1-c/d/e**：逐项移植，每项移植后开关下跑全链路回归；
3. **B1-f**：开关默认切到 react，观察；AgentLoop 的 chat 路径标记
   deprecated；
4. **后续**：study/workflow/swarm 链迁移完成后，统一删除 AgentLoop。

**为什么用开关而非直接替换**：chat 是用户可见主路径，权限网关出问题
是安全事故（P5-5 刚修完的正是权限相关）。开关让每一步都能回退。

## 4. 本阶段（B1-a/b）的具体范围

只做前两项（1d），因为它们**没有行为风险**且立刻可验：

- 系统提示：`build_chat_agent_loop` 已经在调 `assemble_loop_parts`，
  把结果传给 `ReactConfig.system` 即可（现在 react 已经支持 `system` 字段）；
- 上下文注入：`AgentLoop.__init__` 已注册 `PreRunContextProvider`（P5-4），
  react 侧需要同样的 pre-run 注入 —— 在 `_build_engine` 里构造 hub 并
  把 items 拼进 messages。

验收：开关下 chat 的系统提示与上下文与 AgentLoop 路径**逐字节等价**
（照 B1-b research preset 的等价性方法论）。

## 5. 风险

| 风险 | 缓解 |
|---|---|
| 权限网关漏搬 → 工具绕过审批 | B1-d 必须有负路径测试；在此之前开关不默认开 |
| 压缩逻辑差异 → 长会话行为漂移 | B1-c 用现有 `compact_messages` 同一实现，只搬调用点 |
| 流式事件名不一致 → 前端失灵 | 已有 P5-1b 的 `text_delta` 契约，等价测试覆盖 |
| 一次做完不可回退 | 分五步 + 环境开关，每步独立验收 |
