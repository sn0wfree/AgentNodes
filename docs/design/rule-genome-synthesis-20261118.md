# 规则基因组 · 研究汇总与决策锚点

> **定位**：本文是 2026-11-17 全部讨论的**汇总结论与决策锚点**——后续拍板（★ 五项）、开工（M1）、评审都以本文为索引起点。
> **上游文档**（均已入库，commit `b115f278`）：
> - 概念框架：`docs/提示词与基因组-概念对映研究-20261117.md`（R1–R6、概念对映表、失效边界）
> - 设计规格：`docs/design/rule-genome-design-20261117.md`（v2 trace 绑定版，含 §10 未决清单 22 项）
> - 可视化：`docs/规则基因组体系 · 概念示意图 · 20261117.html`（ID 脊柱图 + 5 评估锚点）
> - 先例底座：`docs/提示词基因框架-业界先例调研-20261117.md`（**尚未入库**）、`docs/CL4R1T4S-项目评估-20261117.md`（**尚未入库**）

---

## 0. 一句话

**从「genome 这个词在 prompt 域意味着什么」出发，落到「我们自己规则系统的基因组化形态」，止于「它还缺什么才能可信」——系统设计已定型（v2），实现为零，五个定型级决策悬置。**

## 1. 讨论演化链

| 轮 | 触发 | 问题 | 关键结论 | 产物 |
|---|---|---|---|---|
| 1 | 调研指令 | genome 概念移植到 prompt 域，哪些成立？ | 六种关系共用一个词（R1–R6）；AgentNodes 的 prompt 是双重身份；18 个生物学概念判定：承重 9 / 部分 6 / 装饰 1 / 失效 2 | 概念对映研究 |
| 2 | **用户提案**：AGENTS.md 每条规则当基因片段控制行为，事后归因「哪段行为由哪条规则起效」 | 怎么落地？ | 档案学半边**已存在**（R-\* 编号 = locus、模态、「失败」字段 = verifier 指针、L2→L1 = 带闸拉马克通道）；缺三件：Registry / 表达日志 / 归因 | 设计 v1 |
| 3 | **用户修正**：要与 trace 联合绑定 | 规则观测放哪？ | **砍独立账本**；全链长在现有 trace / event_log 上；唯一新键 `rule_id`；三条铁律 | 设计 v2 |
| 4 | **用户刹车**：先不慌开工，还有什么没讨论到 | 体系还有什么洞？ | 22 项未决（A 基因本体 / B 转录语义 / C 测量学 / D 因果边界 / E 规模生态 / F 流程），其中 5 项 ★ 定型级 | 设计 §10 |
| 5 | **用户要求**：画符号线图评估 | 全局长什么样？ | ID 脊柱一张图 + 未决热点标注 + 5 个评估锚点 | HTML 示意图 |
| 6 | **用户指令**：只提交本次产物 | — | `b115f278`（3 份 / 831 行），上轮 2 份调研未入库，他人重构未触碰 | 本次 commit |

## 2. 思想体系（四层）

### 2.1 概念层 · 世界观

- **R1–R6 六种关系**（详见概念对映研究 §1）：本项目主线是 **R1 描述性基因型**；R2 搜索表示 / R3 探针 / R4 模因 / R6 同名异物是要主动避开的混淆源；引用「基因」一词处必须能定位到 R 几。
- **R5 是本项目独有的关系**：prompt 既是 Tier 1 基因（`kernel/evolution/genome_proposals.py:41`，TIER1_COMPONENTS 含 prompt/skill/graph_shape/tool_set/compaction），又是运行时转录本（`kernel/agent/harness_resolver.py` 按 task_signature 装配、skill 只追加）。
- **推论**：CL4R1T4S 类厂商语料是**转录本观察**不是基因组观察——所有「家族 / 共识」结论必须带「表达层面」限定词；同一厂商多 surface = 同一基因组多次转录（差异表达），与跨厂商比较是两种口径。
- **关键类比修正**：AI 域拉马克继承是常态（经验写回基因组），成立版本是「可写回但必须过闸」= validation_gate ≈ Weismann 屏障；prompt 复制无校对酶 ⇒ provenance 是替代机制而非好习惯。

### 2.2 架构层 · 规则基因组 v2

一句话：**规则 = 基因，trace = 行为轨迹，归因 = 只读 join，零新存储。**

```
registry(rule_id, text_sha256, registry_sha256)
  → genome_snapshot trace entry     「本次会话携带哪些基因」（挂 genome_version_id + 装配链）
  → tool_call / tool_result          既有行为轨迹（不动）
  → rule_check trace entry           verdict + evidence{iter, call_id}（同迭代绑定行为点）
  → scorecard                        指纹 ⊕ genome_version_id ⊕ trace_ref
  → GenomeProposal.evidence_refs     规范化 URI：rule:R-x@sha8 / trace:sid#iter&call_id / eval:run_id
  → validation_gate（≈Weismann 屏障）→ activate（强制 proposal_id）→ 新 version_id → 闭环
```

**三条铁律**：
1. **零平行账本**——会话内观测落 `trace.jsonl`，会话外落 `EventV2 rule.observed`，无第三种存储；
2. **零新增 ID 体系**——join 只用既有 session_id / iter / call_id / genome_version_id / proposal_id，唯一新键 `rule_id`；
3. **观测者 ≠ 被检方**——写入方永远在理念七屏障下方（kernel 侧 / 进程外 / 人工），agent 无伪造入口。

### 2.3 方法层 · 归因三法与诚实边界

| 方法 | 形态 | 关键设计 |
|---|---|---|
| 表达谱 | 被动聚合（registry × snapshot × rule_check） | 零表达 × 零遵守 → 候选降级评审名单（数据化晋升，先排除触发场景未覆盖的假阴性） |
| 敲除 | 主动对照（gold standard） | 变体 registry 的 sha256 **自证身份**无需标记；Δeffect 必须带**本底对照**（敲除后行为不变 ⇒ 来自权重先验） |
| 关联键 | rule_id × eval 分数 × proposal.evidence_refs | 上轮自研 #13「条款 ID ↔ 评测分数关联键」在本仓率先落地 |

**四条诚实边界**：归因是证据性结论（多效性 / 上位性 ⇒ 多对多，报告禁写成单射）；表达 ≠ 遵守（transcribed / enforced 分开记 verdict）；规则在场即干预（Hawthorne 尾巴）；单敲除无表型 ≠ 无用（功能冗余 / 合成致死，D13 未决）。

### 2.4 治理层 · 什么还没想清楚

22 项未决全表见设计文档 §10。★ 定型级 5 项的拍板要点：

| ★ | 问题 | 选项与影响 |
|---|---|---|
| **B6 转录语义** | AGENTS.md 全文注入 ⇒ snapshot 千篇一律 ⇒ entry 无信息量 | A：接受为「审计存照」（便宜，无装配改动）；B：按 task_signature **选择性转录规则子集**（信息量大，牵动 harness_resolver——观测系统升级为表达机制，架构级决策） |
| **C9 表型粒度** | scorecard 是任务级分数，规则效应怎么拆 | 需定义测量模型；不定则关联键 join 出的是噪音 |
| **C10 量具校准** | judge 一致性 / regex 查准查全 / verifier 自身测试 | 需建立校准样本集制度；不定则一切 verdict 无可信度上限 |
| **C11 统计功效** | n、检验选型、多重比较校正（50 规则 × 多场景必出假阳性） | 参照上轮调研 #12 的 Benjamini–Hochberg FDR；不定则 Δeffect 不可用 |
| **A3 registry 历史版本化** | 老 trace 的 registry_sha256 必须永远可解析 | registry 入库 + git 历史作不变量；不定则老 trace 变化石 |

**E16（范围）**与 ★ 同级：单 agent 先行 vs 全 swarm（per-role 快照 + 主体归属），决定 M1 schema 是否加 role 维度。

## 3. 已定案的共识

- 系统形态 = v2 trace 绑定版（§2.2）；不做清单三条（无事后逆向归因、无跨规则排名、**无独立观测存储**——除非证明三存储无法 join）；
- M1–M3 分期与机检验收（设计文档 §6）；M1 试点 3 条规则：R-001 / R-002 / R-004（均有现成进程外 verifier）；
- 通道矩阵与理念七合规表（设计文档 §5）；uncertain / verifier=null / 「未识别」均为一等状态不兜底；
- 安全注记：knockout 变体只在本地临时构造，**严禁 `gh gist edit` 推回 L0 本体**；trace excerpt ≤200 字符指针优先；观测改造不得改变门禁失败语义。

## 4. 产物清单与状态

| 产物 | 路径 | 状态 |
|---|---|---|
| 概念对映研究 | `docs/提示词与基因组-概念对映研究-20261117.md` | ✅ 已提交 `b115f278` |
| 规则基因组设计 v2（含 §10 未决 22 项） | `docs/design/rule-genome-design-20261117.md` | ✅ 已提交（设计稿，**未实施**） |
| 体系概念示意图 | `docs/规则基因组体系 · 概念示意图 · 20261117.html` | ✅ 已提交（渲染已自检） |
| 研究汇总（本文） | `docs/design/rule-genome-synthesis-20261118.md` | ✅ 本 commit |
| 业界先例调研 / CL4R1T4S 评估（上游引用） | `docs/` 两份 md | ⚠️ untracked（建议补提交使引用不断链） |
| 代码实现 | — | **零**（按指令停在开工前；AGENTS.md / rules.md 未动一字） |

## 5. 下一步路径（依赖排序）

1. **拍板 ★ 五项 + E16**（纯讨论，每项一次对话可定一个）→ 产出设计 v3；
2. v3 定稿后 **M1**：`scripts/arch/rule_registry.py` 只读解析器 + genome_snapshot 落点 + evidence_refs URI 校验器（验收全机检，见设计 §6）；
3. 与 M1 平行可做 **F20 事故→回归闭环**：lessons.md 十条教训直接转成敲除触发场景库（一表两视图）——不依赖任何未决项，是最唾手可得的先行件。

## 6. 证据边界

- 代码事实（TIER1_COMPONENTS / 版本链 / harness 装配 / trace 字段 / .rules 规则结构）为本仓直读，行号以 2026-11-17 工作树为准，重构会漂移；
- 外部调研（learngenes / LLM 文化进化 / JCVI-syn3.0 等）可信度分级与三条负发现见概念对映研究 §7；
- 设计 v1→v2 的全部变更动因来自用户的两轮修正（trace 绑定、刹车盘点），非外部依据；
- 本汇总不含新事实，只做收敛。
