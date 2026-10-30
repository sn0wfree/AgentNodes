# 下一阶段计划（2026-10-30 · 基于 v6.3 架构图核实结果）

> 依据：`architecture-diagram.html` **v6.3**（同日对照代码逐格核实，✅/⏳/⚠/⏸ 标注与实现一致）。
> 性质：接替 `project-status-and-roadmap-20261028.md` 的路线表；沿用三道验收门
> （行为等价 / 测试不减 / 失败集不扩大，见 `docs/stage-c1-acceptance-20261028.md`）。
> 编号用 **R1–R6**，避免与历史 A/B/C/E 刀号混淆。
> 工期为单人估算；多 agent team 并行可压至 ~60%（关键路径不变）。

---

## 1. 剩余工作盘点（v6.3 核实结论）

| 桶 | 内容 | 项数 |
|---|---|---|
| ⚸ 挂起 | P5-1c 删旧 chat 分支（业务稳定优先，用户已拍板挂起） | 1 |
| ⚠ 建而未接线 | SessionFence（零调用方）、PlatformStore（未传 data_dir） | 2 |
| ⏳ 已上线链的缺口 | 常驻消费者、steer 真注入、优先级老化 | 3 |
| ⏳ 覆盖面缺口 | CLI/MCP 入口、study 收编（home_session）、容量组闸、Scheduler 唯一 | 4 |
| ⏳ 数据面 | P-3 登记接线、P-5 指针、每 session 分库、PROTOCOL_VERSION | 4 |
| ⏳ 执行面 | cpu/graph 节点、RuntimeHub、subagent lane、preset 治理分级 | 4 |
| ⏳ IP 层 | 方法论归位 core/study/method/、RunContext、Director、D2 提案通道 | 4 |
| 欠账 | 104 条历史基线失败（重构前既有，非本轮产物） | 1 批 |

---

## 2. 阶段总览

| 阶段 | 主题 | 工期 | 依赖 | 完成后图上翻格 |
|---|---|---|---|---|
| **R1** | 接线收口：孤儿模块处置 | ~1d | — | fence 挂根 ⏳→✅ |
| **R2** | chat 链补全：把活链做完整 | ~2-3d | R1 | 常驻消费者/steer/老化 ⏳→✅ |
| **R3** | 入口全覆盖 + study 收编 | ~3-4d | R2 | CLI/MCP 入口、容量组闸、home_session ⏳→✅ |
| **R4** | 数据面落定 | ~2-4d | —（可与 R3 并行） | P-3/P-5/PROTOCOL_VERSION ⏳→✅ |
| **R5** | 执行面扩展 | ~2-3d | —（可与 R3/R4 并行） | cpu/graph 节点、RuntimeHub、lane ⏳→✅ |
| **R6** | 研究域与导演（IP 层） | ~4-6d | R3（控制面完整） | 方法论归位、RunContext、Director ⏳→✅ |

合计 ~14-21d 单人；**关键路径 = R1 → R2 → R3 → R6**。

---

## 3. 各阶段明细

### R1 接线收口（~1d）

| # | 事项 | 说明 | 验收 |
|---|---|---|---|
| R1.1 | **fence 挂根** | `SessionFence` 接进 SessionController：turn 准入时 claim（activeRunId），`run.settled` 前校验，取代/取消的 run 拒绝提交并 emit `run.fenced`。不变量 `_inv_fence_supersedes` 从自检变实测 | 新增 fence 并发测试；既有等价测试不破 |
| R1.2 | **chat 驱动点勘查** | 确认生产 chat 的 run_once 驱动位置与语义，为 R2.1 出设计输入（只勘查不动） | 勘查记录半页 |

**决策点 D1（R1 开工前拍板）**：fence 接线（**推荐**——R3.2 study 收编本来就依赖它）vs 直接退役。

### R2 chat 链补全（~2-3d）

| # | 事项 | 说明 | 验收 |
|---|---|---|---|
| R2.1 | **常驻消费者** | 后台 worker 消费 mailbox，替代请求路径内 run_once 同步驱动；submit 即回 receipt，渲染走 SSE 事件流 | chat 请求 submit 后同步路径零工作 |
| R2.2 | **steer 真注入** | 兑现 A4 延期项：steer 队列 + react 循环安全点注入（只追加），删除 `steer.downgraded` 降级桩。语义按 `queue-mode-research-20261024.md` 五条细则 | steer 注入事件 `steer.applied`；降级路径仅保留给不支持的节点 |
| R2.3 | **优先级老化** | 领先有界（~15s 量级），防 automation 饿死用户消息 | 老化触发事件 + 单测 |

**决策点 D3（R2 上线后）**：P5-1c 触发条件——**推荐「R2 上线一周无回归后执行」**；届时旧 ContextBuilder 失去存在理由，`docs/todo-remove-legacy-chat-branch-20261027.md` §0.3 ⓿ 起步。

### R3 入口全覆盖 + study 收编（~3-4d）

| # | 事项 | 说明 | 验收 |
|---|---|---|---|
| R3.1 | CLI/MCP 入口接适配器 | `cli_request`/`mcp_request` 已有，补调用方与消费者分 lane | 四渠道全走 WorkRequest |
| R3.2 | **StudyScheduler 收编** | study round 走 `scheduler_request`（AUTOMATION kind）→ **home_session_key**（同实体永不并行）→ 消费者分 lane；round 提交过 fence（R1.1 成果第一次真用） | 同一 study 两 round 并行投递被串行化；scheduler 测试基线不扩大 |
| R3.3 | 容量组闸 | chat 宽 / study 窄（=executor 池上限联动）/ cron 隔离；满→排队不拒绝 | 闸触发事件 + 并发单测 |

### R4 数据面落定（~2-4d，可与 R3 并行）

| # | 事项 | 说明 | 验收 |
|---|---|---|---|
| R4.1 | **P-3 登记接线** | PlatformStore 的 artifact 登记部分单独接线，第一用户=回测结果（kind v1）；大产物两步可见 | 回测产物有登记行 + 跨会话可发现 |
| R4.2 | P-5 指针四件套 | `{uri,sha256,bytes,schema_v}`，根=workspace；挂 P-4 血统 | 指针可校验（sha256 对得上） |
| R4.3 | PROTOCOL_VERSION | 事件带协议版本，回放遇不匹配即拒绝（0.5d 小刀） | 版本不匹配回放被拒测试 |

**决策点 D2（R4 开工前拍板）**：每 session 分库（10-26 定案）——
**a. 延后分库 + 仅接 P-3（推荐）**：统一库 WAL + 归档压力未出现，分库是大迁移；PlatformStore 的登记价值先兑现，分库留重开条件（单库 >2GB 或归档需求出现）。
**b. 全量分库**：按 10-26 定案执行，估 3-5d 额外。
**c. 退役 PlatformStore**：与 a 冲突（a 需要它的登记 API），仅当连 P-3 也判定不做时选。

### R5 执行面扩展（~2-3d，可与 R3/R4 并行）

| # | 事项 | 说明 | 验收 |
|---|---|---|---|
| R5.1 | cpu 节点类型 | 注册 cpu 节点 → ExecutorBridge dispatch（fanout-t2 已验证桥，缺的只是节点类型）；cpu 子 run 不得为 study round 红线随节点落地 | cpu 节点跑通子进程池 + 硬杀 |
| R5.2 | graph 子图节点 | 嵌套图执行（子 run 树） | 子图中断连坐回根 |
| R5.3 | RuntimeHub | 解析序 model > provider > auto > 回退；不支持则拒绝不降级；preset 绑定收口；RPM/TPM 限流接 provider client 层（排队不拒绝） | 解析序测试 + 限流排队测试 |
| R5.4 | subagent lane | `subagent:{session}` 专用串行道，默认并发 8，不碰 mailbox 防自锁 | lane 并发上限测试 |

### R6 研究域与导演（~4-6d，依赖 R3）

| # | 事项 | 说明 | 验收 |
|---|---|---|---|
| R6.1 | 方法论归位 | `core/study/` 拆 `method/` + `engine/`（老决策 9），**纯搬家不改行为** | import 全通 + study 测试不破 |
| R6.2 | RunContext | per-run 约束包：预算快照（接 BudgetGate）/ EventSink / CancelScope / 注入通道；回答「在什么约束下跑」 | 引擎从 RunContext 取约束，不再散传 |
| R6.3 | Director | `propose_next`：停因→动作，产出 BranchContext 以 followup 提交 mailbox；红线=不读预算、不得自行开 run | 停止的 study 产出下一步提案事件 |
| R6.4 | preset 治理分级 + D2 | research preset Tier 0 冻结（版本化）；Tier 1 提案通道（D2 落地处）；脱敏指纹进事件流 | 冻结 preset 被改即拒绝 |

---

## 4. 横切项

| 项 | 策略 |
|---|---|
| 104 条历史基线失败 | **不单独立项**：每阶段收尾顺手清偿该阶段触碰文件内的基线失败（less-is-more）；失败集只许缩小不许扩大（验收门三） |
| CapabilityRegistry / L2 统一注册 | 不单独立项：R3/R5 触碰注册表时顺带收敛，`PROTOCOL_VERSION`（R4.3）先行铺协议底座 |
| 宿主进程（qs gateway · fold 重建 · grace） | 延后：单机单进程当前无重启恢复压力；重开条件=生产部署形态确定时 |
| 图即看板 | 每完成一项，把 `architecture-diagram.html/svg` 对应格子 ⏳→✅（虚线→实线）随该刀一起提交 |

---

## 5. 排序理由

1. **先清孤儿（R1）**：less-is-more 不允许「建而未接线」长期存在——接线或退役必须二选一，且 fence 是 R3.2 的前置。
2. **先把活链做完整（R2）再扩面（R3）**：chat 是唯一在生产跑新链的渠道；steer 降级桩和同步驱动是每天在发生的真实妥协，比新增覆盖面优先。
3. **数据面（R4）与执行面（R5）放后且可并行**：它们不阻塞任何人，且各自有独立决策点（D2），不与关键路径抢人。
4. **IP 层（R6）最后**：方法论归位老决策 6 的前提「控制面 OK」直到 R3 才真正成立；Director/RunContext 的正确形状依赖完整的控制面契约。
5. **P5-1c 保持挂起但给出触发条件**：不是无限期，是「R2 上线一周无回归」——届时删除的最后一个风险（上下文注入等价）已被生产验证。

---

## 6. 决策点汇总

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| D1 | fence 接线还是退役 | a 接线（1d 内含在 R1）/ b 退役 | **a**（R3.2 依赖） |
| D2 | 每 session 分库 | a 延后分库+仅接 P-3 / b 全量分库 / c 退役 PlatformStore | **a**（重开条件：单库>2GB 或归档需求） |
| D3 | P5-1c 触发时机 | a R2 上线一周无回归后 / b 继续无限期挂起 / c 立即 | **a** |
| D4 | 104 历史失败 | a 阶段收尾顺手清 / b 单独立项 | **a** |
