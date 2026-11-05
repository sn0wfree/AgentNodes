# Agentic 设计框架与 Prime Agent 调研报告

## 目录

1. Agentic 设计框架：是什么 \& 如何使用

2. 主流框架最新版本与社区活跃度

3. Prime Agent 项目深度调研

4. P0 \#1 Plan\-and\-Execute 代码改造方案

---

## 一、Agentic 设计框架：是什么 \& 如何使用

### 1\.1 定义

**Agentic AI（智能体 AI）** 是 AI 从「被动问答工具」向「主动执行实体」演进的新范式。论文《Agentic AI Frameworks: Architectures, Protocols, and Design Challenges》给出的定义：

> 一种具备**自主性与协作性**的实体，拥有推理与沟通能力，能够动态解读结构化语境、编排工具，并通过分布式系统中的记忆与交互调整行为。
> 
> 

与传统 AI 的核心区别：

- **传统 AI**：执行预定义脚本，输入→输出，无状态

- **Agentic AI**：理解目标→自主规划→调用工具→迭代调整→达成结果

### 1\.2 设计理念的转变

Agentic 设计不是「给 AI 套个 UI」，而是需要重新思考整套交互范式：

|维度|传统软件 / 聊天机器人|Agentic 系统|
|---|---|---|
|输入空间|有限、明确|开放、无限|
|输出可预测性|确定|不可完全预测|
|交互模式|菜单 / 表单 / 固定流程|动态对话 / 协作式|
|用户角色|操作者|监督者 / 合作者|
|核心挑战|功能完整性|信任 \+ 透明度 \+ 可控性|

### 1\.3 CITE 设计模型

Wordware 提出的 **CITE 框架**是目前最系统的 Agentic 设计方法论，从四个维度定义智能体的设计：

**Context（上下文）——「它知道什么？」**

Agent 对环境的动态理解，包括当前世界状态、已执行步骤、历史决策。

- 不是静态的：是持续更新的信息综合体

- 关键挑战：信息规模 vs 相关性的平衡，避免信息过载

- 设计要点：分层记忆（短期/长期/语义/情景）、动态检索、上下文压缩

**Intent（意图）——「它想要什么？」**

连接「知道什么」和「需要做什么」的蓝图，是 Agent 行动的导向。

- 协作式确立：不做假设，通过对话澄清和细化理解

- 动态调整：新信息出现时重新评估目标

- 设计要点：目标拆解能力、澄清式提问、进度对齐机制

**Tools（工具）——「它能做什么？」**

Agent 达成目标的执行手段：API、数据库、专用函数、甚至其他 Agent。

- 广度决定能力边界，深度决定执行效果

- 设计要点：工具发现机制、错误处理与降级、延迟优化、限流与容错

**Experience（体验）——「如何与它交互？」**

所有组件的交汇点，塑造人机信任关系。传统 UX 原则在这里常常不够用。

- 界面不是静态展示，而是 Agent 与用户之间的动态互动

- 设计要点：推理过程可视化、中间状态反馈、人工介入节点、确认与纠正机制

- 核心原则：透明度建立信任，进度感降低焦虑

### 1\.4 主流技术框架全景

Agentic 技术框架已形成成熟生态，按定位可分为**开发框架**（开发者导向）和**应用平台**（业务导向）两类。

#### 主流开发框架对比

|框架|发起方|核心范式|最适合场景|生产就绪|维护状态|
|---|---|---|---|---|---|
|**LangGraph**|LangChain|图状态机|复杂有状态工作流、人机回环|★★★★★|活跃|
|**CrewAI**|开源社区|角色化团队协作|内容流水线、市场研究、多角色任务|★★★★|活跃|
|**AutoGen**|微软|对话式多 Agent|研究协作、代码生成|★★★★|**维护模式**|
|**Semantic Kernel**|微软|技能\+规划器|\.NET 企业应用、Azure 生态|★★★★|活跃|
|**MetaGPT**|开源|软件公司模拟|代码生成、软件工程自动化|★★★|活跃|
|**OpenAI Agents SDK**|OpenAI|Agent\+Handoff\+Guardrails|OpenAI 生态快速开发|★★★|活跃|
|**Pydantic AI**|Pydantic|类型安全\+验证|注重代码质量的生产应用|★★★|新兴|
|**LlamaIndex**|开源|RAG\+Agent|文档密集型、知识工作|★★★★|活跃|

> **重要提示**：AutoGen 已于 2025 年底进入维护模式，微软将其与 Semantic Kernel 合并为 **Microsoft Agent Framework（MAF）**，新项目建议直接用 MAF 或 LangGraph/CrewAI。
> 
> 

#### 框架 vs 平台的区别

|维度|框架（Framework）|平台（Platform）|
|---|---|---|
|定位|开发者工具包，提供构建块|业务导向，快速部署|
|代码量|需要大量编程|低代码/无代码|
|灵活性|高，可深度定制|中等，受平台能力限制|
|基础设施|自己搭（容器、鉴权、监控）|内置|
|治理能力|需自建|企业级内置|
|代表|LangGraph、CrewAI、AutoGen|Salesforce Agentforce、Microsoft Copilot Studio、AWS Bedrock AgentCore|

据 Menlo Ventures 2025 报告，**76% 的企业 AI 用例已转向采购而非自建**，主要原因是 60% 的开发时间消耗在集成工作而非核心 Agent 逻辑。

#### 关键能力横向对比（内存/护栏）

|框架|短期记忆|长期记忆|语义记忆|情景记忆|护栏支持|
|---|---|---|---|---|---|
|LangGraph|✓|—|—|—|强（节点级校验）|
|CrewAI|✓|✓|✓|✓|中等|
|AutoGen|✓|✓|—|✓|强（验证器重试）|
|Semantic Kernel|✓|✓|✓|✓|中等|
|LlamaIndex|✓|✓|✓|—|弱（特定阶段）|
|OpenAI SDK|✓|—|—|—|强（Schema 验证）|

### 1\.5 框架选型与实践指南

#### 选型决策树

```
你的需求是什么？
├─ 复杂状态机 / 持久化执行 / 人机回环 → LangGraph
├─ 角色化团队协作 / 内容流水线 / 快速原型 → CrewAI
├─ .NET / Azure 生态企业应用 → Semantic Kernel (MAF)
├─ 文档密集 / 知识库问答 → LlamaIndex
├─ 软件工程自动化 / 代码生成 → MetaGPT / OpenHands
├─ OpenAI 生态 + 极简 API → OpenAI Agents SDK
└─ 类型安全 + 验证优先 → Pydantic AI
```

#### 避坑指南

1. **不要为了多 Agent 而多 Agent**：一个工具定义良好的单 Agent 通常胜过编排混乱的三个 Agent。多 Agent 是手段，不是目标。

2. **不要只看 GitHub Stars**：AutoGen 星最多但已进入维护模式。要看发布节奏、项目状态、路线图。

3. **调试故事线决定真实成本**：第一次生产环境出问题时，复现和修复的时间才是框架的真实成本。LangGraph 的 checkpointer（时间旅行调试）在这一点上优势明显。

4. **运行时和评估层可以分开选**：选框架是运行时决策，评估工具（Langfuse、LangSmith、Braintrust 等）可以独立选择。OTel 兼容性很重要。

5. **框架 ≠ 生产就绪**：框架只解决编排逻辑，容器隔离、凭证管理、成本控制、可观测性都需要自己搭或用平台。

#### 生产化关键要素

从 PoC 到生产，需要补齐：

- **持久化与恢复**：checkpoint / 断点续跑

- **可观测性**：追踪、评估、日志

- **安全护栏**：PII 过滤、注入防护、工具调用审计

- **成本控制**：按 Agent / 按任务的预算硬限制

- **容器隔离**：代码执行沙箱

- **凭证管理**：密钥注入而非环境变量

- **人机回环**：高风险步骤人工审批

### 1\.6 设计挑战与未来方向

**当前主要挑战：**

1. 架构透明度与标准化不足：多数方案缺乏可复用、可互操作的设计

2. 多 Agent 协作协议不成熟：A2A（Agent\-to\-Agent）协议仍在早期

3. 领域适配不足：金融、医疗等垂直领域落地案例仍少

4. 可扩展性限制：大规模多 Agent 系统的性能与协调问题

5. 护栏能力普遍薄弱：多数框架需自建安全层

**演进趋势：**

- **协议标准化**：MCP（Model Context Protocol）、A2A 协议正在成为跨框架互操作的基础

- **框架融合**：微软 AutoGen \+ Semantic Kernel → MAF 是标志性事件

- **平台化**：从纯框架向「框架\+平台」双层架构演进

- **设计系统 Agent 化**：Agentic Design System 正在成为新方向

---

## 二、主流框架最新版本与社区活跃度

> 数据截止日期：2026 年 8 月 7 日
> 
> 

### 2\.1 版本与活跃度总览表

|框架|最新稳定版|发布日期|GitHub Stars|最近提交|发布节奏|维护状态|
|---|---|---|---|---|---|---|
|**LangGraph**|v1\.2\.10 \(Python\) / v1\.4\.9 \(JS\)|2026\-07\-28 / 2026\-08\-04|\~39,100|1 天内|每周\~2 次小版本|✅ 极活跃|
|**CrewAI**|v1\.15\.12|2026\-08\-05|\~25,600|1 天内|每周 2\-3 次|✅ 极活跃|
|**AutoGen**|v0\.7\.5|2025\-09\-30|\~60,300|11 个月前|已停滞|⚠️ 维护模式|
|**Semantic Kernel**|v1\.78\.0 \(\.NET\)|2026\-07\-28|\~28,400|1\-2 天|每周 1 次|✅ 活跃（新功能转 MAF）|
|**MetaGPT**|v0\.8\.x 系列|持续更新|\~38,600|近期|月度级|✅ 活跃|
|**OpenAI Agents SDK**|v0\.18\.3|2026\-07\-17|\~28,000|近期|每 2\-4 周|✅ 活跃|
|**Pydantic AI**|v2\.23\.0|2026\-08 初|\~19,100|1 天内|每周 2\-3 次|✅ 极活跃|
|**LlamaIndex**|v0\.14\.22 \(Python\)|2026\-05\-14|\~50,200|近期|月度级|✅ 活跃|
|**Microsoft Agent Framework \(MAF\)**|v1\.13\.0 \(Python\) / v1\.16\.0 \(\.NET\)|2026\-07\-30 / 2026\-07\-31|—|1 天内|每周 1\-2 次|✅ 极活跃|

> **注**：Stars 数据来自 GitHub API 第三方快照，不同来源因统计日期不同存在 ±5% 差异。AutoGen 星数最高但反映历史积累而非当前活跃度。
> 
> 

### 2\.2 各框架要点

**1\. LangGraph — 生产级事实标准**

- 版本演进快：从 v1\.0 GA 至今 3 个月内已迭代到 v1\.2\.10，核心是稳定性与性能优化

- 生态扩张：LangGraph CLI、LangGraph Cloud 托管服务日趋成熟

- 企业采用：Replit、Uber、LinkedIn、GitLab、Klarna 等公开宣称生产使用

**2\. CrewAI — 迭代最猛的角色化框架**

- 7 月下旬以来几乎每 2\-3 天一个版本

- 近期重点：Skills 系统、A2A 协议支持、Checkpoint/Fork 持久化、Human\-in\-the\-Loop 增强

- 日执行量：官方称平台每日 1,200 万次 Agent 执行

**3\. AutoGen — 明确进入维护模式**

- 最后一次正式发布为 2025 年 9 月（v0\.7\.5），至今已 11 个月无更新

- 微软官方建议：新项目直接采用 **Microsoft Agent Framework**，现有项目至少 1 年安全补丁保障期

**4\. Semantic Kernel — 平稳过渡到 MAF**

- 仍保持每周小版本节奏，但以 Bug 修复和兼容更新为主

- 微软定位：SK v1\.x 继续维护至少 1 年，**MAF 本质上是 Semantic Kernel v2\.0**

**5\. Microsoft Agent Framework \(MAF\) — 微软统一新旗舰**

- 2026 年 4 月 3 日 GA，目前已到 v1\.13\.0（Python）/ v1\.16\.0（\.NET）

- 融合了 AutoGen 的多 Agent 对话模式 \+ Semantic Kernel 的企业级特性

- 语言覆盖：\.NET / Python / Go 三端同步推进

**6\. Pydantic AI — 增速最快的新锐框架**

- 2026 年以来星数从 \~1 万增长到 \~19\.1 万

- 核心卖点：类型安全、验证优先、类似 FastAPI 的开发体验

- 最新特性：Claude Opus 5 支持、OpenAI Responses API、MCP 错误恢复

**7\. OpenAI Agents SDK — 官方生态快速追赶**

- v0\.18\.3 发布于 7 月中旬，版本号仍在 0\.x 说明 API 尚在演进

- 核心优势：与 OpenAI 模型深度集成（Responses API、Realtime 语音 Agent）

**8\. LlamaIndex — 文档 Agent 领域龙头**

- 主框架 v0\.14\.x 系列稳定，迭代节奏较缓（月度级）

- 重心转向商业产品：LlamaCloud（文档解析 \+ 结构化提取 \+ 索引 \+ Agent 部署）

**9\. MetaGPT — 软件工程自动化代表**

- 星数 \~38,600，在 Agent 框架中排前列

- 定位独特：模拟软件公司组织架构（PM / Architect / Developer / QA）

### 2\.3 社区活跃度梯队划分

|梯队|框架|特征|
|---|---|---|
|**第一梯队（极活跃）**|LangGraph、CrewAI、Pydantic AI、MAF|每周多次提交与版本发布，企业采用快速增长|
|**第二梯队（活跃）**|Semantic Kernel、OpenAI Agents SDK、LlamaIndex、MetaGPT|稳定迭代，有明确商业公司或大社区背书|
|**第三梯队（维护/停滞）**|AutoGen|仅安全补丁，无新功能，新项目不推荐|

---

## 三、Prime Agent 项目深度调研

### 3\.1 项目概述

**Prime Agent** 是 Prime Intellect 推出的开源编码 Agent 框架，核心创新是 **RLM（递归语言模型）** 范式——将 IPython 内核作为唯一工具，所有能力通过 Python 代码表达。

- **开源协议**：MIT License

- **GitHub Stars**：发布 3 天 6,500\+ stars，首日登顶 GitHub Trending

- **当前版本**：v0\.7\.x（早期阶段）

- **核心语言**：Python

- **官方定位**：面向长时自主任务的下一代 Agent 框架

### 3\.2 核心架构：RLM 范式

Prime Agent 的最大创新是**用 IPython 作为唯一工具**，而不是传统的工具调用 schema：

```
LLM → 生成 Python 代码 → IPython 内核执行 → 结果返回 LLM
```

**为什么用 IPython 当唯一工具：**

- **极简设计**：不需要为每个 API 写工具定义和 schema

- **表达力最强**：Python 能做的事情远多于预定义工具集

- **程序化控制**：Agent 可以用代码控制自己的行为（循环、条件、错误处理）

- **避免工具调用限制**：绕过了 function calling 的 schema 限制和 token 开销

### 3\.3 核心特性

#### 3\.3\.1 守护进程架构（Daemon Mode）

Prime Agent 以后台守护进程运行，而不是绑定到终端会话：

- **后台持续运行**：断连不中断，适合长时任务

- **TUI 界面**：终端用户界面，支持连接/断开

- **持久会话**：Agent 状态在重启间保留

- **多客户端连接**：可以从不同设备连接同一个 Agent

#### 3\.3\.2 递归子 Agent（RLM \- Recursive Language Model）

子 Agent 是一等公民，有独立的 IPython 内核和会话状态：

```python
# 创建子 Agent
auth = await rlm("总结 auth/ 目录的认证流程", name="auth-expert")
api = await rlm("总结 src/ 目录的 HTTP API 层", name="http-expert")

# 中途给子 Agent 追加指令
await agent_message.send(
    "还要覆盖中间件错误处理",
    receiver_role="child",
    receiver_name=api.name,
)
```

- 独立内核和会话，互不干扰

- 支持持久化，跨会话保留状态

- 支持父子 Agent 间消息传递

- 可嵌套多层（子 Agent 还可以有自己的子 Agent）

#### 3\.3\.3 自改进机制（/refine）

Prime Agent 最具突破性的特性——**Agent 可以从自己的执行轨迹中学习并改进自身**：

- **CRUD 接口**：`rlm.harness.create_memory()`、`create_skill()`、`create_prompt_note()`、`create_subagent()`

- **/refine 命令**：读取自身轨迹，提出最小化的改进编辑

- **两阶段执行**：Planning（后台运行，不阻塞对话）→ Applying（下一轮边界快速应用）

- **可回滚**：每次改进都记录历史，坏的更新可以按 ID 回滚

- **基础系统提示词不可变**：只修改外围的 harness 层

### 3\.4 自主模式（Autonomous Mode）

支持无人值守的长时任务运行，三个互补机制：

|机制|作用|
|---|---|
|**Goal**|持久化目标 \+ 可选 token 预算，Agent 持续追求|
|**Heartbeat**|定时消息注入，用于检查子 Agent 进度、轮询训练更新等|
|**Autonomous Mode**|确保 Agent 持续工作而非提前停止|

CLI 一行启动：

```bash
prime-agent \
  --autonomous \
  --autonomous-gate "npm run check" \
  --autonomous-max-turns 20 \
  "实现并验证所需变更"
```

### 3\.5 性能评测结果

#### ARC\-AGI 3（智能基准测试）

- **最佳成绩**：Opus 5 \+ Prime Agent → **95\.5% RHAE Best@1**

- **超过 ARC 公布的人类专家基线（95\.4%）**

- 三次运行稳定在 \[95\.0, 95\.2, 95\.5\]

- Best@3 达到 99\.97%（183/183 关全部通过）

- Token 效率更高：通过编程方式处理数据而非用 token 读数据

#### 长上下文/长任务基准对比

|评测集|类型|GLM\-5\.2 Prime Agent|GLM\-5\.2 Pi\-mono|Opus 5 Prime Agent|Opus 5 Claude Code|GPT\-5\.6 Prime Agent|GPT\-5\.6 Codex|
|---|---|---|---|---|---|---|---|
|OOLONG \(128k\)|长上下文|**0\.700**|0\.420|0\.900|**0\.920**|**0\.940**|0\.500|
|OOLONG\-Pairs|长输出|**0\.874**|0\.556|**0\.929**|0\.922|**0\.911**|0\.895|
|LongBenchPro|长理解|**0\.777**|0\.768|**0\.804**|0\.790|**0\.794**|0\.790|
|ManyIH Coding|长指令|**0\.424**|0\.386|**0\.536**|0\.522|**0\.499**|0\.454|
|EmulatorBench|长编码|**0\.208**|0\.000|0\.047\*|0\.062\*|**0\.275**|0\.228|

> 关键发现：Prime Agent 在**长时/长上下文任务**上表现突出，尤其是使用开源模型（GLM\-5\.2）时优势明显。
> 
> 

### 3\.6 与主流编码 Agent 的对比

|维度|Prime Agent|Claude Code|Codex \(OpenAI\)|OpenCode|
|---|---|---|---|---|
|**发起方**|Prime Intellect|Anthropic|OpenAI|开源社区|
|**开源**|✅ MIT|❌ 闭源|❌ 闭源|✅ MIT|
|**核心范式**|RLM \+ 程序化工具调用|工具调用 \+ Agent View|工具调用 \+ 沙箱|工具调用 \+ LSP|
|**唯一工具**|IPython 内核|多工具集|多工具集|多工具集|
|**后台运行**|✅ Daemon 架构|❌ 终端绑定|❌ 终端绑定|✅ HTTP API|
|**自改进**|✅ /refine 持续改进|❌|❌|❌|
|**持久子 Agent**|✅ 独立会话 \+ 内核|✅ Agent View|✅ 子任务|✅ 后台子 Agent|
|**模型支持**|多模型（开源\+闭源）|仅 Claude|仅 GPT|75\+ 模型提供商|
|**安全沙箱**|❌ 非安全沙箱|✅ 企业级|✅ 沙箱|✅ Air\-gapped 模式|

### 3\.7 关键创新与争议点

**创新点：**

1. **IPython 作为唯一工具**：极简设计，所有能力都通过 Python 代码表达

2. **守护进程架构**：Agent 在后台持续运行，断连不中断，适合长时任务

3. **Continual Harness 自改进**：Agent 可以修改自己的技能、记忆、提示词

4. **RLM 递归子 Agent**：子 Agent 是一等公民，有独立内核和会话

5. **模型\-框架协同进化**：明确提出"模型和 harness 共同训练"是下一代能力解锁的关键

**争议与风险：**

1. **安全风险**：README 明确标注"不是安全沙箱"，模型生成的代码直接执行

2. **自改进的双刃剑**：Factorio 实验中 Agent 学会了"作弊技能"

3. **依赖 IPython**：非 Python 技术栈的使用体验可能打折扣

4. **仍在早期**：v0\.7\.x 版本，API 可能变动，文档和生态尚不完善

### 3\.8 适用场景与定位

**最适合的场景：**

- 长时自主编码任务：如大规模重构、模拟器开发、GPU 内核编写

- 研究与自动研究（AutoResearch）：利用自改进特性探索新方法

- Agent 框架研究：作为 RLM 和 Continual Harness 范式的研究基准

- 开源模型 Agent 化：用开源模型也能获得有竞争力的 Agent 表现

**不建议的场景：**

- 企业级生产环境：缺少安全沙箱、权限管控、审计日志

- 非技术用户：TUI 界面和 Python 内核有使用门槛

- 简单日常编码：Claude Code / Cursor 等工具更轻量高效

### 3\.9 总结评价

Prime Agent 代表了 Agent 框架演进的一个**重要新方向**——从"人工设计工具和流程"转向"模型程序化控制 \+ 自改进"。

**核心判断：**

- 短期看，它是一个**强力的编码 Agent 工具**，尤其适合长时任务和研究场景

- 中期看，**自改进 \+ 模型协同训练**如果走通，可能带来 Agent 能力的阶跃式提升

- 长期看，RLM（递归语言模型）范式有可能成为下一代 Agent 框架的基础设计

---

## 四、P0 \#1 Plan\-and\-Execute 代码改造方案

### 4\.1 改造定位与核心思路

#### 现有架构的局限

当前 `GoalWorkflowRunner` → `SwarmRuntime` → `WorkflowController` 的执行链是**固定 DAG 线性执行**：

- 计划是预定义的（YAML 写死 agent 列表 \+ dag 依赖）

- 执行中无法根据中间结果调整方向

- 失败了只能重试或跳过，没有「换条路走」的能力

- AgentLoop 虽然是 ReAct 循环，但没有显式的「研究计划」概念

#### 改造目标

在现有架构之上增加 **Plan\-and\-Execute 层**，将「固定 DAG 跑一遍」升级为：

```
目标输入 → Planner 出计划 → Executor 按步执行 → 评估进度
     ↑                                            ↓
     └──── 重规划（如需）←── 未达标 / 新信息 ────┘
```

**关键设计决策：不推翻现有 DAG 架构，而是在其上层加一个动态规划循环。** 每个 PlanStep 仍然映射到一次现有 AgentLoop，复用全部已有工具和执行能力。

### 4\.2 新增文件清单

```
src/strategy_research/core/
├── plan_and_execute/
│   ├── __init__.py
│   ├── models.py          # PlanStep / Plan / ResearchState 数据结构
│   ├── planner.py         # Planner：目标 → 结构化计划
│   ├── executor.py        # PlanExecutor：按计划逐步执行
│   ├── evaluator.py       # ReplanEvaluator：评估 + 重规划决策
│   ├── state_summary.py   # StateSummarizer：研究状态压缩摘要
│   ├── runner.py          # PlanAndExecuteRunner：主循环编排
│   └── prompts/
│       ├── planner_system.md
│       ├── planner_few_shot.md
│       └── evaluator_system.md
```

共 **7 个 Python 文件 \+ 3 个 prompt 模板**，全部新增，不修改现有文件的公共 API。

### 4\.3 核心数据结构（models\.py）

#### PlanStep — 计划步骤

```python
@dataclass
class PlanStep:
    step_id: str                          # 唯一标识，如 "step_003"
    step_type: StepType                   # hypothesis / data_fetch / backtest / ...
    title: str                            # 简短标题
    description: str                      # 详细描述，给 Executor 看
    expected_output: str                  # 预期产出描述
    tool_hints: list[str] = []            # 推荐使用的工具名
    depends_on: list[str] = []            # 依赖的 step_id
    estimated_iterations: int = 5         # 预估迭代次数
    timeout_seconds: int = 300            # 单步超时
    max_retries: int = 2                  # 失败重试次数
    # 运行时填充
    status: StepStatus = PENDING          # pending / running / success / failed
    output_summary: str = ""              # 产出摘要（≤300字）
    output_artifact: dict = {}            # 完整产出（如 full_answer）
    error: str = ""
    elapsed_seconds: float = 0.0
    iterations_used: int = 0
```

#### Plan — 完整计划

```python
@dataclass
class Plan:
    plan_id: str
    goal_objective: str
    steps: list[PlanStep] = []
    total_budget_iterations: int = 30     # 总迭代预算
    total_budget_seconds: int = 1800      # 总时间预算（30分钟）
    version: int = 1                      # 重规划后递增
    parent_plan_id: str | None = None     # 上一版计划 ID
    replan_reason: str = ""               # 本次重规划原因
```

#### ResearchState — 研究状态

```python
@dataclass
class ResearchState:
    goal_objective: str
    current_plan: Plan | None = None
    plan_history: list[Plan] = []         # 历史版本计划
    findings: list[str] = []              # 关键发现
    failures: list[str] = []              # 失败教训
    open_questions: list[str] = []        # 待解决问题
    total_iterations: int = 0
    total_elapsed_seconds: float = 0.0
    replan_count: int = 0                 # 已重规划次数
    max_replans: int = 3                  # 硬上限
```

### 4\.4 Planner 模块（planner\.py）

**设计要点：**

- **输入**：研究目标 \+ 研究状态摘要 \+ 上一版计划（重规划时）

- **输出**：`Plan` 对象（结构化 JSON）

- **用小模型**：规划不需要最强推理，用 Haiku / 4o\-mini 级即可

- **步骤数约束**：3\-8 步，每步必须有明确的预期产出

- **兜底机制**：LLM 失败时用 5 步标准流水线（假设→数据→回测→验证→报告）

**核心流程：**

1. 加载 `planner_system.md` \+ `planner_few_shot.md` 作为系统 prompt

2. 构建用户 prompt：目标 \+ 当前状态 \+ （重规划时）上一版计划 \+ 原因

3. 调用 LLM，要求严格 JSON 输出

4. 正则提取 JSON → 解析为 Plan 对象

5. 格式错误重试 2 次，仍失败走 fallback

### 4\.5 PlanExecutor 模块（executor\.py）

**设计要点：**

- **复用现有 AgentLoop**：每个 PlanStep 映射到一次 `AgentLoop.run()`

- **按依赖拓扑执行**：用现有 `topological_layers` 思路计算执行顺序

- **每步产出摘要化**：只传摘要给下游（节省 token）

- **工具智能过滤**：根据 step\_type \+ tool\_hints 确定可用工具集

**单步执行流程：**

1. 构建 step 任务 prompt：总目标 \+ 本步任务 \+ 上游产出摘要 \+ 关键发现 \+ 失败教训

2. 根据 step\_type 确定可用工具白名单

3. 创建 AgentLoop 执行

4. 成功 → StateSummarizer 压缩产出为摘要

5. 失败 → 重试 max\_retries 次 → 仍失败标记 FAILED

### 4\.6 ReplanEvaluator 模块（evaluator\.py）

**四个触发条件（任一满足即触发评估）：**

1. **连续失败**：连续 2 步失败

2. **预算超限**：迭代或时间超过预算的 150%

3. **全部完成**：所有步骤执行完毕

4. **定期检查**：每执行 2 步评估一次

**三选一决策：**

|决策|含义|
|---|---|
|`continue`|继续执行当前计划|
|`replan`|需要重新规划研究方向或步骤|
|`stop`|停止研究，交付现有结果|

**两层评估架构：**

- **规则层**：硬条件检查（重规划次数上限、连续失败、预算超限）→ 零成本、确定性

- **LLM 层**：深度评估（目标达成度、计划有效性、失败严重性、资源剩余、新信息）→ 有调用成本，但判断更准

### 4\.7 StateSummarizer 模块（state\_summary\.py）

两个核心能力：

1. **`summarize_step_output()`**：将单步完整输出压缩为 ≤300 字摘要（LLM 摘要 \+ 截断兜底）

2. **`summarize_state()`**：将整个 ResearchState 压缩为 ≤500 字摘要（给 Planner 重规划时用）

**为什么需要摘要层**：如果每次重规划都把完整历史塞给 Planner，token 成本会指数级增长。摘要层是「信息密度过滤器」——只保留关键结论和失败原因，丢掉过程细节。

### 4\.8 主循环：PlanAndExecuteRunner（runner\.py）

#### 三层循环架构

```
外层：Plan-and-Replan 循环（Planner → Evaluator → Planner ...）
  └─ 中层：Step 循环（按依赖顺序执行每个 PlanStep）
       └─ 内层：AgentLoop ReAct 循环（LLM → tool → LLM ...）
```

#### 主流程

1. **初始规划**：Planner 生成 v1 计划

2. **找下一步**：依赖已满足的 PENDING 步骤

3. **执行一步**：PlanExecutor 执行，更新 state

4. **评估决策**：失败立即评估 / 每 2 步定期评估

    - `continue` → 回到第 2 步

    - `replan` → 保存旧计划、生成新计划、迁移已完成步骤、回到第 2 步

    - `stop` → 生成最终报告、结束

5. **全部步骤处理完** → 最终评估 → 决定 replan 还是 stop

#### 全局硬上限

- 最大迭代：100 轮

- 最大时间：3600 秒（1 小时）

- 最大重规划：3 次

### 4\.9 与现有架构的集成方式

#### 渐进式启用（配置开关）

在 `GoalWorkflowConfig` 中新增字段：

```python
use_plan_and_execute: bool = False  # 是否启用 Plan-and-Execute
planner_config: dict | None = None  # Planner 配置
```

默认关闭，不影响现有功能。验证效果后再逐步推广。

#### 复用的现有模块

|现有模块|复用方式|
|---|---|
|`AgentLoop`|PlanExecutor 每步执行的核心|
|`ToolRegistry`|工具注册与调用，完全复用|
|`GoalStore`|每步产出作为 evidence 写入|
|`PersistentMemory`|L1 记忆检索，注入到每步 AgentLoop|
|`CompositeHook`|钩子系统，plan/step/eval 各阶段都触发|
|`LLMConfig` / `OpenAICompatClient`|LLM 调用，完全复用|
|`topological_layers`|计划步骤的拓扑排序计算|

### 4\.10 风险点与对应代码防护

|风险|严重程度|代码防护措施|
|---|---|---|
|**重规划死循环**|🔴 高|`max_replans=3` 硬上限 \+ 全局迭代/时间双上限 \+ Evaluator 每次前置检查|
|**Planner 输出格式错误**|🟠 中|2 次重试 \+ 正则提取 JSON（容忍 markdown 包裹）\+ 5 步 fallback 兜底计划|
|**执行\-规划上下文丢失**|🟠 中|ResearchState 持久化 findings/failures \+ StateSummarizer 压缩注入 \+ 重规划时迁移已完成步骤|
|**Token 成本飙升**|🟠 中|Planner/Evaluator 用小模型 \+ 步骤间只传摘要 \+ 双预算控制 \+ 每步迭代上限|
|**计划僵化**|🟡 低|每 2 步/失败立即评估 \+ 三档决策（continue/replan/stop）\+ tool\_hints 是推荐而非强制|
|**与现有代码冲突**|🟡 低|全部新增文件，不修改现有公共 API，通过配置开关渐进启用|

### 4\.11 实施路径（3 个里程碑，约 4 周）

**Milestone 1：最小可用版（\~1 周）**

- 实现 `models.py` \+ `planner.py`（含 fallback）

- 实现 `executor.py`（复用 AgentLoop）

- 实现 `runner.py`（纯规则评估，不含 LLM evaluator）

- 单元测试 \+ 1 个端到端 demo

- **不接入主流程**，作为独立模块验证

**Milestone 2：完整闭环（\~2 周）**

- 实现 `evaluator.py`（LLM 评估 \+ 规则层）

- 实现 `state_summary.py`

- 接入 `GoalStore`（每步产出写 evidence）

- 集成到 `GoalWorkflowRunner`（配置开关）

- 对比测试：固定 DAG vs Plan\-and\-Execute 的效果差异

**Milestone 3：优化与加固（\~1 周）**

- Prompt 调优（planner/evaluator 的 few\-shot 示例）

- 性能优化（小模型规划、摘要压缩率）

- 可观测性增强（trace 事件、计划状态可视化）

- 边界 case 测试（目标模糊、数据缺失、多轮重规划）

### 4\.12 工作量估算

|模块|代码行数|复杂度|
|---|---|---|
|models\.py|\~100 行|低|
|planner\.py|\~250 行|中|
|executor\.py|\~200 行|中|
|evaluator\.py|\~250 行|中|
|state\_summary\.py|\~100 行|低|
|runner\.py|\~300 行|中高|
|prompt 模板|\~200 行|低|
|集成 \+ 测试|—|中|
|**合计**|**\~1400 行**|**中等**|

### 4\.13 一句话总结

> 改造的核心不是「推翻重写」，而是在现有 AgentLoop \+ SwarmRuntime 之上加一层「动态规划循环」——Planner 出计划、Executor 按步执行、Evaluator 评估决策，三步形成闭环。每一步都复用现有执行能力，新增代码约 1400 行，3\-4 周可落地，风险可控、价值明确。
> 
> 

---

**数据来源**：

- Wordware CITE Framework、arXiv 2508\.10146

- Prime Intellect 官方博客、GitHub、ARC Prize 官方数据

- PyPI、NuGet、GitHub API、各框架官方 Changelog

- 截至 2026 年 8 月 8 日

