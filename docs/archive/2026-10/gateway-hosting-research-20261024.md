# Gateway 宿主形态调研与定案 — 2026-10-24

> 问题：v6 定了「Gateway 单进程 · 唯一控制面」，但**「单进程」是哪个进程**没回答。
> 结论：**Gateway 挂载式单进程**——Gateway 是进程的主人，FastAPI 是被它挂载的渠道之一。业界 C 家族（专职守护）的唯一合理变体，每条选择有业界先例。
> 状态：宿主形态已定案（决策 14）；session 粒度讨论中（§5）。

---

## 1. 三个候选方案

| 方案 | 形态 | 致命问题 / 代价 | 结论 |
|---|---|---|---|
| 独立 daemon（OpenClaw 原生） | gatewayd 常驻，全部渠道走 WS 客户端 | 多付一整套 client SDK + 连接管理 + WS 协议——20+ 渠道、多设备、always-on 才值得；我们 4 渠道单机单用户 | 否 |
| 寄生 FastAPI | web 进程「顺便」带大脑 | **腐烂方向**：request-scoped 概念（中间件/DI/钩子）会长进控制面路径；「重启一下 API 试试」变成习惯动作——而重启就是杀 run | 否（正确的成本、错误的腐蚀方向） |
| **Gateway 挂载式** | `qs gateway` 是进程主人，挂载 uvicorn（WebUI·HTTP）/ MCP 适配器 / scheduler；CLI = HTTP 客户端 | 单故障域（单用户工作台可接受，缓解见 §4） | **✅ 定案** |

寄生 → 挂载是**所有权反转**，一字之差：大脑是单元，HTTP 是外设。依赖方向随之理顺：`core/gateway` 零 FastAPI import，FastAPI 适配器 import Gateway——顺手消掉问题①反向依赖的病根。

## 2. 业界宿主形态全集（四模式）

**决策定律：宿主形态不是品味问题，被两个需求决定——「状态必须住在哪」+「工作必须什么时候发生」。**

| 模式 | 代表 | 状态住在哪 | 什么时候工作 | 适用条件 |
|---|---|---|---|---|
| **A · 无守护**：一 session 一进程 | Claude Code · Aider · Codex CLI 本地 | 磁盘（transcript 文件，`--resume` 重读） | 用户开着才有 | 接受「关了就停」，无 automation |
| **B · 应用即宿主** | ZCode 桌面版 · Cursor/Windsurf | IDE / 桌面 app 进程内 | app 开着 | 有常开的桌面壳 |
| **C · 专职守护 + 薄客户端** | **OpenClaw** · Jupyter Server · LangGraph Server · Temporal | 常驻服务进程（状态可重建） | 7×24 | 需要 automation / 多渠道 / 服务化 |
| **D · 云托管** | Devin · Codex cloud · Jules | 厂商服务器舰队 | 7×24 | 产品即服务 |

**我们的推导**：需求清单里有一条把 A/B/D 全部排除——**nightly scheduler 要在没人开 CLI 时发起研究**（automation 是一等公民）。所以只剩 C 家族；C 家族内部的问题只剩「守护进程里放什么」，见 §3。

## 3. 业界先例对照（v6 每条决定 → 先例 → 偏离）

| v6 决定 | 业界先例 | 偏离点及理由 |
|---|---|---|
| 常驻 Gateway 进程 | OpenClaw / Jupyter Server / LangGraph Server | 无 |
| CLI/WebUI/MCP = 薄客户端 | OpenClaw / Jupyter（浏览器也只是客户端） | 直接挂 HTTP，不要 WS+TypeBox 机器（单机单用户） |
| **控制面内无重计算**，执行下放 executor 子进程 | **Jupyter server↔kernel**（ZMQ 相连，十多年验证）：跑卡死 numpy 的 kernel 不冻结 server 与其他 kernel | 无——同构复刻：回测/DAG 批下放子进程，ReAct 循环（IO 密集等 LLM）留进程内 |
| **事件溯源 + fold 重建，重启 = 安全常规操作** | **Temporal** 信条：「恢复不是特殊路径，是常规操作」；进程重启从 DB fold 全部运行态 | 单机文件级（EventStore），不上 DB 集群 |
| mailbox / 工具边界注入 / 拒绝不降级 | OpenClaw session-controller（已调研：`queue-mode-research-20261024.md`） | 无 |

**唯一属于我们自己的形状是产品层**——研究方法论、Director、RSI——它们全住在管线后段，与宿主无关。**v6 宿主架构是业界收敛解的组装，不是冒险创新；风险集中在工程执行，不在方向。**

## 4. 定案清单

1. **宿主 = Gateway 挂载式单进程**：`qs gateway`；`--no-http` 即无头研究盒（scheduler 形态免费获得），不另起炉灶。
2. **前置① workers=1 钉死**：uvicorn 多 worker = 多 Gateway = mailbox/状态机当场分裂。启动时自检，发现多 worker 拒启——进代码，不靠约定。
3. **前置② core/gateway 自包含**：独立 start/stop 生命周期，渠道只挂载不拥有；FastAPI 只是宿主之一。保留将来抽 gatewayd 的期权而不预付成本（触发条件：出现「API 重启不能杀研究」的真实痛点）。
4. **前置③ 状态可重建**：内存运行态 + EventStore 溯源；宿主重启 = fold 重建（dev reload / 崩溃 / 发版全是免费灾难演练）。配合幂等键 + fence 拒陈旧提交。
5. **重执行下放**：控制面内不得出现重计算——回测/DAG 批在 executor 子进程，Gateway 只持有控制。
6. **CLI 变 HTTP 客户端**：无 daemon 时明确报错不挂起；auto-spawn（CLI 检测不到就拉起 gateway）留 E2，不阻塞定案。

**已知代价**：单进程 = 单故障域。缓解已内建（恢复哲学），多进程 HA 是另一档成本，现阶段无理由付。

## 5. session 粒度（讨论中，未定案）

**问题**：「chat 与 study 共用 SessionController」≠ 共用一个 session（否则 2h 研究让提问排 2h 队）。

**草案五条**：
1. session = **turn 串行域**（准入作用域），不是用户/进程/study 实体；session 内串行，并行只出现在 turn 内部（DAG 归 runtime）。
2. 交互研究：**study round = chat 会话里的一个长 turn**——期间新消息走 queue_mode 五行表（steer/followup/interrupt），零新机制。
3. 无发起会话的来源各有其 session（automation 按任务、MCP 按客户端、Director 按目标）；**study 实体记录 `home_session_key`**——任何来源跑它的 round 都提交到 home session：同一 study 的 round 永不并行，人工与 scheduler 接力同一 session，fence 只看一处。
4. 跨 session 并发归**容量组**（chat 宽 / study 窄 / DAG 另组）——「并发满」拒绝的作用域。
5. session 管时序，**账本管事实**：hypothesis/journal/claims 属 study 实体，与 session 解耦，会话关了研究资产不丢。

**自动解决**：Director followup 进 home session 天然排队；「问进展」走 status/事件流不是 turn。

**已知代价**：研究期间该会话被占用，「边跑边聊」须另开会话——OpenClaw 会话模型固有形状，留给 UI 引导（会话标「研究中」）。

**待拍两点 → ✅ 2026-10-26 定案（遗留清零批量拍板）**：
1. `home_session_key` ✅ **定死**（首轮 session；重开条件：出现「研究工作区合并多会话」的真实产品需求）。
2. chat 里发起 study ✅ **不问用户**，默认占本会话 + toast 提示「本轮预计 X 分钟」（重开条件：用户实测反馈被占会话困扰）。

---

## 6. Gateway 内部设计（2026-10-24 定案）

### 6.1 内部依赖方向（谁认识谁）

```
协议端点 ──► mailbox ──► SessionController ──► RuntimeHub（原 RuntimeSelector，10-26 定名）──► GraphEngine 执行
                ▲              │  ▲                                  │
                │              ▼  │                                  ├─► ContextHub（每次模型调用）
                │         EventStore ◄──── 产物/fence 校验 ◄──────────┘
                │              ▲
        Director ┘             │
        Scheduler ──► mailbox  │（Director/Scheduler 也从 EventStore 读 fold）
```

- 端点只认识 mailbox；mailbox 只排序不裁决；**SessionController 是唯一裁决者**
- RuntimeHub 解析 turn 的执行绑定（preset 图 + provider/模型）；ContextHub 只被引擎调用，控制器不碰
- Director / Scheduler 以普通提交者身份进 mailbox，**无特权路径**
- **模块间唯一共享可变状态 = EventStore**

### 6.2 六条性格不变量

1. 控制面内无模型调用（Director 要 LLM 就作为 turn 被准入）
2. 控制面内无重计算（回测/DAG 批 → executor 子进程）
3. 控制面方法快同步无 IO（mailbox 预留 O(1)）
4. 唯一开 run 路径 = admission
5. run 产物落库必过 fence
6. 模块间不共享状态，只共享事件

### 6.3 生命周期（定案）

- **启动**：重放 EventStore → fold 会话状态/调度 deadline → 重建 mailbox 未决项 → 挂渠道 → 开准入。恢复的是**状态**，不恢复**执行**。
- **中断 run 的恢复策略：可配置，默认不自动恢复**（2026-10-24 拍板）——被打断的 run 标记 interrupted，重试靠重新提交（幂等键保证安全）；理由：run 因 steer 注入不可重放。配置项形如 `gateway.recovery.auto_resume = false`。
- **关闭**：停止准入 → 通知 runtime 优雅收尾 → **等 grace（默认 ~5 分钟，可配置）** → 超时者标 interrupted → 落盘事件尾 → 退出。

### 6.4 收编映射（模块 ← 现状代码）

| Gateway 模块 | 收编 |
|---|---|
| mailbox + SessionController | SessionService / StudyRunner / SwarmRuntime 的控制部分 |
| RuntimeSelector + 3 runtime | role_factory / chat_loop / phase_engine / swarm runtime |
| ContextHub | 4 builder + 5 injector |
| Scheduler | 3 处调度收敛 |
| EventStore | event_store + SSE 双轨 |
