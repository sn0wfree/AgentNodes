# 三 Schema 合并规格 · GRL ∪ Registry ∪ Locus（草案 v0.9）

> **目标**：把三套 schema 合成**一个**对象模型——GRL 规则对象（并行会话，5526 条款实测）、Rule Registry 条目（规则基因组 v2 §2）、Locus 位点表条目（U4 §3.1 裁决五）——外加观测绑定方式的 schema 级约定。
> **状态**：**跨会话协商稿**——GRL 是并行会话的工件，本规格引用其全部实测结论；**升为 v1 前必须经归属会话确认**（别人做的不要动 = schema 也不单方面定版）。
> **上游**：[`GRL-基因规约语-20261118.md`](GRL-基因规约语-20261118.md)（§2 语法 / §3 语义 / §4 实测 / §5 缺陷 / §7 接口）· [`rule-genome-design-20261117.md`](rule-genome-design-20261117.md) §2–3 · U4 拍板记录（UPGF §3.1）。
> **口诀**：**一个对象模型 + 两个域扩展包 + 一张独立位点表 + 观测零内联。**

---

## 0. 一句话

三套 schema 不是三个标准，是**同一对象的三个投影**：GRL 投影了「条款语义」，Registry 投影了「治理与验证」，Locus 投影了「坐标系」——合并 = 把三个投影挂回同一根脊柱（`id` + `locus_id` + sha256 锚）。

## 1. 工件与真源矩阵

| 工件 | 拥有（真源） | 引用（不拥有） | 版本化 |
|---|---|---|---|
| **GRL 对象**（W 域条款 / S 域规则共用核心） | 语义槽位：modality / negation / verb / object / condition / scope / class / eval / allele_key | locus_id → 位点表；id → 自身 | 随编码批清单 |
| **Rule Registry**（S 域） | 治理：governance_layer / verifier / promotion_state / relations | 对象核心字段（真源在 GRL schema） | registry_sha256（git 入库，A3） |
| **Locus Table**（两域共用） | 坐标系：locus_id / definition / applies_to / status / stability | — | table_sha256（U4，批清单级引用） |
| **编码批清单**（W 域） | 编码 run 元数据：模型版本 / 时间 / table_sha256 / 统计 | 全部对象 | 批次 id |
| **trace / event_log**（观测） | rule_check / rule.observed / genome_snapshot | **只引用 id，不内联对象**（M4） | append-only |

**真源原则**：每个字段只有一个 owning 工件；其它工件要么引用（id / locus_id / sha256），要么不出现。

## 2. 六条合并决策

| # | 决策 | 内容 | 依据 |
|---|---|---|---|
| **M1** | **分层 schema：core + S-ext + W-ext** | 核心字段两域共用；S 域扩展包（verifier / 治理）与 W 域扩展包（vendor / 截断标记）各自附加。兼容规则照抄 protobuf：**只加字段、不改类型、枚举封闭**；两域扩展不强行同构 | 可行性研究 §8（自然不对称）；GRL §4.5 闭环质量依赖字段稳定 |
| **M2** | **tier ⊥ governance_layer 正交分离** | GRL `tier`（0 冻结 / 1 可提案 / 2 研究域）= **变异权限**；registry `layer`（L0 通用 / L1 项目 / L2 教训）= **治理层级**。两者正交：一条 L1 规则可以是 Tier 0（如安全底线），L0 原则也可以是 Tier 1。**禁止合并为一个字段** | 混用会把「谁能改它」和「它在哪层治理」两个维度压塌——这是本次对齐发现的最大解混淆点 |
| **M3** | **modality 统一到 GRL 六值有序量表** | `must(3) > should(2) > may/can(1) > unspecified(-1)`，registry 三值映射：MUST→must、禁止→must_not、SHOULD→should。**有序性是功能不是装饰**：GRL §3.4 强度守恒靠它机检「不许降级」 | GRL §3.1 实测（12711 次守恒检查 0 违规） |
| **M4** | **观测零内联** | rule_check / genome_snapshot 是独立 trace entry，**只引用对象 id**；对象里不出现 verdict / 观测者 / 会话字段。理由：观测是流，对象是库——混写等于重建平行账本 | 规则基因组 v2 三铁律之「零平行账本」的 schema 版 |
| **M5** | **allele_key 入 schema（计算字段）** | `allele_key = scope.key + modality + negation + verb`（**L2 分辨率**，GRL §4.4 实测：跨厂商共享量 L1=6 / **L2=35** / L3=3，单调性判据 C13）。存为派生字段（编码器写入），用于等位引用 `#D01~must` 与跨源对齐 | 等位键分辨率是**实测出来的**，不是设计的 |
| **M6** | **判据纪律继承** | 合并 schema 的校验器必须继承 GRL §5 的教训：① 命中量加**上限**检查（C8a 全集命中仍 PASS 的教训——「PASS ≠ 判据正确」）② **自证探针**（故意注入违规，检测器必须抓到，C14）③ 三态字符串禁与布尔混用（缺陷 #3） | GRL §5 第 7 条 = 本轮最有价值缺陷；量具校准（C10）的直接实现范本 |

## 3. 合并 schema v0.9 逐字段表

### 3.1 Core（两域必填/可选共用）

| 字段 | 类型 | 必填 | 枚举/格式 | 来源 | 备注 |
|---|---|---|---|---|---|
| `id` | string | ✅ | CURIE 预留（U2）：`<idspace>:<local>`；现行形态 `an:R-002` / `cl4r:ANTH-SAF-0007` | GRL id + U2 | idspace 注册表未建前允许裸 local id（标记 legacy） |
| `locus_id` | string\|null | ✅（null 合法） | 位点表主键；引用 `#D01` 形态 | 两者 + U4 | **null 是一等状态**（GRL §4.6：唯一归属率实测仅 54.6%） |
| `tier` | int | ✅ | 0 / 1 / 2 | GRL | 变异权限；Tier 0 不可被 override（编译器硬拒） |
| `class` | string | ✅ | regulative \| constitutive \| non-statement | GRL（SPDeconstruction 谱系） | |
| `modality` | string | ✅ | must / must_not / should / may / can / unspecified（**有序量表** 3/3/2/1/1/-1） | GRL | registry 三值映射见 §4 |
| `negation` | string | ✅ | yes / no / suspected（三态） | GRL | suspected = 低置信单列（实测占 4.4%） |
| `verb` / `object{key,raw}` / `condition` | — | W 必填 / S 可选 | 自由文本 + 归一键 | GRL | S 域规则散文强时可后补 |
| `scope{key,raw}` | object | ✅ | key 归一 + raw 原文（§3.3 规范化） | GRL | registry 无此字段——新增（原 trigger 拆入 condition+scope） |
| `eval[]` | string[] | ✅（可空数组） | `domain.metric` 形态 | GRL | 条款 ID ↔ 评测分数关联键（自研 #13） |
| `text_sha256` | string | ✅ | sha256 原文块 | registry | 原文锚 |
| `source_span{file,line_start,line_end}` | object | ✅ | 可回查 | registry（prompt-vc 式） | 回查逐字一致为机检 |
| `provenance{}` | object | ✅ | **四元组**：源文件/版本/抽取时间/抽取模型 + 域附加（vendor/file/surface） | registry+U4 纪律 | 校对酶替代（C6） |
| `allele_key` | string | 计算字段 | scope.key+modality+negation+verb | GRL §4.4 | 编码器写入；L2 分辨率 |

### 3.2 S 域扩展包（仅 Registry 对象携带）

| 字段 | 类型 | 枚举 | 备注 |
|---|---|---|---|
| `governance_layer` | string | L0 / L1 / L2-lesson / checklist | **与 tier 正交**（M2）；来源 AGENTS.md / rules.md 结构 |
| `verifier{type,command,anchor}` | object | type: script/regex/judge/human/null | registry「失败」字段结构化；null 不兜底 |
| `promotion_state` | string | active / proposed / deprecated（+replaced_by） | registry 现行 |
| `relations[]` | array（**预留 A5**） | depends_on / conflicts_with / …（PromptSys 7 关系候选） | v1 再定枚举 |
| `severity` | enum（**预留 A4**） | 待定 | 与 modality 正交 |
| `module{import[],override[]}` | object（**预留**） | GRL §2.3 语法 | S 域暂无模块需求；预留接口位 |

### 3.3 W 域扩展包（仅编码对象携带）

| 字段 | 类型 | 备注 |
|---|---|---|
| `vendor` / `file` / `surface` | string | provenance 附加面（多 surface = 同基因组多次转录，概念对映 §4.3） |
| `encoding_run_id` | string | → 编码批清单（模型版本/时间/table_sha256 在批级） |
| `truncated` | bool | 上游数据缺陷显式标记（GRL 缺陷 #5：语义截断 31.9%） |

### 3.4 Locus Table 条目（独立工件，照录 U4 裁决五）

`{locus_id, short_name, definition, text_sha256, applies_to:[S|W], status: confirmed|candidate|deprecated, replaced_by?, consider?, stability{mean_jaccard,runs,ari_global}, source: blind-discovery|prior-match|external-challenge|self-proposal, added_in_table_version}`

**与 GRL 的接口**：GRL `locus` 字段更名对齐为 `locus_id`（值即位点表主键）；位点引用 `#D01` 的解析源 = 位点表。

## 4. 枚举对齐表（registry 旧值 → 合并值）

| registry 旧 | 合并 schema | 备注 |
|---|---|---|
| MUST | `must`（强度 3） | |
| 禁止 | `must_not`（强度 3） | 否定三态通常同时置 yes，由编码器按 §3.2 跨槽探测裁定 |
| SHOULD | `should`（强度 2） | |
| —（无对应） | `may` / `can`（强度 1）/ `unspecified`（-1） | W 域实测高频，S 域暂少用 |
| layer L0/L1/L2 | `governance_layer`（保留） | 与 tier 正交（M2） |
| promotion_state active/proposed/deprecated | 保留 | 仅 rule 对象；locus 用 confirmed/candidate/deprecated（U4） |

## 5. 实例（合并 schema 下的三个对象）

```jsonc
// S 域 · 自仓规则（R-002 走查图的基因卡）
{
  "id": "an:R-002",
  "locus_id": null,                       // 待 W2 位点表定稿后归属；null 合法
  "tier": 1,                              // 可提案
  "class": "regulative",
  "modality": "must_not",                 // ← 旧「禁止直读」
  "negation": "yes",
  "scope": { "key": "env-access", "raw": "环境变量只经 settings 读" },
  "verb": "read",
  "object": { "key": "environment variable", "raw": "AGENTNODES_* 环境变量" },
  "condition": "任何需要读环境变量的代码",
  "eval": ["robustness.env_rename_grep_single"],
  "text_sha256": "3f9c…", "source_span": { "file": ".rules/rules.md", "line_start": 33, "line_end": 38 },
  "provenance": { "source_file": "AGENTS.md/.rules", "version": "v1.5.11", "extracted_at": "2026-11-20", "extracted_by": "rule_registry.py(M1)" },
  "allele_key": "env-access|must_not|yes|read",
  // S-ext
  "governance_layer": "L1",
  "verifier": { "type": "script", "command": "check_env_access.py", "anchor": "batch_gate 第 8 步" },
  "promotion_state": "active"
}
```

```jsonc
// W 域 · 厂商条款（GRL §2.1 原例的合并形态）
{
  "id": "cl4r:ANTH-SAF-0007",
  "locus_id": "D01",
  "tier": 0,                              // W 域 tier 表达「该厂商的自冻结强度」
  "class": "regulative", "modality": "must", "negation": "yes",
  "scope": { "key": "harmful-content-safety", "raw": "HARMFUL CONTENT SAFETY" },
  "addressee": "claude", "verb": "reproduce",
  "object": { "key": "copyright chunk", "raw": "large 20+ word chunks of content" },
  "condition": "when from search results",
  "eval": ["safety.refusal_rate", "copyright.no_large_quote"],
  "text_sha256": "…", "source_span": { "file": "Claude-4.1.json", "line_start": 210, "line_end": 214 },
  "provenance": { "vendor": "ANTHROPIC", "file": "Claude-4.1.json", "version": "snapshot 2026-11-09", "extracted_at": "2026-11-18", "extracted_by": "SPDeconstruction(gemini-flash-3_5)" },
  "allele_key": "harmful-content-safety|must|yes|reproduce",
  // W-ext
  "encoding_run_id": "enc-20261118-01", "truncated": false
}
```

```jsonc
// Locus Table 条目（两域共用坐标系）
{
  "locus_id": "D01", "short_name": "身份与人格",
  "definition": "声明 agent 是谁、以何种身份行事的条款",
  "text_sha256": "…",
  "applies_to": ["S", "W"],
  "status": "confirmed",                  // 靠盲发现簇匹配挣得（U4 裁决二）
  "stability": { "mean_jaccard": 0.82, "runs": 5, "ari_global": 0.71 },
  "source": "prior-match", "added_in_table_version": "lt-0.1"
}
```

## 6. 机检清单（合并 schema 校验器，继承 GRL §5 纪律）

1. **引用完整性**：`locus_id` 非 null 时必须存在于位点表当前版本；`replaced_by` 必须指向 active id；
2. **枚举封闭**：modality 六值 / negation 三态 / tier 三值 / class 三值 / status 按工件——禁止同字段布尔与枚举混用（GRL 缺陷 #3）；
3. **span 回查**：`source_span` 逐字一致；`text_sha256` 匹配；
4. **provenance 四元组非空**（C6）；
5. **allele_key 重算一致**：编码器写入值 = 字段重算值；
6. **命中量上限检查**：一切「按条件筛对象」的校验必须带上限断言（防全集命中静默退化，GRL C8a）；
7. **自证探针**：校验器自带注入靶子（故意坏一条，必须报错，GRL C14）；
8. **IDSPACE 注册检查**（U2 拍板后启用）：未注册 idspace 拒绝。

## 7. 兼容与迁移

- **GRL 5526 对象 → v0.9**：只加字段（allele_key / source_span / provenance 四元组扩展），protobuf 式向后兼容；`locus` → `locus_id` 是唯一改名（编码器一次性重写 + 批清单记版本）；
- **R-xxx → CURIE**：`an:` 前缀注册后双写过渡（legacy 标记），registry_sha256 变更走 A3 历史;
- **locus null 上界**：唯一归属率实测 54.6% ⇒ W1 编码时预期近半对象 `locus_id: null`——**这是正常状态**，位点表（W2）定稿后重编码提升归属率；
- **观测字段零迁移**：trace 侧从未内联对象字段，无迁移成本。

## 8. 待拍板接口位（本规格只留位，不定义）

| 接口位 | 挂靠决策 | 预留字段 |
|---|---|---|
| IDSPACE 注册表 | U2（§3.3 简报） | `id` CURIE 形态 |
| nanopub-lite 证据包 | U3（§3.4 简报） | `ext:` URI 指向的证据包 schema |
| 规则间关系 | A5 | `relations[]` |
| 严重度 | A4 | `severity` |
| 注入位置 | B9 | genome_snapshot（不在本对象） |
| 模块/覆盖语义在 S 域的启用 | —（GRL §2.3 已有语法） | `module{}` |

## 9. 诚实边界

- **本规格是协商稿**：GRL 字段语义（尤其 modality 量表、negation 三态、allele_key 分辨率）全部来自并行会话实测（5526 条款 / grl_check.py 18 判据），本规格**转述未复跑**——升 v1 前需归属会话确认；
- 实例中的 hash / span / eval 键为**示意值**（R-002 对象除 id/governance_layer/verifier 外字段为示意构造）；
- W 域 tier 语义（「厂商自冻结强度」）是本规格新引入的解释，未与 GRL 会话对齐——列为本规格自己的待确认点；
- 尚无任何工件实际按 v0.9 落盘；M1/W1 实施时若发现字段冲突，走预注册修订流程改版本号，不许就地改语义。
