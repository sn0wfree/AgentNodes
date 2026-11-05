# 编排层成员边界 — 决策记录

> 关联：`layer-review-orchestration-unify.md` · `layer-review-orchestration-engine.md` · `architecture-diagram.html`（v3）
> 状态：**3 条已拍板（2026-10）**；另 5 条仍待决（见 §4）

## 1. 判据（已采纳）

> **编排层回答「一次完整工作，怎么被驱动起来？」**

| 问题 | 归属 |
|---|---|
| 谁决定 **开始 / 继续 / 停** | **编排** |
| 谁决定 **调用哪些组件、按什么顺序** | **编排** |
| 谁决定 **这一步怎么做** | 引擎 / 能力（**不是**编排） |

每个编排格只做 4 件事：**接驱动源 → 循环调能力 → 判停/续 → 收尾持久化**。
编排层是 RunContext 的**持有者与注入方**，不实现预算/事件/追踪/取消。

## 2. 决策（3 条，已拍板）

| # | 问题 | 决定 | 落地 |
|---|---|---|---|
| **1** | **AgentLoop 移出编排层 → 能力层？** | ✅ **同意** | 新切 **0.11**：物理移入 `core/agent/`（能力层），调整 ~20 个文件 import；六层依赖方向首次自洽：编排 → 引擎 → 能力 → 数据 |
| **2** | **SwarmDAG 算编排还是调度？** | ✅ **编排层内的一格**，标注「DAG 驱动」 | 图上标「DAG 驱动 · 单位＝DAG 层 · 分层并行」，与 SessionLoop（消息驱动）/ StudyLoop（轮驱动）并列 |
| **3** | **Director 放编排层还是独立一层？** | ✅ **编排层内的一格** | 图上放第 4 格（虚线框，待建 1.4）——它的产出就是「下一个驱动指令」，正是编排层的输出面 |

## 3. 编排层最终形态（4 格）

| 格 | 驱动源 | 循环单位 | 现状 | 落刀 |
|---|---|---|---|---|
| **SessionLoop** | 用户请求（被动） | 消息 | 1450 行 / 24 方法 | 2.4 拆 3 协作者 |
| **StudyLoop** | 自主（9 类停因） | 研究轮 | 1175 行 | 1.1/1.4 |
| **SwarmDAG** | 批次（35 套 preset） | DAG 层 | 756 行 | 口径成文 |
| **Director** | 自主（停因→动作） | 下一步 | **待建** | **1.4** |

**编排层内部两层分工**：

```
驱动循环（不可替换 · 决定正确性）
    └── 调度策略（可插拔槽位：HITL 何时暂停/恢复 · Director 下一步做什么）
```

**为什么不把「驱动模式」也插件化**：会话驱动 / 自主驱动 / DAG 驱动决定的是正确性（何时停、怎么续），不是行为风格。按 DSH 判据「错了就错的东西不做成插件」，驱动模式留在结构里；只有 HITL 与 Director 是可换槽位。

## 4. 仍待决（5 条，与本议题无关但挂在图上）

| # | 问题 | 我的倾向 |
|---|---|---|
| **D** | 契约（RoundOutcome/BranchContext）提到 `core/study/contracts.py`？ | 该做 |
| **E** | `core/study/` 拆 `method/` + `engine/`？ | 现在做 |
| **R1** | 统一「运行期环境 RunContext」而非「统一编排协议」？ | 同意 |
| **R2** | RunContext 放 `core/orchestration/`？ | 是 |
| **R3** | chat 路径无预算门立刻补（0.5d）？ | 立刻补 |

## 5. 主线新增（0.11）

| 刀 | 内容 | 估工 | 依赖 |
|---|---|---|---|
| **0.11** | **AgentLoop 移入能力层**：物理归位 + import 调整 + CI 门禁（编排层禁依赖 AgentLoop 的内部细节，只经 `Orchestratable`/能力接口调用） | **1d** | — |

**验收**：
- 六层依赖方向 **0 反向**（含 ⑦）
- `core/agent/` 与 `core/study/` 的 import 关系可被 CI 门禁静态检查
- 既有 12k 测试 + eval 跟幅不降

**不做**：不顺带拆 AgentLoop 的 mixin/step（那是 2.4 的触达式工作）；不重命名 AutoresearchRunner（并入 E）。

---

## 6. 业界对标后的修正（2026-10 追加）

> 依据：`docs/archive/2026-10/industry-benchmark-openclaw-20261008.md`（OpenClaw 392k★ 架构对标）

三条决策**方向不变**，但**终点要改**：

| 原表述 | 修正后 | 依据 |
|---|---|---|
| AgentLoop「移入能力层」 | 移入**且成为 runtime**（实现 `AgentRuntime` 协议 prepare/run/settle），不只是搬目录 | OpenClaw 把 harness 做成一等可换 runtime（codex/copilot/claude-cli），平台保留 channels/sessions/policy/state |
| SwarmDAG「编排层内一格」 | 仍是一格，但它是**执行面（runtime）** | 平台/执行面分离是 OpenClaw 的第一原则 |
| Director「编排层内一格」 | 仍是编排层内的**策略**（不是循环） | OpenClaw 的 session controller 拥有 turn 准入，策略是插件 |

**并且我们此前对 C 刀的建议是错的**：

> 原建议「把 `chat_loop` / `role_factory` **收口成一套**」
> ❌ OpenClaw 的做法是**不合并**，而是**做成 runtime 选择 + 声明式回退**
> （选择序：model-scoped > provider-scoped > auto 认领 > 内置 fallback；
> 「一旦某 harness 开始执行，其失败不会通过另一个 runtime 重放」）
> ✅ 修正为：定义 `AgentRuntime` 协议 + 选择器，两套构造成为两个 runtime 实现

**进一步收敛（主线 1.9）**：编排层从「多套控制面」收敛为 **1 个控制面（SessionController）+ N 个 runtime**。

**新发现的两条缺失不变量**（主线 0.12 / 2.1）：
- **write fence**：被取代的 run 不能提交陈旧数据（我们目前**完全没有**；`fence` 命中 35 处全在无关文件）
- **prompt 缓存边界**：稳定段在上、目录在下，刷新不改变前缀字节（我们 44 工具每次重算 tool_list = 成本盲区）
