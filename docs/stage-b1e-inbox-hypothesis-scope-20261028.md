# B1-e steering inbox + hypothesis — 调查与设计 — 2026-10-28

> 调查结果推翻了原估：这两项对 **chat 链**都不是必需能力。文档记录
> 依据，并把范围收缩为「一条确实需要的 + 一条明确不做的」。

## 1. 调查：两项能力的真实使用面

### 1.1 steering inbox —— 零生产调用方

| 证据 | 结论 |
|---|---|
| `git grep "\.inject(\|ainject(" src/` | 只有定义（`loop.py:794/806`），**无任何调用点** |
| 测试引用 | 仅 `test_dsh_comprehensive.py` 9 处 |
| 设计意图 | DSH-inspired「mid-loop 用户插话」——功能已实现但**没有任何入口接它** |

**这不是 chat 链的功能**，是预留能力。chat 的「中途插话」实际走的是
**新一轮 turn**（mailbox 准入），不是 loop 内注入。

### 1.2 hypothesis auto-create —— chat 链显式关闭

`chat_loop.py:140` 明确传 `enable_hypothesis_auto_create=False`。
`loop.py:345` 的默认值 `True` 只对 **study 的 role_factory 路径**生效
（研究轮需要自动建假设）。chat 场景不创建假设。

## 2. 结论：B1-e 范围重定义

| 项 | 原计划 | 实际结论 |
|---|---|---|
| steering inbox | 移植到 react | **不做**。零生产消费者；chat 的插话语义是「新 turn」，不是 loop 内注入。移植一个没人用的能力是把死代码搬进新引擎。 |
| hypothesis auto-create | 移植到 react | **不做**（B1 范围内）。它是 study 链的能力，属于 **B2**；chat 显式关闭。 |

**B1-e 因此从「0.5d 移植两项能力」变为「0 项移植 + 记录决策」。**

## 3. 唯一需要确认的事

react 节点是否需要在 **max_iterations > 1** 时支持「中途追加消息」？
答案：**不需要**——因为 AgentLoop 的 inject 也没有生产调用方，如果将来
真的需要，正确做法是**先有消费者再接**（YAGNI），而不是先搬一份。

## 4. 对 B1-f 的影响

B1-f（全链路等价 + 开关切换）的检查清单去掉两项：

- ~~steering inbox 等价~~
- ~~hypothesis auto-create 等价~~

剩余检查清单（B1-f 真正要验的）：

| # | 项 | 状态 |
|---|---|---|
| 1 | 系统提示逐字节等价 | ✅ B1-a 已验 |
| 2 | 上下文注入形状等价 | ✅ B1-b 已验 |
| 3 | 压缩时机与回滚等价 | ✅ B1-c 已验 |
| 4 | 权限闸门等价（含 DENY/ASK） | ✅ B1-d 已验 |
| 5 | 流式事件名与前端契约 | ✅ P5-1b 已验 |
| 6 | 工具事件（started/completed） | 待验 |
| 7 | 多轮对话（history 注入） | 待验 |
| 8 | 错误路径（LLM 失败/工具异常） | 待验 |

**B1-f 因此可以提前**：1-5 已由前四个阶段覆盖，只剩 6-8 三项验证 +
开关切换本身。

## 5. 决策记录（供后续）

若将来需要「loop 内插话」：

1. 先确定**消费者**（哪个 UI/API 会调用）；
2. 再决定形态——react 是同步节点，`inject()` 的 asyncio.Queue 语义
   在同步路径不可直接复用（AgentLoop 的 sync `run()` 也是靠同一队列，
   但只在 async 上下文有意义）；
3. 最可能的正确形态：react 节点接受 `drain_hook: Callable[[], list]`，
   而不是持有队列——与 compactor/ctx_factory 同一注入风格。
