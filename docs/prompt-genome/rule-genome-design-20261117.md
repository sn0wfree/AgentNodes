# 规则基因组 · Rule Registry 设计 v2（trace 绑定版）

> **提案**：把 AGENTS.md / `.rules/rules.md` 的每条规则当作**基因片段**；规则=基因、行为=表型；归因「哪段行为由哪条规则起效」。
> **v2 变更（本版核心）**：**砍掉独立 Expression Ledger**。规则观测不再是平行账本，而是**长在现有 trace 主干上的一等公民**——全链只有一套既有 ID（`session_id` / `iter` / `call_id` / `genome_version_id` / `proposal_id`），唯一新增键是 `rule_id`，且必须锚回 registry 的 sha256。
> **状态**：设计稿（未实施 · 未确认）。本文只定规范；实现需另行确认。
> **上游**：
> - `提示词与基因组-概念对映研究-20261117.md`（同文件夹 · 概念框架、归因三法、双层位点）
> - `提示词基因框架-业界先例调研-20261117.md`（同文件夹 · 自研项 #13「条款 ID ↔ 评测分数关联键」——本设计就是它的项目内实现）
> - 理念七：检查信号必须经由被检方不可控的通道产生

---

## 0. 为什么 v2 要 trace 绑定

v1 设计了独立的 `rule_expression` 事件账本。问题：**平行账本必然产生对账负担**——行为轨迹在 `trace.jsonl`，规则判定在 ledger，两套时间线要靠 session_id 事后 join，且 ledger 与被观测行为点（具体到第几轮迭代、哪次工具调用）没有绑定，归因永远停在"这个 session 提到过 R-009"的粗粒度。

trace 基建（本仓库已实测）恰好提供了缺的绑定粒度：

| 既有资产 | 位置 | 已有的 ID / 结构 | v2 用它绑什么 |
|---|---|---|---|
| **TraceWriter**（crash-safe JSONL + sidecar blob） | `kernel/agent/telemetry/trace.py` | 每条 entry 带 `type` / `iter`（迭代号）/ `tool` / `call_id` / `status`；已有可选 ref 字段先例（`artifact_refs` / `policy_decision_id` / `data_audit_id`，`trace.py:50-57`） | 规则观测以新 entry 类型落 trace，**同 `iter` 绑定行为点** |
| **EventBusV2**（typed 信封 → `event_log` 表） | `kernel/events/event_v2.py` | `aggregate_id` + `seq` + dot-namespaced `type` + freeform data；append-only 投影 | 进程外观测（不发生在会话内）走事件总线，绑 `proposal_id` / `commit` |
| **GenomeVersionStore**（版本链+游标） | `kernel/orchestration/genome_version.py` | `version_id`，activate 强制携带 `proposal_id` | 每次会话的「基因快照」挂 `version_id` |
| **GenomeProposal** | `kernel/evolution/genome_proposals.py` | `evidence_refs: list[str]`（自由格式） | v2 把它规范化成可 join 的 URI |
| **evals/scorecard**（70 golden + 指纹） | `evals/` | 指纹已补（framework v1.2） | 指纹并进 `genome_version_id` + `trace_ref` |

**设计原则（v2 三条铁律）**：
1. **零平行账本**——规则观测只有两种落点：会话内 → `trace.jsonl` entry；会话外 → `EventV2` 事件。
2. **零新增 ID 体系**——join 只用既有 ID；`rule_id` 是唯一新增键。
3. **观测者≠被检方**——`rule_check` / `rule.observed` 的写入方永远是 kernel 侧或进程外脚本，被检 agent 无写入口（理念七）。

---

## 1. ID 脊柱（全链绑定图）

```
rule_registry.json                    registry_sha256 = 基因组目录的版本号
  rule_id ─ text_sha256 ─ layer ─ verifier        │
      │                                           │
      ▼ 转录（G→E）                                │
trace.jsonl : genome_snapshot                      │
  { genome_version_id, registry_sha256,           │
    rule_refs[{rule_id, text_sha256}] }            │  ← 本次会话携带的基因清单
      │                                           │
      ▼ 行为（E）                                  │
trace.jsonl : tool_call / tool_result              │
  { iter, call_id, tool, status }                  │
trace.jsonl : rule_check                           │
  { rule_id, verdict, channel, recorded_by,       │
    evidence{iter, call_id?, span} }               │  ← 同 iter = 同一行为点，天然 join
      │                                           │
      ▼ 表型（P）                                  │
scorecard / goal journal                           │
  { genome_version_id, trace_ref, rule_refs? }     │
      │                                           │
      ▼ 归因（回写 G）                              │
GenomeProposal.evidence_refs（v2 规范化 URI）        │
  ["rule:R-009@ab12cd34",                          │
   "trace:<session_id>#iter=7&call_id=<id>",      │
   "eval:<scorecard_run_id>"]                      │
      │ activate（强制携带 proposal_id）             │
      ▼                                           │
GenomeVersionStore → 新 version_id ────────────────┘ 下一轮 genome_snapshot 更新
```

**归因查询 = 纯 join，零新存储**：`rule:R-009` → 所有带它的 `genome_snapshot` → 对应 session 的 `rule_check`（verdict 分布）→ `evidence.iter/call_id` 回放行为点 → scorecard 对比 → 提案与 Δeffect。

---

## 2. Rule Registry（位点表，v1 方案保留）

单一真源：`AGENTS.md` + `.rules/rules.md` --解析--> `rule_registry.json`（整体 sha256 = `registry_sha256`）。

```jsonc
{
  "registry_sha256": "…",                  // 目录版本号；trace/snapshot 引用的是它
  "rules": [{
    "rule_id": "R-002",
    "layer": "L1",                          // L0 | L1 | checklist | lesson
    "short_name": "环境变量只经 settings 读",
    "modality": "MUST",                     // MUST | SHOULD | 禁止
    "trigger": "任何需要读环境变量的代码",
    "action": "在 foundation/settings.py 的 ENV_TABLE 声明…",
    "verifier": {"verifier_type": "script", "command": "check_env_access.py", "anchor": "batch_gate 第 8 步"},
    "source": ["L2 2026-11-09（批 A/B'）"],
    "promotion_state": "active",
    "text_sha256": "…",                     // 规则原文块 hash
    "source_span": {"file": ".rules/rules.md", "line_start": 33, "line_end": 38}
  }]
}
```

解析验收（机检，同 v1）：L1 `R-\d{3}` 100% 抽出；span 回查逐字一致；`verifier.command` 指向的脚本必须存在（悬空 verifier = 显式报错）；verifier 为 null 不兜底。

## 3. Trace 绑定层（v2 核心）

### 3.1 转录绑定：`genome_snapshot` entry

会话装配完成时（`harness_resolver.assemble()` 之后、首轮推理之前），由 kernel 侧写一条 trace entry：

```jsonc
{"type": "genome_snapshot",
 "genome_version_id": "gv-…",           // 来自 GenomeVersionStore.current()
 "registry_sha256": "…",
 "rule_refs": [{"rule_id": "R-001", "text_sha256": "…"}, …],
 "assembled_from": {"prompt_version": "…", "skills": ["…"]}   // 装配链（上轮机检项的落地）
}
```

- 语义：**本次会话携带了哪些基因**。此后本 session 一切观测都能反查「当时生效的规则集」——knockout 实验（变体规则集）天然可区分，因为 `registry_sha256` 不同。
- 一次会话一条；重装配（harness 切换）追加新条目，不覆盖。

### 3.2 行为绑定：`rule_check` entry

任何观测者产出判定时，写同格式 trace entry：

```jsonc
{"type": "rule_check",
 "rule_id": "R-002",
 "verdict": "violated",                  // complied | violated | uncertain
 "channel": "script",                    // script | regex | judge | human
 "recorded_by": "scripts/arch/check_env_access.py@batch_gate",
 "evidence": {
   "iter": 7,                            // ★ 与被观测行为点同迭代号（join 键）
   "call_id": "toolu_…",                 // 可选：绑定到具体 tool_call
   "span": "src/foo.py:12",              // 可回查指针
   "excerpt": "os.environ.get(…)"        // ≤200 字符，超出走 sidecar（复用 TraceWriter 卸载机制）
 },
 "registry_sha256": "…"
}
```

**join 语义**：`rule_check.evidence.iter` == 同 session `tool_result.iter` ⇒ 该判定锚定到具体行为点。粗粒度判定（无 call_id）允许存在，但**报告必须区分「锚定到行为点」与「仅锚定到会话」两级置信**。

### 3.3 会话外绑定：EventV2 `rule.observed`

batch_gate / 进程外 verifier 的观测对象是**代码库或提案**，不是会话——走事件总线而非 trace.jsonl：

```jsonc
EventV2{ aggregate_id: "proposal:gpr-…" | "commit:<sha>",   // 绑提案或 commit，不绑 session
         type: "rule.observed",
         data: { rule_id, verdict, channel, recorded_by,
                 evidence{span, excerpt}, registry_sha256 } }
```

- 进程外脚本输出统一 JSON 记录 → kernel 侧 ingest 器转写为 `rule.observed` 事件（脚本本身不依赖 kernel，保持进程外不可控性 ✅ 理念七）。
- 提案验证（validation_gate）产生的观测绑 `proposal_id`；CI 门禁绑 `commit`。

### 3.4 表型绑定：scorecard / journal

- eval run 记录增补两字段：`genome_version_id`（该次 eval 跑在哪个基因组上）与 `trace_ref`（关联 trace 目录标识）。
- 现有评估指纹把这两个字段并入指纹输入——基因组变了指纹必变（检测「悄悄换基因再跑分」）。
- goal journal 的证据条目同样可带 `rule_refs`（该 journal 证据关联到哪些规则的期望行为）。

### 3.5 归因回写：`evidence_refs` URI 规范化

`GenomeProposal.evidence_refs: list[str]`（现状自由格式）v2 规范化为三种 URI，可机检：

| scheme | 形式 | 指向 |
|---|---|---|
| `rule:` | `rule:R-009@<sha8>` | registry 中的规则版本（@sha 缺失判失败——不锚版本的规则引用 = 半失效） |
| `trace:` | `trace:<session_id>#iter=7&call_id=<id>` | trace 行为证据点（iter/call_id 可省略，省略即降置信） |
| `eval:` | `eval:<scorecard_run_id>` | 表型分数 |

解析器拒绝未知 scheme（fail loud，对齐 EventV2 的 EventType 注册表纪律）。

## 4. Attribution（归因三法，v2 全部变成 join）

| 方法 | v2 做法（零新存储） | 产出 |
|---|---|---|
| **表达谱** | 只读 join：`rule_registry` × `genome_snapshot` × `rule_check` 按 `rule_id` 聚合 | 每规则的表达次数 / 遵守率 / 两级置信占比；零表达规则 → 候选降级评审名单 |
| **敲除** | 变体 registry（删 R-x）→ 新 `registry_sha256` → knockout 会话的 `genome_snapshot` 自动携带变体哈希 → 与原版 scorecard 按 `genome_version_id` 对比 | Δeffect；本底对照（敲除后分数不变 ⇒ 行为来自权重先验）。**变体与原版无需任何标记区分，`registry_sha256` 自证身份** |
| **关联键** | `rule_id` × scorecard（经 `genome_version_id` join）× `proposal.evidence_refs` | 「改 R-x → 哪个 benchmark 动了 → 哪份提案声称以此为据」全链可追溯 |

归因语义边界（同 v1，不变）：结论是**证据性**的；多效性（一规则→多行为）与上位性（规则簇联合）是多对多常态；报告不得写成单射。

## 5. 通道矩阵（理念七合规表）

| 通道 | 观测者 | 落点 | 绑定键 |
|---|---|---|---|
| **script**（硬） | 进程外 `scripts/arch/` 门禁（已有） | `EventV2 rule.observed`（经 ingest） | commit / proposal_id |
| **regex**（硬） | kernel 侧 hook（会话内行为锚点） | `trace.jsonl rule_check` | session_id + iter (+call_id) |
| **judge**（软，M2 试点） | kernel 侧 judge 进程（绝不能是被检 agent 自报） | `trace.jsonl rule_check`（channel=judge） | session_id + iter |
| **human** | 人工抽查 | `trace.jsonl rule_check`（channel=human）或 EventV2 | 按上下文 |

uncertain 是一等状态；judge 一致性未达标不放量（Fleiss κ 参考 0.25–0.29，上轮调研 #23）。

## 6. 分期实施（v2）

| 期 | 内容 | 验收（机检） |
|---|---|---|
| **M1** | `rule_registry.py` 解析器 + `genome_snapshot` entry 落点（装配后写）+ `evidence_refs` URI 校验器 | registry 抽出率 100%、span 回查一致；任意新会话的 trace.jsonl 首条即 genome_snapshot 且 rule_refs 与 registry 一致；含坏 scheme / 缺 @sha 的 evidence_refs 被解析器拒绝 |
| **M2** | `rule_check` entry（regex 通道 2–3 条试点）+ EventV2 `rule.observed` ingest 器 + 表达谱 join 报告 | 试点规则的 rule_check 与 tool_result 同 iter 可 join；ingest 后 event_log 可查 `rule.observed`；表达谱报表产出且零事件规则入选评审名单 |
| **M3** | knockout：变体 registry 生成器 + 触发场景库（每规则 ≥3）+ Δeffect 报告 + scorecard 指纹并入 genome_version_id | ≥3 规则的 Δeffect 报告含本底对照列；指纹对基因组变化敏感（改 registry 指纹必变）；报告可从 proposal 回放到 trace 证据点 |

## 7. 与 GenomeProposal / 晋升的对接（同 v1，补 trace 维度）

- 规则 = prompt/skill 工件内的**条款级位点**；修改规则以工件级 `GenomeProposal(target_component="prompt", change={rule_id, diff})` 生效。
- validation_gate 产出的 `rule.observed` 事件绑 `proposal_id`——**提案的验证证据直接长在事件总线上**，不再是提案字段里的自由文本。
- 表达谱数据化晋升：零表达 × 零遵守 → 候选降级（先排除「触发场景未覆盖」假阴性再动）。

## 8. 安全注记

- knockout 变体 registry 与变体 AGENTS.md **只在本地临时目录**构造；AGENTS.md 本体是 gh-gist 托管 L0，**严禁把变体 `gh gist edit` 推回**。
- trace entry 的 `excerpt` ≤200 字符、指针优先，避免把不可信外部文本整段入库（CL4R1T4S 教训）；大字段复用 TraceWriter sidecar 机制。
- `rule_check` / `genome_snapshot` 的写入方不含被检 agent 的自由输出通道；agent 无法伪造自己遵守（理念七）——若未来 agent 侧需要申请记录，只能走「申请 → kernel 复核 → kernel 落笔」路径。
- 观测层改造**不得改变门禁的失败语义**（记账是加路径，不是替换 fail loud）。

## 9. 不做清单（同 v1 + v2 新增）

- 不做自由行为流的事后归因（judge 试点后另行评估）。
- 不做跨规则效果排名（触发场景不同质，效应量只在同场景内可比）。
- **不做规则观测的独立存储/独立查询服务**（v2 新增铁律）——一切归因都是对 trace/event_log/scorecard 的只读 join；发现需要新表时，先证明现有三个存储无法 join，再上方案评审。

---

## 10. 未决问题清单（2026-11-17 盘点 · 阻塞 M1 前必须逐个拍板）

> 状态：`OPEN`（未讨论结论）。★ = 体系定型级，不拍板不动工。

### A 组 · 基因本体

| # | 问题 | 阻塞 | 状态 |
|---|---|---|---|
| A1 | 规则原子化边界（散文原则/簇/表格行，切多细） | M1（registry schema 定稿前） | OPEN |
| A2 | rule_id 谱系：合并/拆分/改名/删除时历史 trace 归属（orthology） | M1 | OPEN |
| A3 | registry 自身历史版本化：老 trace 的 `registry_sha256` 必须永远可解析（入库+git 不变量） | M1 | OPEN |
| A4 | 严重度字段（modality 之外加 severity？） | M2 | OPEN |
| A5 | 规则间关系建模（depends_on / conflicts_with / 冗余簇；PromptSys 7 关系可借） | M2 | OPEN |

### B 组 · 转录语义

| # | 问题 | 阻塞 | 状态 |
|---|---|---|---|
| B6 ★ | 「在场」定义：全文注入 ⇒ 全规则永远在场 ⇒ genome_snapshot 无信息量；是否升级为按 task_signature 的**选择性转录**（牵动 harness_resolver） | M1 | OPEN |
| B7 | 动态转录：compaction 后规则出窗，snapshot 需追加；compaction 本身是 Tier 1 组件 | M1 | OPEN |
| B8 | 显式声明放弃「表达强度」中间层（可观测的是 在场+行为符合 两端） | M1（文档决策） | OPEN |

### C 组 · 测量学（metrology）

| # | 问题 | 阻塞 | 状态 |
|---|---|---|---|
| C9 ★ | 表型粒度对齐：任务级 scorecard 里怎么拆规则级效应（测量模型） | M3（但 M1 schema 需预留） | OPEN |
| C10 ★ | 量具校准协议：judge 一致性 / regex 查准查全 / verifier 自身的测试——校准样本集制度 | M2 | OPEN |
| C11 ★ | 统计功效：n、检验选型、多重比较校正（Benjamini–Hochberg FDR，上轮调研 #12 参照） | M3 | OPEN |
| C12 | 观测层自身的回归防线（校准样本回归） | M2 | OPEN |

### D 组 · 因果边界

| # | 问题 | 阻塞 | 状态 |
|---|---|---|---|
| D13 | 功能冗余与合成致死：单敲除无表型 ≠ 无用；双敲除做不做 / 结论边界声明 | M3 | OPEN |
| D14 | Hawthorne 效应声明：规则在场即干预，归因解释带尾巴 | M3（文档决策） | OPEN |
| D15 | 拮抗观测：规则冲突时的行为检测与记录 | M2 后 | OPEN |

### E 组 · 规模生态

| # | 问题 | 阻塞 | 状态 |
|---|---|---|---|
| E16 ★ | 适用范围：单 agent 先行 / 全 swarm（per-role 快照、主体归属） | M1 | OPEN |
| E17 | 冷启动观察窗口：多久才能判「零表达」 | M2 | OPEN |
| E18 | judge token 预算；trace 膨胀与保留策略（trace 有无 GC 未查） | M2 | OPEN |
| E19 | 范围拍板：本仓自测 vs 含跨源对齐（CL4R1T4S 桥接另立项目？） | M1（文档决策） | OPEN |

### F 组 · 流程与人

| # | 问题 | 阻塞 | 状态 |
|---|---|---|---|
| F20 | 事故→回归闭环：L2 教训台账与触发场景库同源（一表两视图） | M3 | OPEN |
| F21 | 表达谱报表的消费者/节奏：对接现有 3 个月审查周期 | M2 | OPEN |
| F22 | 仪器自身（解析器/ingest/judge）进不进基因组管理（进程外自由身 vs 提案管控） | M2 | OPEN |

**建议拍板顺序**：B6 → C9/C10/C11 → A3 → E16 → 其余按分期前就位。
