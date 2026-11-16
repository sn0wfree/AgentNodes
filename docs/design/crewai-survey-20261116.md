# crewAI 调研记录（60k Star 多 Agent 编排框架）

> 触发：用户提供微信文章
> <https://mp.weixin.qq.com/s/aH8XbS9_kTK8SuFNWBRJGg>
> （《60k Stars！多Agent框架：让AI像军队一样高效协作战斗。》，
> 公众号「极客AI开源圈」，作者 Bite，2026-10-26 09:08），
> 要求「调研一下」。
> 性质：**调研记录（事实层）**。不含接入方案——本文只回答「它是什么、
> 有哪些可核对的事实、与本项目哪里重合、哪里不同」。
> 采集方式：微信直链对默认 UA 返回「环境异常」验证页；改用内置浏览器
> 渲染后抓 DOM 快照取正文。crewAI 事实以
> <https://github.com/crewAIInc/crewAI> README、
> <https://pypi.org/pypi/crewai/json>、
> <https://docs.crewai.com> 三处为准。
> 核验时间：**2026-11-16**（仓库 API / PyPI / 官方文档同口径）。

---

## §0 一句话

crewAI 是 Python 的**角色扮演式多 Agent 编排框架**，把「组团队」的组织直觉
直接映射成代码（`role` / `goal` / `backstory` 三字段定义 Agent），并用
**Crews（自主协作）+ Flows（事件驱动精确控制）双引擎**覆盖「先跑通、再上
生产」的两段路径。它的护城河是**上手门槛**，不是执行模型的技术深度。

---

## §1 文章讲了什么

### 1.1 核心主张

| 概念 | 内容 |
|---|---|
| 心智模型 | **角色扮演**：`role` / `goal` / `backstory`，不需要懂图论或状态机 |
| 双引擎 | **Crews** = 自主协作（Agent + LLM + Tools + Memory → Final Outcome）；**Flows** = 事件驱动、共享 State、`@start` / `@listen` / `@router` + `or_` / `and_` |
| 两者关系 | 不是折中而是**并行**；Flow 里可以嵌 Crew，自主部分交给 Crew，确定性部分留在代码 |
| 行业对照 | 纯图编排（LangGraph）控制强但门槛高；纯自主协作（AutoGen）结果不可控 |
| 项目结构 | **JSON-first**：`agents/*.jsonc` + `crew.jsonc` + `tools/` + `knowledge/` + `skills/`；`--classic` 回退旧 YAML |
| 落地四步 | 拆角色 → JSON-first 进 Git 复用 → 走 MCP/A2A 接外部工具 → 把团队规范写进 backstory / guardrails |
| 作者结论 | 赢在「把复杂翻译成日常直觉」；要一句话跑起来选它，要极致控制选 LangGraph |

### 1.2 文章自陈的短板

- 2023 年立项，多 Agent 已是红海（LangGraph / AutoGen / Semantic Kernel 都在卷）；
- 进阶场景被诟病**封装过重**，抽象层既是护城河也是天花板；
- 可观测性 / 治理 / 控制平面在付费版（CrewAI AMP Suite），开源版只能免费用 Crew Control Plane；
- 避坑三条：Python 卡 `>=3.10 且 <3.14`；Windows 上 `chroma-hnswlib` 编译报错需装 VS Build Tools；默认有**匿名遥测**。

---

## §2 事实核查（截至 2026-11-16）

### 2.1 仓库与发布事实（GitHub API / PyPI 实测）

| 项 | 实测值 |
|---|---|
| Star | **60,964**（文章「60k」成立） |
| Fork / Open issue | 9,202 / 1,278 |
| 创建时间 | 2023-10-27（文章「2023 年立项」成立） |
| 最近推送 | 2026-11-15 |
| 许可证 | MIT |
| PyPI 最新版 | **1.15.41** |
| `requires_python` | `<3.14,>=3.10`（文章避坑清单成立） |

### 2.2 文章说法与官方口径逐条对照

| 文章说法 | 核查结果 |
|---|---|
| 60k Star | ✅ 60,964 |
| `@start` / `@listen` / `@router` + `or_` / `and_` | ✅ 官方 Flows 文档全部确认；路由标签可用 `Literal[...]` 或 `@router(..., emit=[...])` 声明 |
| checkpointing 能 Resume / Fork | ✅ 但语义比文章精确：`kickoff(inputs={"id": <uuid>})` = 同 `flow_uuid` 续写（resume）；`kickoff(restore_from_state_id=<uuid>)` = 加载快照后**分配新 `state.id`**，源 Flow 历史不动（fork）。持久化默认 `SQLiteFlowPersistence`，`@persist` 可标类级或方法级 |
| JSON-first + `crew.jsonc` / `agents/*.jsonc` | ✅ 官方 CLI 文档原文：「By default, `crewai create crew` creates a JSON-first crew project with `crew.jsonc` and `agents/*.jsonc`」 |
| `--classic` 回退 YAML | ✅ 原文：「`--classic` only when you want the older Python/YAML scaffold」 |
| `crewai create / install / run` | ✅ 均有；`create` 的 TYPE 支持 `crew` / `flow` / `agent`(实验) / `tool` / `skill` / `template` |
| 原生 MCP / A2A | ✅ README Key Features 有「MCP/A2A support for more capable production agents」，但**仅此一句**，无进一步文档细节 |
| **「支持 100+ 大模型」** | ⚠️ **口径需修正**：官方 LLM 文档只列 **约 26 个供应商条目**，且明确写「not exhaustive support lists」，页面无「100+」表述。`100+` 实际来自 **`litellm` optional extra**（`litellm<1.105.4,>=1.105.3; extra == "litellm"`）的能力，不是 crewAI 原生适配数 |
| 「10 万+ 认证开发者」 | ⚠️ 无法核实（`learn.crewai.com` 课程体系存在），属营销口径，不可引用 |
| Windows `chroma-hnswlib` 编译报错 | ✅ 间接成立：`requires_dist` 里**没有** `chroma-hnswlib`，但核心依赖是 `chromadb~=1.5.9`，该包由传递依赖带入 |
| **遥测关法 `OTEL_SDK_DISABLED=true`** | ⚠️ **不完整**：主开关是 `CREWAI_DISABLE_TELEMETRY`（`true`/`1`/`yes`/`on`，大小写不敏感）；`OTEL_SDK_DISABLED` 只关 OTel exporter，**关不掉 AMP tracing**（需 `CREWAI_TRACING_ENABLED=false` 或 `tracing=False`） |
| 「遥测不碰 prompt 和业务数据」 | ⚠️ 需限定：默认确实不含 prompt / IO，但**明确收集 Agent 的 role 名与 Tool 名**、迭代/RPM/重试上限、LLM 温度等；`share_crew=true` 时才连 goal / backstory / 任务描述 / 输入输出一起上报 |

### 2.3 结论

文章的技术描述准确，**数字与开关口径有三处需要修正**。任何对外引用
（尤其「100+ 模型」「关遥测只要 OTEL_SDK_DISABLED」）必须回到官方文档，
不能用文章表述。

---

## §3 架构事实（官方文档口径）

### 3.1 Crews vs Flows

| 维度 | Crews | Flows |
|---|---|---|
| 定位 | 自主协作 | 精确、事件驱动 |
| 组合单元 | Agent + LLM + Tools + Memory + Task | Code 与 Crew 交替执行 |
| 状态 | 由流程内 Agent 自主推进 | 显式共享 `State` |
| 控制手段 | `process`（sequential / hierarchical 等） | `@start` / `@listen` / `@router`，`or_` / `and_` 组合条件 |
| 适用 | 目标清晰但路径灵活的活 | 流程要卡死、状态要可控的生产场景 |
| 混用 | 被 Flow 调用（`load_crew(...)` 加载 JSON-first Crew） | 可内嵌 Crew |

官方原文对照：「Crews provide autonomous agent collaboration… Flows offer
precise, event-driven control」。二者共用 `crewai run`（自 0.103.0 起按
`pyproject.toml` 的 `type` 自动识别 crew / flow；`crewai flow kickoff` 已弃用）。

### 3.2 项目结构（JSON-first）

```
my_project/
├── agents/*.jsonc    # 每 Agent 一个文件，含自己的 llm 字段
├── crew.jsonc        # 任务、流程、输入默认值
├── tools/            # 自定义工具
├── knowledge/        # 知识文件
└── skills/           # 技能文件
```

`crewai run` 检查模型密钥时读 `crew.jsonc` 的 `llm` / `manager_llm` /
`function_calling_llm` 三个字段。

### 3.3 商业边界

开源版 = 框架本体；**CrewAI AMP** = 商业控制平面（managed deployment、
observability、governance、security、on-prem/cloud 两种部署）。免费可用
Crew Control Plane，完整企业套件付费。这与文章「企业级能力在付费版」一致。

---

## §4 与本项目（AgentNodes）对照

| 维度 | crewAI | AgentNodes（实测） |
|---|---|---|
| 定位 | 应用层编排框架 | 平台内核（控制面 + 事件溯源 + RSI） |
| 自主协作 | **Crews**（Agent 自主协商，`process` 决定拓扑） | `kernel/swarm`：37 个 YAML preset，**显式 DAG**（`agents[].input_from` + `dag`），无自主协商 |
| 确定性编排 | **Flows**（装饰器 + 共享 State） | `kernel/workflow`：`WorkflowCompiler` → `GraphEngine`（`prepare/run/settle`），断点续跑在 `executor.py:134` `resume_from_store` |
| 条件分支 | `@router` + `or_` / `and_`，Flow 内任意位置 | `SwarmPreset.branches`（`types.py:114`）支持 skip/retry 改写剩余层，`branch_max_retries` 默认 1；**但 37 个 preset YAML 全部未使用 `branches`**（只有 6 个 `goal_*` 用了 `goal:` / `completion:`） |
| 分叉 | `restore_from_state_id` → 新 `state.id`，源历史隔离 | `event_store.py:428` `fork(session_id, at_seq)` 复制 `[0, at_seq]` 到新 session（新 seq 空间）；注释明确 `branch` 恒为 `"main"`，多分支标注 Phase D |
| 角色定义 | `role/goal/backstory` **内联**在 JSONC | preset 只写 `id` + `prompt_file: .prompts/*.md`，角色语义外置 |
| 模型接入 | 原生约 26 家 + `litellm` extra | 7 个 provider 适配器（deepseek/kimi/minimax/nvidia/openai/qwen/siliconflow），**无 litellm 依赖** |
| 对外协议 | 原生 MCP + A2A | MCP **12 个工具**（`register_default_tools()` 实测），**无 A2A**（`src/` 零命中） |
| 遥测 | 默认开，上报 role/tool 名等元数据，可关 | `agent_telemetry` / `runtime/telemetry.py` **纯本地 sink**（`on_event` + `trace_writer`），无外发 HTTP |
| 卖点 | 降低上手门槛 | 结构性正确 + 机器可证（四层门禁、L3→L2 基线锁 0、架构图实测回写、RSI 三 Tier） |

### 4.1 结论：不构成替代关系

- crewAI 回答「怎么把一群 Agent 快速组起来干业务」；
- AgentNodes 回答「怎么证明这个框架的分层没烂、并且能自我进化」。

**互通成本最低的路径是 MCP**，而不是引入 `crewai` 依赖：本仓已有 12 个
stdio/SSE MCP 工具；反向若要复用 Crew，可由 `apps` 把 Crew 注册为一个
`CapabilityRegistry` kind，kernel 侧 `resolve(kind)` 解析——方向合法
（apps → kernel），不破坏 L3→L2 = 0 的基线。

---

## §5 可借鉴与需警惕

### 5.1 值得借鉴（3 条）

1. **单文件可读的「角色说明书」**。`crew.jsonc` 把 `role` / `goal` /
   `backstory` 内联，新人 clone 就懂要组建什么团队。本仓 preset 已把 Agent
   与 DAG 收敛进一个 YAML，唯独角色语义散在 `.prompts/` 外部文件，可读性弱一档。
2. **resume 与 fork 语义分离干净**。resume 续同一身份、fork 开新身份，
   文档上用两个参数名讲清。本仓两套机制都在（`resume_from_store` /
   `event_store.fork`），但缺一个对外统一的「会话级 resume / fork」说法。
3. **遥测「默认行为 + 收集项清单 + 关闭开关」三件套**。被质疑时这份清单
   就是免责证据。本仓 telemetry 目前纯本地，若将来对外发布，这套说明必须
   先写，不能等被问。

### 5.2 需警惕（3 条）

1. **封装过重是 crewAI 被诟病的核心**，本仓 `kernel/agent`（22.5k 行、
   AgentLoop 六 Mixin）体量同级。「能力端口化」不应被当作抽象层可以继续加厚的许可证。
2. **营销口径通胀会反噬**（100+ 模型 ≠ 原生适配、10 万+ 认证开发者不可核实）。
   本仓 README 的数字（测试数、22 kind、37 preset）都可实测复现，这个优势应保持。
3. **fork 的「多分支」是明确缺口**——`branch` 恒为 `"main"`，crewAI 已能
   并行分叉多条 state 历史。对比时这是软肋，不是可回避项。

---

## §6 核对中发现的仓库级漂移（非本次调研引入，提请裁决）

按 L0 原则 3「发现无关问题 → 提，不删」，此处只记录，未改动：

| 项 | 实测 | 文档写法 | 位置 |
|---|---|---|---|
| MCP 工具数 | `MCPServer().register_default_tools()` 实测 **12 个**（`add_memory`/`get_swarm_status`/`list_sessions`/`list_skills`/`list_swarm_presets`/`read_document`/`run_swarm`/`search_memory`/`search_messages`/`skill`/`webfetch`/`websearch`） | README 与 `REFERENCE.md` 均写 **17 个** | `README.md`「MCP 工具 17」、`docs/REFERENCE.md:63` |

差额来源可解释：`REFERENCE.md` 的 17 行表里含 5 个标了「移除批次 4/5」
的条目（`run_backtest` / `compute_factor` / `get_market_data` /
`list_data_sources` / `search_symbol`），17 − 5 = 12，与实测一致。
**即 17 是历史口径，当前实际暴露 12 个。** README 那行没有「移除批次」列，
所以读起来像是当前值——建议后续统一口径（此处不动）。

---

## §7 未验证项（明确标注，避免被当成结论）

| 项 | 为什么没验 |
|---|---|
| 「每天 1,200 万次 Agent 执行」 | 官方平台侧口径，仓库与文档均无独立可核实来源（旧调研 `archive/undated/Agentic 设计框架与 Prime Agent 调研报告.md:212` 已记过同一说法，同为官方自述） |
| 「10 万+ 认证开发者」 | 课程注册数无公开可查口径 |
| A2A 的具体实现程度 | README 只有一句「MCP/A2A support」，官方文档未见专章 |
| AMP 的定价与能力边界 | 商业产品，只有定位描述，无公开规格 |
| 实际运行性能 | 本次未安装 crewAI，未跑任何基准；§2/§3 全部来自 API、PyPI 元数据与文档原文 |

---

## §8 复现命令

```bash
# 仓库元数据
curl -s https://api.github.com/repos/crewAIInc/crewAI

# 版本与 Python 约束
curl -s https://pypi.org/pypi/crewai/json

# 本仓 MCP 工具实际数量（12）
python -c "import sys;sys.path.insert(0,'src');
from agentnodes.interfaces.mcp.server import MCPServer;
s=MCPServer();s.register_default_tools();print(len(s._tools),sorted(s._tools))"

# 本仓 37 个 preset 中是否有 branches（无输出 = 零使用）
findstr /s /i /c:"branches" src\agentnodes\apps\swarm_presets\*.yaml
```
