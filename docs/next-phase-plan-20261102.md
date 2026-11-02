# 下一阶段重构升级计划（2026-11-02）

> 输入：`docs/roadmap-next-20261030.md`（R1–R6）逐条对代码复核 + 覆盖率实测 + 架构图 v6.5 对照。
> 前序：`docs/delivery-summary-r1-r6-20261030.md` §11–§16、`docs/plan-retry-fix-and-workflow-migration-20261031.md`。
> 分支：`refactor/v6-gateway`（本地领先 origin 2 个提交，网络恢复后推送）。
> 三道验收门沿用：**行为等价 / 测试不减 / 失败集不扩大**；新增第四道：**文档不得宣称未完成的事**。

---

## 0. 一句话结论

R1–R6 的**骨架已经完成**（fence 挂根、常驻消费者、steer 注入、老化、P-5 指针、单执行核），
但复核查出 **1 处配置声明未接线、4 处文档声明与代码不符、1 个口径失真**。
下一阶段的重点不是继续铺新功能，而是**把已宣称完成的东西真正做实**，然后再推剩下的三个真实缺口。

---

## 1. 复核结论：说完了 vs 做完了

### 1.1 真正完成（代码可验证）

| 条目 | 证据 |
|---|---|
| R1.1 fence 挂根 | `gateway/session_controller.py:143` claim、`:226` settle 前校验、`:228` emit `run.fenced`；INV-4 行为检查 `gateway/invariants.py:65-107` |
| R1.2 chat 驱动点勘查 | `docs/stage-r1-fence-wiring-design-20261030.md:25-45` |
| R2.1 常驻消费者 | `gateway/gateway.py:325` start_consumer / `:397` stop；宿主 `cli/commands/server.py:42` |
| R2.2 steer 真注入 | `orchestration/mailbox.py:155-182` steer 队、`:188-201` drain→`steer.applied`；react 安全点 `runtimes/nodes/react.py:103-116`；`steer.downgraded` 已零残留 |
| R2.3 优先级老化（逻辑） | `orchestration/mailbox.py:248-273` `_apply_aging`→`work.aged`；排序 `:358-377` |
| R4.2 P-5 指针四件套 | `storage/artifact_channel.py:29-48`、读回校验 `:116-130` |

### 1.2 声明了但没接线（**真缺陷**）

| # | 问题 | 证据 | 后果 |
|---|---|---|---|
| **A1** | `MailboxConfig.cap` / `aging_s` 从未传入 `EntryPoint` | `gateway/gateway.py:63-68` 构造 `EntryPoint` 只传 sink/is_active/on_interrupt；`config_hub.py:47-50` 声明 `cap=20` `aging_s=15.0` | `QNODES_MAILBOX__CAP` / `__AGING_S` **完全不生效**，恒用 `mailbox.py:64-66` 的默认值。R2.3「领先有界可配置」是空话 |

这是本轮唯一**新增**的真缺陷，且与之前 8 个「静默失效」同类。

### 1.3 文档声称完成、代码不足（**4 处**）

| # | 文档原话 | 实际情况 | 影响 |
|---|---|---|---|
| **B1** | 交付总结 `:83`「回测结果是第一个真实用户」 | 唯一登记 kind 是 `round_metrics`（`study/runner.py:728`）；**回测产物未接 P-3** | P-3 的验收例子不成立 |
| **B2** | 交付总结 `:92`「ModelHub 解析序集中」 | 只有 `gateway/singleton.py:39-42` 一处经 ModelHub；**~19 处**仍直调 `LLMConfig.load()`（api 6 处、cli 5 处、core/agent 3 处、strategy_acceptance 2 处等） | 「统一解析」不成立；改 provider 要改 19 个地方 |
| **B3** | 交付总结 `:86`「PROTOCOL_VERSION 成为事件信封唯一来源」 | `agent/event_store.py:512` 的 replay 走 `replay_rows` **不做版本校验**；`events/event_v2.py:345-354` 的 `to_row()` 不含 `schema_v`（靠 DDL DEFAULT） | 回放遇不兼容版本不会拒绝，是数据损坏风险 |
| **B4** | R4.1「P-3 登记接线」标完成 | `artifact_channel.py:103` status 恒 `"registered"`（无「两步可见」）；`platform_store.list_artifacts` **零生产调用方**（跨会话无出口） | 登记了但没人能查到 |

### 1.4 口径失真（**1 处**）

`cov_gate.py` 的 H 档用前缀 `core/` 兜底，实际吞了 **98 个文件 / 8,544 行**——
包括 `core/engine/`（回测引擎）、`core/utils/`、`core/permission/`、`core/skills/` 等
**完整子包**，而档位说明写的是「顶层单文件研究工具」。

实测：若要按架构把子包归位（A 补 session/permission/events/utils，B 补 engine/strategy_acceptance，
C 补 tools/skills/web/hooks/mcp/scheduled_research），结果是 **A 91.8% / B 89.7% / C 89.8% / H 81.2%**，
把红从 1 档变 3 档——**说明当前不是「H 特别差」，而是「H 兜底掩盖了 B/C 也刚好卡线」**。

结论：**不重划**（重划只是把问题挪位置），但要把 H 档的正名写清，并承认它就是「core 下未被 A/B/C 认领的一切」。

### 1.5 真实未完成（按触发条件挂着，不是缺陷）

| 条目 | 状态 | 触发条件 |
|---|---|---|
| R3.3 容量组闸 | 未建（`CapacityGroup` 全仓零命中） | 真实并发压力出现 |
| R5.1 cpu 节点 / R5.2 graph 子图 | 判定不做（`stage-r5-execution-plane-assessment-20261030.md`） | 有用户 |
| R5.4 subagent lane | 判定不做 | 同上 |
| R6.2 RunContext | 判定不做，约束散在 state 键 | 约束数量增长到难以追踪 |
| R6.4 Tier 0 冻结 | 未做（`study/graph_preset.py` 无 freeze/Tier） | preset 被误改过 |
| D2 提案通道消费方 | `study.directive` **零消费方** | 决定要做自主闭环时 |
| RL-1 每 session 分库 | 统一单库 | 单库 >2GB 或归档需求出现 |
| RL-2 L2 统一注册 | 11 套 Registry 并存 | — |

---

## 2. 覆盖率现状（实测，非估算）

```
A 平台内核   4,849 stmts · 434 miss · 91.0%  ✅
B 研究域     7,159 stmts · 705 miss · 90.2%  ✅
C 能力层    10,235 stmts · 978 miss · 90.4%  ✅
H 研究工具   8,544 stmts · 1,235 miss · 85.5%  ❌  需补 381 行
门禁合计    30,787 stmts · 3,352 miss · 89.1%
```

**口径外**（不受门禁约束，但影响「覆盖率极高」这个目标是否成立）：

```
D API        5,987 stmts · 1,508 miss · 74.8%
E CLI        5,481 stmts · 1,534 miss · 72.0%
全库        50,345 stmts · 12,782 miss · 74.6%
```

**H 档 TOP 缺口**（按需要补齐的行数排序）：

| 文件 | 语句 | 未覆盖 | 覆盖率 | 到 90% 需补 |
|---|---:|---:|---:|---:|
| `core/alpha_zoo_convert.py` | 549 | 137 | 75.0% | 83 |
| `core/autoresearch.py` | 403 | 122 | 69.7% | 82 |
| `core/backtest.py` | 314 | 102 | 67.5% | 71 |
| `core/data_import.py` | 172 | 88 | 48.8% | 71 |
| `core/scheduled_research/cli.py` | 242 | 71 | 70.7% | 47 |
| `core/engine/optimizers/base.py` | 121 | 49 | 59.5% | 37 |
| `core/strategy_acceptance/cli.py` | 69 | 44 | 36.2% | 38 |
| `core/data_readiness.py` | 189 | 48 | 74.6% | 30 |
| **合计（前 8）** | — | 661 | — | **459** |

前 8 个文件补完即可跨过 90%（当前缺 381）。

---

## 3. 执行计划

### P0 先说清楚（0.5d，无风险）

| # | 事项 | 产出 |
|---|---|---|
| **P0.1** | **修 A1**：`Gateway.__init__` 读 `load_platform_config()`，把 `mailbox.cap` / `aging_s` 传进 `EntryPoint`；`MailboxConfig.steer_cap` 也补进声明 | 2 个新测试：环境变量生效 / 非法值 fail-fast |
| **P0.2** | **修 B1–B4 的文档**：把交付总结里 4 处「已完成」降级为准确表述（或补代码，见 P2） | 交付总结 §17「复核勘误」 |
| **P0.3** | **正名 H 档**：`cov_gate.py` 注释 + 文档写清 H = 「core 下未被 A/B/C 认领的剩余」，不再叫「顶层单文件」 | 口径不再误导 |

### P1 覆盖率补齐到每档达标（2–3d）

按缺口排序补，**每补一个文件新增一次实测**：

| 批次 | 文件 | 预计新增测试 | 目标 |
|---|---|---:|---|
| P1-1 | `alpha_zoo_convert.py` AST 深分支 | ~30 | 75→90% |
| P1-2 | `autoresearch.py` `run_research_round` | ~25 | 70→90% |
| P1-3 | `backtest.py` 撮合/指标分支 | ~28 | 68→90% |
| P1-4 | `data_import.py` Tushare/iFinD/Fred 契约（mock 外部） | ~25 | 49→85%（远端 loader 不追） |
| P1-5 | `scheduled_research/cli.py` 命令分支 | ~15 | 71→90% |
| P1-6 | `engine/optimizers/base.py` + `strategy_acceptance/cli.py` | ~20 | 60→90% / 36→90% |
| P1-7 | `data_readiness.py` 剩余检查项 | ~10 | 75→90% |

**方法纪律**（沿用上一轮）：不为覆盖率写测试；每个模块先看未覆盖行是**真分支**还是**防御性 except**，
后者用 `# pragma: no cover` 标注而不是硬凑。每批结束跑一次全量 + `c1_gate` + `cov_gate`。

### P2 真实缺口：按触发条件决策（不排期，逐个拍板）

| # | 缺口 | 我的建议 | 理由 |
|---|---|---|---|
| **P2.1** | B3 replay 版本校验 | **建议修**（0.5d） | 数据损坏风险 > 成本；只加一个校验 + 1 测试 |
| **P2.2** | A2 每 session 分库 | 延后 | 重开条件未到（单库未见 >2GB） |
| **P2.3** | R3.3 容量组闸 | 延后 | 单消费者线程已串行化，无真实压力 |
| **P2.4** | R6.4 Tier 0 冻结 | 延后 | 等 preset 被误改过一次 |
| **P2.5** | D2 提案通道消费方（自主闭环最后一环） | **需要用户拍板** | 这是产品决策：Director 的提案要不要能自动落成下一轮 |
| **P2.6** | B1 回测接 P-3 / B4 list_artifacts 出口 | 延后 | 等有跨会话发现产物的真实需求 |
| **P2.7** | B2 ModelHub 收口 19 处 | 建议做（1d） | 分散的 `LLMConfig.load()` 是「改一处漏一处」的结构债 |

### P3 收尾

- 架构图 v6.6：把 P0/P1/P2 的成果翻格，并把三处陈旧格（ConfigHub 脱敏指纹等）修正
- 全量四道门 + e2e + browser lane
- 打 `v6.5-refactor-complete` tag（P5-1c 已退役，图无 ⏸）

---

## 4. 排序理由

1. **P0 优先**：A1 是「配置声明了但不生效」，与之前 8 个静默失效缺陷同类——**先止血**。
   B1–B4 是「文档撒谎」，不修就等于给下一轮留下错误前提（上一轮我自己就被误导过一次）。
2. **P1 次之**：覆盖率是用户目标里明确的一项（「覆盖率极高」），且 381 行缺口是可量化、可收敛的。
3. **P2 最后且需要拍板**：这些是产品决策不是技术债。**不主动铺**，避免「建而未接线」的新孤儿。
4. **P3 是仪式**：四道门 + tag，让这一阶段有明确的收口点。

---

## 5. 决策点（需用户拍板）

| # | 问题 | 选项 | 我的建议 |
|---|---|---|---|
| **D-A** | D2 提案通道消费方要不要做 | a 做（自主闭环收口，1–2d）/ b 挂着 | **b 挂着**——先看 P1 覆盖率补齐，产品决策单独拍 |
| **D-B** | B2 ModelHub 19 处收口要不要现在做 | a 现在做（1d）/ b 延后 | **a 现在做**——属于「结构清晰、扩展性好」这条主线 |
| **D-C** | 覆盖率是否扩到 D/E（API/CLI） | a 不扩（维持三档 + H）/ b 扩 | **a**——CLI 交互层投产比低；但要在文档里写明数字，不假装 |

---

## 6. 与 R1–R6 的对应

| R 阶段 | 本轮处置 |
|---|---|
| R1 接线收口 | ✅ 已完成（fence 挂根实测） |
| R2 chat 链补全 | ✅ 逻辑完成，**但 A1 配置未接线** → P0.1 |
| R3 入口全覆盖 | ⚠ 部分：CLI 已接、`mcp_request` 零调用方、容量闸未建 → P2.3 |
| R4 数据面 | ⚠ 部分：P-5 完整、P-3 只接了 study、PROTOCOL_VERSION 回放未校验 → P2.1 |
| R5 执行面 | ✅ 判定不做（有评估文档） |
| R6 IP 层 | ⚠ 部分：方法论已拆 method/、Director 产出无消费方、Tier 0 未冻结 → P2.4/P2.5 |
