# 执行面业界调研 — 2026-10-24

> 📌 **精化说明**：文中「GraphEngine 唯一 runtime」已被选项 B 精化为「唯一**编排** runtime，AgentLoop 是执行原语」——见 `stage-c1-wp-agent-node-design-20261028.md`。

> 问题：v6 执行面（AgentRuntime 协议 + 3 runtime + executor 子进程）如何落地——进程模型、事件回流、取消、隔离、资源上限、安全。
> 对象：agent 宿主（Claude Code · Codex CLI · OpenClaw）· 工作流引擎（Temporal · LangGraph Server）· 计算框架（Jupyter · Ray · Dask）。
> 结论先行：**业界收敛为三层执行分类法（进程内 / 常驻子进程池 / OS 沙箱·远端）；我们适配「runtime 进程内 + executor 池 + 沙箱占位」，Windows 现实约束下进程边界是当前唯一可用隔离**。

---

## 1. 执行面必须回答的六个问题

| # | 问题 | 失败形态 |
|---|---|---|
| 1 | 代码跑在哪个进程 | 控制进程被阻塞/被拖崩 |
| 2 | 执行事件如何回流 | WebUI 时间线断流、进度不可见 |
| 3 | 取消如何传导 | 用户 cancel 杀不掉回测、半写数据 |
| 4 | 不可信代码怎么隔离 | agent 写的 strategy.py 越权（Tier 2，安全债在案） |
| 5 | 资源如何设限 | 一个回测吃满 CPU/内存，别的研究饿死 |
| 6 | 崩溃后怎么算 | 结果丢失 / 陈旧提交（已有 fence） |

## 2. 业界方案逐家

### 2.1 Agent 宿主：工具执行的位置与隔离

**Claude Code**（[sandboxing 文档](https://code.claude.com/docs/en/sandboxing)）
- Bash 工具跑在 OS 级沙箱（macOS **Seatbelt** / Linux **Landlock**）；**只沙箱 Bash 工具**，harness 与其他工具在沙箱外——这是它被反复批评的点（[Beltdown 逃逸分析](https://accomplish.ai/blog/beltdown-escaping-the-claude-code-sandbox)、[hooks 早于沙箱初始化的逃逸](https://codeant.ai/blogs/claude-code-macos-sandbox-escape)）。
- **对我们的直接信号**：[issue #26616](https://github.com/anthropics/claude-code/issues/26616) 正在请求「所有工具执行移出主进程、每工具一个 worker 子进程」——社区在要我们 executor 的方向；教训：**边界要覆盖全部执行，而不是挑几个工具**。

**Codex CLI**（[linux-sandbox README](https://github.com/openai/codex/blob/main/codex-rs/linux-sandbox/README.md)）
- 内核级隔离 spawned 进程：Linux 用 **bubblewrap + seccomp**（从 Landlock 演进——Landlock 隔离不了 app-server Unix socket）；macOS Seatbelt。
- 关键性格：**沙箱工具调用，不沙箱自己**（主进程不沙箱，子进程严管）；即便如此仍有已披露逃逸（[CSA 2026](https://labs.cloudsecurityalliance.org/research/csa-research-note-openai-codex-sandbox-escapes-20260922-csa)）——沙箱是纵深一层，不是免死金牌。
- **对我们的直接信号**：`codex-rs/linux-sandbox` 是将来 Linux 部署时沙箱位的实现参考。

**OpenClaw**（[sandboxing](https://docs.openclaw.ai/gateway/sandboxing) · [cloud workers](https://docs.openclaw.ai/gateway/config-cloud-workers)）
- 工具执行可切到 sandbox backend——**默认关**，`agents.defaults` 配置；harness（如 Codex harness）以插件接入，原生面被 harness 拥有时平台只做镜像+投影。
- **Cloud workers**：把一个 session 的执行搬到一次性云机，**transcript 仍归宿主**——「执行可搬走、事实留原地」的典范，与我们的 EventStore 归属一致。

### 2.2 工作流引擎：控制与执行的进程分离

**Temporal**（[task-queue](https://docs.temporal.io/task-queue) · [how-temporal-works](https://docs.temporal.io/encyclopedia/architecture/how-temporal-works)）
- Worker 独立进程长轮询任务队列；**Workflow 任务队列与 Activity 任务队列默认分离**——确定性编排代码与副作用/重执行代码路由到不同 worker 进程，独立扩缩、独立故障域。
- **对我们的直接信号**：workflow vs activity 的二分 = 我们 **runtime（turn 编排）vs executor（重活）** 两个派发面的先例；`MaxConcurrentActivityExecutionSize` = 容量组参数化样例。

**LangGraph Server**（[架构综述](https://www.emergentmind.com/topics-langgraph-architecture)）
- 异步 executor 执行 background run（生命周期超过 HTTP 请求）；checkpoint + durable execution 支撑中断恢复。run 在服务进程内异步执行，重活语义交给 activity/工具层。
- 佐证「**agent 循环留在服务进程内是主流**」；我们 core/study 已用 langgraph，语义连续。

### 2.3 计算框架：重计算并行

**Jupyter**（十年验证的「每会话一执行进程」）
- kernel = 独立进程，ZMQ 五通道（shell/iopub/stdin/control/hb）：执行走 shell、**流式输出走 iopub 订阅**、**中断走 control 通道**、心跳检测死亡。
- **对我们的直接信号**：executor 的通道设计直接抄——dispatch 队列 / 事件流 / 取消 / 存活检测四通道；「UI 断开 kernel 不停」= 我们 session 的同构。

**Ray / Dask**（[对比](https://domino.ai/blog/spark-dask-ray-choosing-the-right-framework)、[基准](https://www.jennakwon.page/2021/03/benchmarks-dask-distributed-vs-ray-for.html)）
- Ray：任务吞吐 ~4×Dask、内存效率高——适合海量轻任务参数扫描；actor 模型适合有状态计算。
- Dask：数据并行的 DataFrame 大变换、单机到集群平滑、日志可调试性好。
- **结论：现在都不引入**。单机单用户 + 中等规模参数扫描，`ProcessPoolExecutor`/常驻子进程池足够；**升级触发条件**写死：参数扫描任务数常态化 >10² 或需要跨机 → 先 Ray（吞吐）后 Dask（数据并行）。

## 3. 业界收敛：三层执行分类法

| 层 | 跑什么 | 业界实践 | 我们的归属 |
|---|---|---|---|
| **T1 进程内异步** | IO 密集循环（等 LLM）、轻工具 | OpenClaw runtime / LangGraph server executor / Claude Code agent loop | **ReAct · Research · Dag 的编排部分**（async task） |
| **T2 常驻子进程池** | CPU 密集、可取消、需隔离 | Jupyter kernel / Temporal activity worker / Claude Code #26616 诉求 | **回测 · DuckDB 批处理 · DAG 重节点**（executor 池） |
| **T3 OS 沙箱 / 远端** | 不可信代码 | Codex bubblewrap+seccomp / OpenClaw sandbox（默认关）+ cloud workers | **Tier 2 基因组 strategy.py**（沙箱占位，v1 不做） |

派发面分离（Temporal 教训）：**runtime 走 T1，重工具走 T2，互不混道**——一个 runtime 永不因等回测而占住错误层。

## 4. 本项目适配分析（约束 → 结论）

| 约束 | 影响 |
|---|---|
| **Windows 开发机（win32）** | 无 Seatbelt/Landlock/bubblewrap/seccomp——**业界沙箱全家不可用**；Windows 侧只有 Job Object（资源上限）/restricted token/容器（后置）。诚实结论：**进程边界是当前唯一现实隔离**，深度沙箱是 Linux 部署后的事 |
| Windows 无 fork，spawn 重导入 | executor 池用**常驻子进程**（一次 spawn 反复派活），不用按任务 spawn；派发载荷必须可 pickle（契约要窄） |
| DuckDB 查询释放 GIL | 轻量 DB 查询 T1 可行；但 Python 后处理 GIL 绑死 → 真 CPU 活一律 T2；**每个 executor job 自开连接**，不跨进程共享连接 |
| core/study 已用 langgraph | ResearchRuntime 的 checkpoint/恢复语义有现成底座，T1 归属无摩擦 |
| 安全债在案 + Tier 2 自改代码 | T2 executor 即安全边界的种子：先进程隔离 + 最小权限，沙箱位留给 A5/P3 |
| 单用户单机 | 池大小 = CPU−1 起步；容量组（study 闸）与池上限联动 |

## 5. 定稿：GraphEngine 统一执行模型（2026-10-24 用户确认）

> 演进轨迹：三 runtime（ReAct/Research/Dag）→ 2 runtime + Fanout 能力 → **GraphEngine 唯一 runtime**。
> 最后一步由用户提出：ReAct 是标准节点（每阶段可开/关）、Research 是图的特化 preset、配置文件自由配置执行方式——即 LangGraph 的统一模型，且 langgraph 已在依赖中（core/study 分支），包裹而非手搓。

### 5.1 模型

```
GraphEngine（唯一 runtime · 包裹 langgraph · 支持环——ReAct 是环，DAG 装不下它）
 ├─ 节点类型（每阶段开/关 ReAct 就在这）：
 │    llm 单发（关）· react 循环（开，现 AgentLoop 代码平移）
 │    cpu → ExecutorBridge → 子进程池 · graph 子图嵌套
 ├─ Preset 注册表（图 = 纯数据）：
 │    chat = 单 react 节点退化图
 │    research = 方法论阶段图【Tier 0 冻结】
 │    fanout / dag 形状【Tier 1 可提案（D2 通道）】
 └─ 执行红线（引擎性质，不入配置）：图根 = turn 走 mailbox ·
      cpu 子 run 不得为 study round · 取消连坐 · fence 挂根 · 事件带 run_id
```

**治理原则：配置的普适性 ≠ 配置的可变性。** preset 每张带治理等级；research preset v1 冻结，方法论进化 = preset 版本化 + 提案门禁（RSI 愿景的正路）。preset 默认值（fanout 并发 8、失败 abort_turn 等）先用代码默认，有使用数据后再抬到提案通道。

### 5.2 多 subagent 执行（七条规则，2026-10-24 定）

1. **两级准入**：turn 准入走 mailbox（SessionController）；subagent 走 `subagent:{session}` lane（默认 8），不碰 mailbox——不自锁。不变量改述：「唯一开 **turn** 路径 = admission」。
2. **树形身份**：子 run_id = `{parent_run_id}:{ordinal}`，控制器仍只签根 run。
3. **两类子 agent 两层资源**：llm/react 型 → T1 asyncio task（lane 限流 + LLM 速率上限）；cpu 型 → T2 executor 池 job。
4. **预算树形切分**：父预留、子消费、settle 归还（分层预算 attempt 层）。
5. **steer/interrupt 只认 turn**：注入邮箱挂根 run；CancelScope 是树，级联取消（T1 cancel + T2 取消通道 5s 硬杀）。
6. **事件带 run_id 进 EventStore**：WebUI 画 turn→子树；大结果存储直写 + 事件带指针。
7. **子无逃逸路径**：cpu 子 run 不得为 study round（home session 串行不可绕）；升格 turn 须由父 runtime 提 followup。失败 v1 = 标红 + 层策略，降级阶梯推 C3。

### 5.3 Executor 池与 job 契约

```
Gateway 进程（T1）：GraphEngine + ExecutorBridge（dispatch(job)→JobHandle）+ 容量组闸
Executor 池（T2）：spawn 常驻子进程 × CPU−1（Windows 无 fork，一次 spawn 反复派活）。**池型评估定论（2026-10-26）：必须进程池**——协程池被 GIL 排除（纯 CPU 串行 + 霸占事件循环冻住 Gateway）；线程池被两点排除（共享内存无故障隔离，C 扩展段错误=Gateway 陪葬；Python 无法硬杀线程，取消升级契约失效）；不用现成 ProcessPoolExecutor（缺中途事件流/取消升级/心跳），基于 multiprocessing spawn 自建，整池包 Windows Job Object。
沙箱位（T3 占位）：Linux 部署后接 bubblewrap+seccomp，先服务 strategy.py
```

job 契约要点（Jupyter 四通道 + Temporal 活动语义）：dispatch / events / cancel / heartbeat 四通道；协作取消优先、5s 升级硬杀、被杀产物由 fence 拦截；幂等键贯通 job 级；事件回流即 EventStore 事件；**大结果不走事件流**（直写存储+指针）；载荷白名单可 pickle；每 job 自开 DuckDB 连接（写冲突归 2.4 单写者设计）；**Windows Job Object（KILL_ON_JOB_CLOSE）为池首日要求**（防孤儿进程 + 资源上限一石二鸟）。

### 5.4 与刀号的关系（更新）

- **A1** = GraphEngine 骨架 + llm/react 两节点类型 + ExecutorBridge；T1 阻塞纪律以 CI enforcement 为验收（asyncio debug + 阻塞检测 fixture）
- **B1** = research preset 化（phase_engine→图数据）+ fanout/dag 形状解释；swarm 控制面代码死亡不搬家
- **D2** = preset 提案通道（Tier 1）；2.4 = DuckDB 单写者与 executor 写路径合并设计

## 6. 开放点 → ✅ 2026-10-26 定案（遗留清零批量拍板）

1. executor 池 ✅ **单池 + 容量组闸**（池=资源、闸=策略；Job Object 首日）。重开条件：多租户公平性 / 异质环境（GPU、独立 python env）/ 多机。
2. T3 沙箱时机 ✅ **两段式**：第一段随 executor 池首日（进程隔离 + Job Object + 工作区写限 + worker 断网[防火墙规则，A1 时验证]）；第二段绑 P3 D2 前（WSL2 + bubblewrap/seccomp）。残余风险如实声明：两段之间恶意/被注入代码在用户权限下运行（单用户自用可接受）。
3. heartbeat 间隔 ✅ **A1 实现时定**（Jupyter hb 默认 3s 量级起步）。

## 7. 出处

- Claude Code：[sandboxing](https://code.claude.com/docs/en/sandboxing) · [#26616 worker subprocess per tool](https://github.com/anthropics/claude-code/issues/26616) · [Beltdown](https://accomplish.ai/blog/beltdown-escaping-the-claude-code-sandbox) · [CodeAnt 逃逸](https://codeant.ai/blogs/claude-code-macos-sandbox-escape)
- Codex：[linux-sandbox README](https://github.com/openai/codex/blob/main/codex-rs/linux-sandbox/README.md) · [Agent-Safehouse 分析](https://agent-safehouse.dev/docs/agent-investigations/codex) · [Novee 沙箱对比](https://novee.security/blog/breaking-ai-agent-sandboxes) · [CSA 逃逸披露](https://labs.cloudsecurityalliance.org/research/csa-research-note-openai-codex-sandbox-escapes-20260922-csa)
- OpenClaw：[sandboxing](https://docs.openclaw.ai/gateway/sandboxing) · [cloud workers](https://docs.openclaw.ai/gateway/config-cloud-workers) · [Codex harness 参考](https://docs.openclaw.ai/plugins/codex-harness-reference/approval-and-sandbox)
- Temporal：[task-queue](https://docs.temporal.io/task-queue) · [how-temporal-works](https://docs.temporal.io/encyclopedia/architecture/how-temporal-works) · [worker performance](https://docs.temporal.io/develop/worker-performance)
- LangGraph：[架构综述](https://www.emergentmind.com/topics/langgraph-architecture)
- Ray/Dask：[Domino 选型](https://domino.ai/blog/spark-dask-ray-choosing-the-right-framework) · [Dask vs Ray 基准](https://www.jennakwon.page/2021/03/benchmarks-dask-distributed-vs-ray-for.html)
