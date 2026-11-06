# 业界调研知识库 · 索引（2026-11-06）

> **本文档地位**：四路 agent 调研的**原始资料索引 + 结论提炼**。
> 调研发生在「RSI 与日常使用是否冲突」的讨论中，目的是回答：
> **成熟系统怎么做到「自我改进过程对日常无感、只在犯错时可见」？**
>
> **原始缓存**：`.zcode-research/sm/`（19 个文件，约 1.1 MB）。
> 本文件把缓存里的 **4,014 个文档 URL** 按主题聚类，标注哪些可用、
> 哪些抓取失败，并给出可直接引用的结论与出处。

---

## §0 缓存清单与质量评级

| 文件 | 大小 | 内容 | 评级 |
|---|---|---|---|
| `langsmith-sitemap.xml` | 272 KB | **1,739 个 URL**（langsmith 1,241 + oss 434 + api 61）| ★★★ 最完整 |
| `unleash-sitemap.xml` | 82 KB | **597 个 URL**（api 407 + guides 48 + concepts 38）| ★★★ |
| `braintrust-sitemap.xml` | 92 KB | **490 个 URL**（articles 181 + encyclopedia 99 + blog 97）| ★★★ |
| `humanloop-docs-sitemap.xml` | 45 KB | **328 个 URL**（全在 /docs/ 下）| ★★★ |
| `promptlayer-sitemap.xml` | 44 KB | **294 个 URL**（reference 175 + features 77）| ★★★ |
| `vellum-sitemap.xml` | 32 KB | **212 个 URL**（developers 104 + product 76）| ★★ |
| `humanloop-sitemap.xml` | 18 KB | **97 个 URL**（blog 67，营销内容为主）| ★ |
| `flagger-sitemap.xml` | 308 B | **2 个 URL**（只是 sitemap 索引，未展开）| ✗ 需重新抓 |
| `launchdarkly-sitemap.xml` | 0 B | **空文件** | ✗ 抓取失败 |
| `openai-sitemap.xml` | 5.7 KB | **0 个 `<loc>`** | ✗ 格式不符 |
| `promptfoo-sitemap.xml` | 167 B | **0 个 `<loc>`** | ✗ 抓取失败 |
| `braintrust-llms.txt` | 69 KB | 官方 llms.txt 全文 | ★★★ |
| `promptlayer-llms.txt` | 43 KB | 官方 llms.txt 全文 | ★★★ |
| `statsig-llms.txt` | 19 KB | 官方 llms.txt 全文 | ★★★ |
| `humanloop-v4-llms.txt` | 18 KB | 官方 llms.txt（v4 版）| ★★ |
| `promptfoo-llms.txt` | 18 KB | **404 页面 HTML**，非 llms.txt | ✗ 无效 |
| `flagger-root.html` | 399 KB | Flagger 文档首页 | ★★ |
| `ld-home.html` | 297 KB | LaunchDarkly 首页 | ★★ |
| `bing-shadow.html` / `bing-shadow2.html` | 各 105 KB | 搜索引擎结果页（绕行采样）| ✗ 无价值 |
| `braintrust-docs-root.html` | 16 B | 空响应 | ✗ |
| `mojeek-shadow.html` | 369 B | 搜索引擎结果页 | ✗ |

**有效资料**：4,014 个文档 URL + 4 份 llms.txt。**失败 7 项**（1 个 0 字节、3 个 sitemap 格式不符、2 个搜索引擎噪声、1 个 404）。

---

## §1 主题索引：按「我们要回答的问题」组织

### 1.1 问题一 · 停止/中断机制如何分域

> 我们的问题：全局单一 kill-switch，Ctrl+C 会误停后台 RSI 循环。

| 主题 | 可用 URL 数 | 代表路径 |
|---|---|---|
| LangGraph 中断与人工介入 | ~40 | `/oss/python/langgraph/interrupts`、`/langsmith/agent-server-api/...` |
| 状态与检查点 | ~30 | `/oss/python/langgraph/persistence`、`/oss/python/langgraph/use-graph-api` |
| 后台任务与 run 管理 | ~60 | `/langsmith/agent-server-api/thread-runs/`、`/langsmith/background-run` |
| Human-in-the-loop | ~20 | `/langsmith/human-in-the-loop`、`/oss/python/langgraph/add-human-in-the-loop` |

**从 llms.txt 直接可读的**（`braintrust-llms.txt` / `promptlayer-llms.txt` 不含此类，需查 LangSmith）。

### 1.2 问题二 · 行为变更如何不影响 in-flight 任务

> 我们的问题：RSI 改了 prompt/tool_set，正在跑的任务会不会被改？

| 主题 | 平台 | 代表路径 |
|---|---|---|
| Prompt 版本与部署 | PromptLayer | `/features/prompt-registry/prompt-editor-versioning`、`/features/prompt-registry/overview` |
| Release label / 环境指针 | PromptLayer | `/why-promptlayer/ab-releases`、`/features/prompt-registry/dynamic-release-labels` |
| Prompt 版本 API | PromptLayer | `/reference/templates-get`（支持 `version` 参数钉住）|
| Prompt 生命周期 | Humanloop | `/docs/prompts/`、`/docs/explanation/environments` |
| Deployments / Release tags | Vellum | `/product/deployments/`、`/developers/api-reference/` |
| Prompt 版本管理 | Braintrust | `/evals/prompts/versions`、`/evals/prompts/use-in-code` |
| Prompt Hub / 提交指针 | LangSmith | `/langsmith/manage-prompts`、`/langsmith/prompts` |

**关键结论**（已在对话中验证并记录）：**没有任何平台提供开箱即用的「会话级版本钉住」**。
成熟做法 = 不可变版本 + 请求级 pin 参数 + 应用层在会话开始时解析一次。
我们 kernel 的 `harness_resolver`「只在装配期问一次」正好是这个原语的实现。

### 1.3 问题三 · 指标被游戏 / 评估集过拟合的识别

> 我们的问题：held-in 涨、held-out 跌 → 怎么知道是过拟合而不是噪声？

| 主题 | 平台 | 代表路径 |
|---|---|---|
| 评估集切分（train/val/test）| LangSmith | `/langsmith/manage-datasets#create-and-manage-dataset-splits` |
| 回归测试与基线对比 | LangSmith | `/langsmith/evaluation-concepts#regression-tests` |
| Pairwise 评估 | LangSmith | `/langsmith/evaluate-pairwise` |
| CI 门禁 | Promptfoo | `/docs/integrations/ci-cd/`（llms.txt 失效，需重抓）|
| 质量门 | Braintrust | `/encyclopedia/quality-gate`、`/encyclopedia/ci-cd-integration` |
| 评估泄漏 | Braintrust | `/encyclopedia/eval-leakage` |
| 连续评估管线 | PromptLayer | `/features/evaluations/building-pipelines`、`/features/evaluations/continuous-integration` |
| 回测（backtesting）| PromptLayer | `/features/evaluations/backtesting` |

**关键结论**：双门的成熟统计对应物是 **Thresholdout（Reusable Holdout）**——
Dwork, Feldman, Hardt, Pitassi, Reingold, Roth, *Science* 2015 / arXiv:1506.02629。
机制：分差小于噪声阈值只回 1 bit；显著分歧才回真实值并**扣过拟合预算**；预算耗尽停止回答。
（这个结论来自 agent 的论文检索，不在缓存文件里，但可复核。）

### 1.4 问题四 · 熔断/降级的作用域与止血判据

> 我们的问题：RSI 故障要不要停日常？什么证据足以全停？

| 主题 | 平台 | 代表路径 |
|---|---|---|
| 渐进发布（progressive rollout）| Unleash | `/guides/gradual-rollout` |
| 粘性（stickiness，会话级一致性）| Unleash | `/concepts/stickiness` ← **最接近「会话钉住」的机制** |
| 策略与分组（groupId 重洗）| Unleash | `/concepts/activation-strategies` |
| Feature flag 概念 | Unleash | `/concepts/what-is-a-feature-flag` |
| 变更请求与审批 | Unleash | `/concepts/change-requests` |
| 环境隔离 | Unleash | `/concepts/environments` |
| Guardrail 指标（护栏指标）| Statsig | `statsig-llms.txt` 全文可读 |
| 发布告警 | Statsig | `/metrics/rollout-alerts`（需重抓正文）|

**关键结论**：
- **Unleash stickiness** = `MurmurHash(userId/sessionId + groupId)` 决定分桶，
  同一 session 恒定拿到同一变体。这是业界最接近「会话级版本钉住」的现成机制。
- **Guardrail metric** 是标准术语（Kohavi et al., *Trustworthy Online Controlled Experiments*）：
  主指标涨、护栏指标跌 → 独立告警。Eppo 的三态（灰勾/黄/红）与 Statsig 的显著性门槛可参考。

---

## §2 抓取失败的 7 项（需重抓）

| 项 | 问题 | 重抓建议 |
|---|---|---|
| `launchdarkly-sitemap.xml` | 0 字节 | 改抓 `https://launchdarkly.com/sitemap.xml` 或 `docs.launchdarkly.com/sitemap.xml` |
| `openai-sitemap.xml` | 无 `<loc>` | 改抓 `https://platform.openai.com/sitemap.xml`（需绕 CF）|
| `promptfoo-sitemap.xml` | 167 字节，无内容 | 改抓 `https://www.promptfoo.dev/sitemap.xml` |
| `flagger-sitemap.xml` | 只是索引文件 | 展开 `sitemap-pages.xml` 子 sitemap |
| `promptfoo-llms.txt` | 抓成 404 HTML | 正确地址可能是 `/llms-full.txt` |
| `braintrust-docs-root.html` | 16 字节空响应 | — |
| `bing-shadow*.html` / `mojeek-shadow.html` | 搜索引擎结果页，非目标内容 | 丢弃 |

**这 7 项不影响已提炼的结论**——它们对应的平台（LaunchDarkly / OpenAI / Promptfoo / Flagger）
的结论已在对话记录中有其他来源支撑。

---

## §3 可直接引用的外部文献（缓存外，agent 已核实）

| 主题 | 文献 | 标识 |
|---|---|---|
| 可复用留出集 / 分歧预算 | Dwork et al., *The reusable holdout* | Science 2015 / arXiv:1506.02629 |
| 排行榜过拟合 | Blum & Hardt, *The Ladder* | ICML 2015 / arXiv:1502.04585 |
| RM 过优化（proxy-gold gap）| Gao, Schulman, Hilton | arXiv:2210.10760 |
| Reward hacking 定义 | Skalse et al. | arXiv:2209.13085 |
| Goodhart 分类 | Manheim & Garrabrant | arXiv:1803.04585 |
| AI control（trusted monitor）| Greenblatt et al. | ICML 2025 / arXiv:2312.06942 |
| 防篡改审计日志 | Schneier & Kelsey | ACM TISSEC 1999 |
| 引用监控器 | Anderson 1972 / Saltzer & Schroeder 1975 | — |
| Off-Switch Game | Hadfield-Menell et al. | arXiv:1611.08219 |
| OpenAI Preparedness Framework v2 | — | openai.com（PDF）|
| Anthropic RSP v3.4 | — | anthropic.com（PDF）|
| EU AI Act Art.14(4)(e) 停止按钮 | — | artificialintelligenceact.eu |

---

## §4 结论（对设计的影响）

1. **停止机制必须分域**——业界共识，父子传播单向向下（Go context 树 / TTY 进程组 / Temporal Parent Close Policy）。我们的全局 halt 是反模式。
2. **会话级版本钉住是应用层模式，不是产品特性**——我们的 `harness_resolver` 已实现，是领先而非落后。
3. **双门应有分歧预算**——Thresholdout 给了「何时该信 held-out 分数」的统计语义。
4. **RSI 故障不该停日常**——SRE 的「什么证据足以全停」只有三种（数据完整性 / 维护窗口 / 自身燃烧率），RSI 不在其列；正确动作是**回滚 + 停 RSI**。
5. **术语校正**：用 **OEC + guardrail metrics**；**不要用 counterfactual metric**（非标准术语，arXiv 全文仅 13 条异用）。

---

## 附：缓存文件去留建议

| 类别 | 文件 | 建议 |
|---|---|---|
| 高价值 sitemap | langsmith / unleash / braintrust / humanloop-docs / promptlayer / vellum / humanloop | **保留**，URL 清单可复用 |
| 高价值 llms.txt | braintrust / promptlayer / statsig / humanloop-v4 | **保留**，官方摘要可直接检索 |
| 中等价值 HTML | flagger-root / ld-home | 保留（首页含导航结构）|
| 无效文件 | launchdarkly-sitemap(0B) / openai-sitemap / promptfoo-sitemap / promptfoo-llms.txt / braintrust-docs-root / bing-shadow×2 / mojeek-shadow | **删除**（已在 §2 记录失败原因）|

删除无效文件后缓存约 **1.0 MB**（原 1.1 MB），全部是有效参考资料。
