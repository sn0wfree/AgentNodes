# 文档索引与治理 — 2026-10-29（C2）

> 273 份 md 里 164 份无交叉引用。逐个重写不现实，也不是好主意。
> 本文建立**权威索引**：哪些是当前有效文档、哪些是被取代的历史记录、
> 哪些需要归档。读完这一份就能知道该信哪份。

---

## 0. 治理原则

1. **不重写历史文档**。已完成的 stage 文档是决策记录，改它等于篡改证据。
2. **只标注状态与取代关系**。过期文档保留原文，但在本文索引里标明。
3. **当前有效 = 能对上代码的**。凡本文标 ✅ 的，都经过代码级验证。
4. **归档 ≠ 删除**。`docs/_archived/` 只存放已被明确取代且无引用价值的。

---

## 1. 当前有效文档（读这些）

### 1.1 架构与契约（最高权威）

| 文档 | 作用 | 验证 |
|---|---|---|
| `architecture-diagram.html` | v6.2 五层目标架构图 | ✅ 与代码一致（审计门② §5） |
| `stage-c1-team-contract-20261028.md` | 并行重构契约：能力矩阵/事件表/所有权/less-is-more | ✅ 16 条架构门禁钉死 |
| `stage-c1-acceptance-20261028.md` | 验收标准：三道门 + 终局判定 | ✅ 已按选项 B 修订 |
| `project-status-and-roadmap-20261028.md` | 现状 + 路线图 | ✅ B2/B3 已更新 |

### 1.2 C1 实施记录（按时间序）

| 文档 | 内容 | 提交 |
|---|---|---|
| `stage-c1-wp-agent-node-design-20261028.md` | 选项 B：编排/执行分工 | `0db7e6ce` |
| `stage-c1-b3-swarm-orchestration-design-20261028.md` | swarm 不迁的论证 | `755d9051` |
| `stage-c1-b3-workflow-assessment-20261028.md` | workflow 不迁的论证 | `755d9051` |
| `stage-c1-b4-hotfix-d7-schema-20261029.md` | D7 + schema 双主止血 | `c161b0aa` |
| `stage-c1-b5-wiring-20261029.md` | ContextHub 接线（选项 A） | `1a9f2c49` |
| `stage-c1-b5-w2-arbitration-20261029.md` | 仲裁 + 预算取数修正 | `db8bc7b8` |
| `stage-c1-b5-prerun-react-20261029.md` | goal/memory 入 react | `38ad7786` |
| `audit-gate-2-report-20261029.md` | 审计门② 五项 | `5b38a8a7` |

### 1.3 待实施（有前置条件）

| 文档 | 状态 |
|---|---|
| `stage-c1-b4-dataplane-design-20261028.md` | 设计完成，等 7 个语义决策 |
| `stage-c1-b5-contexthub-w1w2-design-20261028.md` | W1/W2 已实施，§3.1 的 priority 修正留 W3 |

---

## 2. 历史记录（保留了决策过程，但结论已被取代）

### 2.1 20261008 架构讨论批次 —— 已被 v6.2 取代

这 9 份是 v2/v3 时代的架构讨论，**结论已被 `architecture-diagram.html`（v6.2）
和后续 30 份 stage 文档取代**。保留原因：记录了当时的权衡与反对意见。

| 文档 | 状态 |
|---|---|
| `architecture-current-20261008.md` | 过时：描述 v3 五层，与 v6.2 不同 |
| `architecture-discussion-record-20261008.md` | 历史：四轮讨论纪要 |
| `architecture-harness-recommendations-20261008.md` | 历史：harness 建议 |
| `architecture-reeval-20261008.md` | 历史：架构师重估 ① |
| `architecture-reeval2-20261008.md` | 历史：架构师重估 ② |
| `architecture-refactor-brief-20261008.md` | 历史：重构简报 |
| `architecture-v2-and-rsi-20261008.md` | 过时：v2 目标架构 |
| `architecture-v3-openclaw-inspired.md` | 过时：v3 目标架构 |
| `architecture-review.md` | 历史：332 行早期评审 |

**为什么保留而非删除**：v3→v6.2 的演进理由（为什么从「三 runtime」收敛到
「编排唯一」）只在这些文档里有，删了就丢了论证链。

### 2.2 已被明确取代的方案文档

| 文档 | 取代者 | 依据 |
|---|---|---|
| `roadmap-plain-20261026.md` | `project-status-and-roadmap-20261028.md` | 文档自身声明「已重排，保留作对照」 |
| `stage-3b-plan-20261026.md` | 阶段 3c / 4 实测记录 | 3b 已被 3c 取代 |
| `replan-after-e1-20261026.md` | 同上 | 纵向切片计划已完成 |
| `pre-e1-final-review-20261026.md` | 同上 | 自身标注「归档」 |
| `code-graph-vs-architecture-gap-20261027.md` | 本索引 §1 | 差距已全部填平（ContextHub 存在、GraphEngine 编排） |
| `code-and-test-inventory-20261026.md` | 本索引 + `project-status` §1 | 静态清单过期 |

### 2.3 调研类（结论已吸收，原文保留）

| 文档 | 结论去处 |
|---|---|
| `hitl-resume-industry-research-20261027.md` | 方案 A 已实施（`71d5cf34`） |
| `execution-plane-research-20261024.md` | 「GraphEngine 唯一」已被选项 B 精化为「唯一编排」 |
| `platform-data-plane-*`（2 份） | 已归档，数据面设计在 B4 |
| `gateway-hosting-research-20261024.md` | 单进程宿主已实现 |

---

## 3. 需要归档的（建议移入 `docs/_archived/`）

**判定标准**：满足全部三条 —— ① 结论已被取代、② 无其他文档引用、
③ 不含独一无二的论证链。

| 文档 | 理由 |
|---|---|
| `session-summary-20260804.md` | 最早的一次会话摘要，无引用 |
| `code-quality-upgrade-20260813.md` | 代码质量计划，已由 P0-P3 完成 |
| `agentquant-research-20260902.md` | 外部调研，无引用 |
| `step3-run-loop-extraction-design.md` | run loop 抽取已完成 |
| `study-archive-objective-flow-redesign.md` | 自身标注「归档」 |
| `study-chat-data-flow.md` | 历史记录，数据面已由 B4 接管 |
| `study-frontend-design.md` | 历史记录 |
| `study-subsystem-tech-debt-20260827.md` | 技术债大部分已清，剩项并入 B4 |

**本轮不执行移动**：`_archived/` 会造成 164 份无引用文档里的引用断裂风险，
而收益只是目录更短。按 less-is-more，索引标注已足够。

---

## 4. 文档健康度实测

| 指标 | 值 |
|---|---|
| md 总数 | 273 |
| 带日期戳 | 88（20260804 .. 20261029） |
| 无交叉引用 | 164 |
| 当前有效（本文 §1） | 13 |
| 历史记录（§2） | ~20 |
| 建议归档（§3） | 8 |

**读数**：真正需要「读懂才能工作」的只有 13 份。其余是决策考古材料，
不需要维护，也不应该被当成当前事实引用。

---

## 5. 引用规则（给后续 worker）

1. **写新文档前**：查本索引 §1，看是否已有权威文档可追加，而不是新建。
2. **引用历史文档时**：标注它是历史记录，不要让读者以为是当前事实。
3. **发现文档与代码矛盾**：改代码或改文档，然后在本文 §2 记录取代关系——
   不要让两份文档同时「正确」。
4. **不要新增无日期戳的文档**：日期戳是判断新旧的唯一可靠信号。

---

## 6. 已知的文档-代码不一致（本轮未修，如实记录）

| 文档 | 不一致 | 处置 |
|---|---|---|
| `stage-c1-b5-contexthub-w1w2-design` §3.1 | 说 `PreRunContextProvider` 的 priority 语义误用，留 W3 | 未修（会改渲染字节） |
| `code-and-test-inventory-20261026.md` | 说 ContextHub 不存在（当时确实不存在） | 已过期，见 §2.2 |
| `execution-plane-research-20261024.md` | 「GraphEngine 唯一 runtime」 | 已被选项 B 精化，见 §2.3 |
