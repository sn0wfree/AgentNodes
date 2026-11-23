# AgentNodes 架构重构执行路线图

> 状态：规划稿，待讨论与分批批准；本文件不授权任何代码改动或删除。  
> **基线说明**：Git CLI 当前无法启动，无法独立确认 HEAD 或工作区 diff。最近已知代码 ref 为 I1 的 `97700326`；其后 I2 已有实施与验证记录（提交 ref 未确认）；I2 方案文档 ref 为 `0ff16e2c`。状态复核：2026-11-20。
> 编制依据：四层骨架、三前端一核心、隔离执行 B2.2、M12/M13、兼容迁移与顺序⑥审计等设计记录。  
>
> **状态更新（2026-11-20，本轮复核，取代此前状态快照）**：
> - B2.3 上下文跨界 3a–3c、B2.4 生产接线/lease 顺序 4a–4d、B2.5 准入与临时工作区 write 试点 5a–5c 均已完成。B2.4 生产分支仍由默认关闭的开关和空 allowlist 保护；B2.5 仍是测试/脚本级试点，零生产消费者，shell 未接。
> - 深化批次已完成：`IsolationAdmission`、`IsolatedToolExecutor`、执行器入口收敛、RSI host/consumer seam、G1 显式 emit、S1 会话归属 helper、goal 覆盖口径统一；M11b 与 S2 会话删除生命周期也已完成。
> - WP-8 3b.3/3b.4 的核对完成；其余 wiring 结构收敛仍需独立方案。WP-9 的 M6 尚未开始；M11a/b 已完成。
> - I3 父侧 ctx 校验（`43ff7126`）、I1 E5 declared 单点解析（`97700326`）与 I2 `IsolationChannel.read()` 返回契约均已完成；I2 执行记录将其方案 ref 记为 `0ff16e2c`。
> - 最近一次全量测试记录（I2 执行记录）为 `8062 passed / 0 failed`，`batch_gate` ALL GREEN；架构图与框架数字已同步到实测 464 文件 / 97,025 行，层间上行违规 0。测试为既有执行记录，本轮未重跑；当前工作区状态无法确认。

## 0. 目标与执行原则

本路线图把已有架构方向转成可由不同执行者分批完成、可验收、可回滚的工作包。目标不是一次性搬迁所有代码，而是以**窄接口、深模块、稳定 seam、测试穿越接口**的方式逐步收敛职责，避免把复杂度从一个文件散落到多个浅层转发模块。

执行约束：

1. **先裁决、后实施**：每个工作包先确认其设计记录与本路线图中的前置决定；涉及接口、层级归属或删除时，单独完成 R-014 方案审查。
2. **一次一个可回滚单元**：一个工作包对应清晰的文件所有权、测试范围和提交；主体迁移与删除分开提交。
3. **先探针、后迁移**：对新 seam 先用一个代表性调用贯通调用方、实现和测试，再扩大迁移面。
4. **按依赖并行，不按标签并行**：只有文件所有权不重叠、前置裁决已完成的工作包才并发；`chat.py`、session/config 路径和正在进行的隔离工作区是热区。
5. **不改变现存契约的默认值**：除非单独批准，不改变 HTTP 状态码、超时承诺、外部导入面、平台支持范围或工具副作用语义。
6. **删除最后做**：遵循 R-015、R-016、R-020；删除前重新审计真实引用、出口和外部消费者，并把删除做成独立提交。

## 1. 当前基线与工作区注意事项

### 1.1 Git 基线

- 最近已知代码 ref：`97700326`（I1）；I2 随后已有实施记录，但其提交 ref 与当前 HEAD 均无法通过 Git CLI 独立确认。I2 方案文档 ref `0ff16e2c`，状态已从待批更新为已实施。
- M5 变更包括 `src/agentnodes/interfaces/cli/commands/slash_goal.py`、新增 `src/agentnodes/apps/goal/audit.py` 与 `tests/test_goal_audit.py`，提交 `cf03f038`。
- 当前官方架构数据：L1 121/21,946、L2 50/11,542、L3 229/51,333、L4 64/12,204，共 464 文件 / 97,025 行；`sync_architecture_diagram.py` 与 `check_framework_numbers.py` 均通过，层间上行违规 0。
- 最近可核实全量 pytest 记录（I2 执行记录）为 `8062 passed / 0 failed`（`-n 6`），`batch_gate` ALL GREEN；该测试证据来自既有执行记录，不代表本轮重新执行。
- 下一轮代码改动后按 R-007 重跑聚焦测试、全量 pytest、batch_gate，并按实际文件/行数变化更新架构图和 framework 数字。

### 1.2 已完成且不应重开的问题

| 事项 | 当前结论 |
|---|---|
| 四层方向 | `interfaces → apps → kernel → foundation`；新增/迁移文件须登记并通过层级门禁。 |
| 4c B2.1 工具准入 | 已提交 `1a991dbc`；只建立准入声明，不改执行路径。 |
| B2.2–B2.5 隔离执行 | 3a–3d、B2.3 3a–3c、B2.4 4a–4d、B2.5 5a–5c 均完成；隔离能力位于 L3。B2.4 生产路径有 guarded 接线但默认开关关闭、allowlist 为空；B2.5 write 仅测试/脚本试点，shell 不接，POSIX 生产执行仍禁用。 |
| M12 组合根收敛 | 已完成；包含组合根生命周期/定位与 `atexit` 回调引用释放修复（`ac6f7dd2`）。 |
| M13 registry 职责/终态 | 两个 registry 互补、不合并；已知终态保留且不自动 prune（`92b48e4c`），重启/持久化后的已知终态语义仍单独待裁决。 |
| 三前端一核心与深化批 | WP-8 3b.1/3b.2 完成，3b.3/3b.4 核对完成，剩余 wiring 收敛另案；WP-9 的 M3/M5/M7/M8、M9-1/2/3/4、M11a/b 完成，M6 待做。IsolationAdmission/IsolatedToolExecutor、G1、S1、I1、I3、goal 覆盖统一、RSI 消费者入口均已实施。 |
| S2 会话删除生命周期 | `61676773` 完成；Web/CLI 共用归档与投影失效，新增行为测试覆盖两入口缓存失效。 |
| 隔离契约 I1/I2/I3 | 已完成：I1 declared capability 单点解析、I2 `IsolationChannel.read()` 显式 data/timeout/eof 契约、I3 父侧 ctx 校验；见各 R-014 执行记录。 |
| OptionsPricingTool、hooks | 保留；不是删除候选。 |
| 陈旧文档引用 | 已审计并完成当时批准的修正；后续只在相关批次确有引用变化时再更新。 |

### 1.3 方案记录索引

- [分层骨架设计](./layered-skeleton-design-20261103.md)
- [三前端一核心设计](./l1-three-frontends-one-core-design-20261119.md)
- [隔离执行 B2.2 设计](./isolated-execution-b22-design-20261119.md)
- [B2.3 上下文跨界 R-014 方案与执行记录（3a–3c 完成）](./b23-context-crossing-r014-20261120.md)
- [I3 父侧 ctx 校验 R-014 方案与执行记录（已完成）](./ctx-parent-validation-r014-20261120.md)
- [I1 E5 declared capability 单点 R-014 记录（已完成）](./declared-capability-single-source-r014-20261120.md)
- [I2 IsolationChannel.read() 返回契约 R-014 与执行记录（已完成）](./channel-read-contract-r014-20261120.md)
- [S2 会话删除生命周期归一与执行记录（已完成）](./session-deletion-lifecycle-r014-20261120.md)
- [WP-8 evolution 迁移清单与执行记录](./wp8-evolution-migration-checklist-20261120.md)
- [步骤 4c / 删除待批准项](./pending-approvals-20261119.md)
- [M13 Workflow registry 复审](./m13-workflow-registry-review-20261119.md)
- [Chat send_turn 用例提取](./chat-send-turn-usecase-extraction-20261119.md)
- [Legacy Chat API 兼容复审](./legacy-chat-api-compat-review-20261118.md)
- [顺序⑥审计](./sequence6-audit-20261119.md)
- [兼容迁移指南](../guides/legacy-chat-api-migration.md)

## 2. 全局依赖图

```text
当前状态基线（已知代码 ref 97700326 之后 I2 已实施，精确 HEAD 未确认；B2.2–B2.5、M11b、S2、I1–I3 已完成）
                    │
                    ├── B2.2 3a–3d ── B2.3 3a–3c ── B2.4 4a–4d ── B2.5 5a–5c（均完成）
                    │                                  └── 生产隔离开关关闭、allowlist 空；write 仅测试试点
                    │
                    ├── M13 终态可见性裁决 ──> 有限保留/清理实现
                    │
                    ├── app 批 3b evolution（3b.1/3b.2 完成；3b.3/4 核对完成）──> wiring 结构收敛另案
                    │
                    ├── app 批 3c（M3/M5/M7/M8、M9-1/2/3/4、M11a/b 完成）──> M6 待做
                    │
                    ├── 会话删除生命周期 S2（Web/CLI 共用，已完成）
                    ├── I1 declared capability / I2 read 契约 / I3 父侧 ctx 校验（均完成）
                    │
                    ├── Chat 3a.4 清理重新审批 ──> 迁移/测试 ──> 删除独立提交
                    │
                    ├── Legacy Chat 完整正式版本周期结束 + 再审批 ──> 兼容出口删除
                    │
                    └── seq_generator R-014 方案/审批 ──> 四触点同步 ──> 独立退役提交

以上各线完成并通过架构复核
                    └── 最终架构数字、图、文档和残余清理审计
```

图中各支线在文件范围独立时可并行；同一热文件或共享接口不并行编辑。B2.2 链内严格按顺序推进，不能因其他支线并行而跳过平台/副作用验收。

## 3. 工作包与验收标准

### WP-0 · 工作区归属与当前树重基线

- **状态**：本轮已完成；基线更新为 `89ef1fc4`。
- **目标**：确认现存 diff 的所有者、完成边界和是否继续作为同一工作包；为后续分派建立可信基线。
- **范围**：只读盘点 `git status`、提交记录、架构数字和当前适用测试；不得改动已有用户文件。
- **前置**：维护者确认工作树由谁持有及本轮是否允许其他 Agent 进入隔离热区。
- **验收**：形成基线记录（HEAD、工作区 dirty 清单、适用测试结果、架构门禁结果、尚未验证项），并将既有变更映射到 WP-1，不把未提交改动算作已完成。
- **回滚**：无代码变更；若归属不清，冻结该文件组，不派发冲突任务。

### WP-1 · 隔离执行 B2.2 子批 3b：IPC 与 worker

- **状态**：已提交 `e161095d`。
- **目标**：完成 worker 通道、审批代理、结果/状态回传与 `actual_state` 语义闭环，仍不接入生产 AgentLoop。
- **禁止**：不改生产工具调用路径；不把“超时标记”当作“worker 已退出”；不在未确认进程退出前释放 lease；不扩大 shell 试点。
- **前置裁决**：3b 已完成；seam 所有权裁决已按用户批准落地，三文件于 `04a1255c` 迁回 L3 `kernel/agent/tools/`，回到 B2.2 设计 §2 原定落位。
- **验收**：IPC 协议/审批结果和状态词表覆盖正常、异常、拒绝、迟到结果；worker 退出结果可验证；Windows 路径测试通过；POSIX 保持明确 unsupported guard；保留既有 sync-only xfail。
- **验证**：先相关隔离测试，再 `batch_gate`、全量 pytest 和架构图同步检查；本包改动源代码时遵循 R-007。
- **回滚**：本包单独提交；失败时仅回退 IPC/worker 本批，不撤销已提交 3a adapter seam。

### WP-2 · 隔离执行 B2.2 子批 3c：临时工作区脚本试验

- **状态**：已提交 `89ef1fc4`；仍不接 AgentLoop/生产工具路径。
- **目标**：证明隔离执行能承载临时工作区的 `WriteFileTool` 脚本试验，不接生产 AgentLoop。
- **文件所有权**：只占用设计清单所需的隔离试验入口、工具 fixture 与专属测试；开始前列出最终文件清单，避免与 WP-1 并发。
- **前置**：WP-1 验收通过；3c 方案单独批准；确认副作用仅限临时目录。
- **验收**：S1–S4 覆盖真实临时工作区写入、deadline 终止并确认、迟到结果拒收/worker 毒化、lease 在确认停止后释放；POSIX 按理由 skip；shell 未接入。
- **验证记录**：5 个试点用例；隔离三组测试 `19 passed`；全量 `7874 passed / 106 skipped / 2 xfailed / 0 failed`；本次当前树 `batch_gate` ALL GREEN。
- **回滚**：整批回退试验接线和试验代码；保留 WP-1 通道。

### WP-3 · 隔离执行 B2.2 子批 3d：平台矩阵

- **状态**：已批准并完成，提交 `7171642e`。
- **目标**：明确 Windows、POSIX 及不支持平台的可承诺行为。
- **前置**：WP-1、WP-2 完成；3d 平台矩阵和 seam L3 归属均已批准并完成。Windows 真机覆盖、POSIX 守卫及未验证平台 unsupported 语义见 B2.2 §11。
- **验收**：Windows 本机真跑 adapter/IPC/试点行为；POSIX CI 真跑无平台依赖的守卫测试；未验证平台显式 unsupported。Windows CI 尚不存在，已在 B2.2 §11 记录，不冒充 CI 覆盖。
- **结果**：`tests/test_isolation_platform_matrix.py` 单独承载跨平台守卫测试；POSIX 生产执行仍禁用，3d 不接 AgentLoop。
- **回滚**：移除未达标平台的启用路径，维持显式 unsupported。

### WP-4 · 隔离执行 B2.3：上下文跨界

- **状态**：3a–3c 已实施并验收；上下文 codec、worker 解码、错误回传和探针均已落地，全程未接入普通生产工具路径。
- **目标**：定义并验证父进程/worker 之间必要上下文的传递与隔离，不传递不必要的进程内对象。
- **前置**：B2.2 子批 3a–3d 完成；B2.3 方案和阶段批准已完成。
- **方案/记录**：[B2.3 上下文跨界 R-014](./b23-context-crossing-r014-20261120.md)；3a–3c 的验收已逐项映射到 codec、Windows 真 worker 和全平台契约测试。
- **文件边界**：仅限隔离协议、上下文构造/解码与其测试；不与 Chat send-turn 生命周期迁移并发编辑。
- **验收**：最小上下文往返、无效/不兼容版本拒绝、白名单外键拒绝、worker 非毒化及生产入口父侧校验（I3）均已覆盖。
- **回滚**：协议版本化；回退到已验收的 B2.2 IPC，不改变普通工具调用。

### WP-5 · 隔离执行 B2.4：deadline、进程树与 lease 顺序

- **状态**：4a–4d 已完成；生产路径有受控接线，但开关默认关闭且工具 allowlist 为空。
- **目标**：把 E5 deadline 路径与 worker 进程树终止、退出确认、迟到结果拒收及 lease 生命周期按顺序接合。
- **前置**：B2.3 验收、隔离 seam L3 归属、独立生产接线方案与阶段批准均已完成。
- **禁止**：未收到确认退出就释放 lease；超时后接纳迟到产物；把幂等误当成取消；把进程终止描述成可撤销副作用。
- **验收**：deadline、终止确认、迟到结果拒收、per-call id、E5 终态和 lease 顺序均已有测试；B2.4 不表示默认启用隔离执行。
- **回滚**：可整体关闭隔离 deadline adapter，回到既有执行模式；不得保留部分生产接线。

### WP-6 · 隔离执行 B2.5：真实工具试点

- **状态**：5a–5c 已完成；仅测试/脚本级临时工作区 `write` 试点，零生产消费者，shell 未接。
- **目标**：在所有前置验收后，限定试点 `file_write` 到临时工作区。
- **前置**：B2.4 验收、工具准入和副作用边界复核均已完成。
- **范围/禁止**：仅临时工作区 `file_write`；shell 工具明确延期，不作为“顺手扩展”。
- **验收**：真实 write、越界拒绝、deadline/终止和 lease 顺序已由测试覆盖；发布说明明确进程终止不会撤销已发生的写盘副作用。
- **回滚**：按工具名关闭试点；不影响其他工具及既有调用。

### WP-7 · M13 Workflow registry 终态可见性

- **状态**：核心语义已由 `92b48e4c` 锁定；两个 registry 保持独立，已知终态保留，不接自动 prune。
- **目标**：定义 `WorkflowRunRegistry` 的终态运行记录可见时长与 approve/status 语义；不合并 `_active_runners` 与 `WorkflowRunRegistry`。
- **范围**：kernel definition-run registry、workflow router、对应 API/registry 测试。
- **必须保留的现有区别**：已知但不在 awaiting 状态的 run，approve 返回 **409**；未知 run 返回 **404**。
- **已完成**：kernel registry 直测确认任意多次 get 后仍保留终态；status 后 approve 的 409 正断言锁定；唯一移除路径为显式 pop/DELETE。
- **待裁决**：重启后内存 registry 为空但 store 有终态 run 时的 404/409 契约；`prune()` 零接线代码若要删除须另走 R-014/R-020。
- **验收**：已知终态保留、status 后 409、unknown 404 与 goal runner cache 独立性均由现有测试覆盖。
- **回滚**：清理策略与 hook 同一批回退；回到当前无自动 prune 的行为。

### WP-8 · 三前端一核心批 3b：`apps/evolution`

- **状态**：3b.1 `a2d18919`、3b.2 `7f0f8453` 已提交；3b.3/3b.4 核对记录已完成 `405ca977`，其余 wiring 结构收敛仍需独立方案。后续 RSI host 和 consumer 注入入口已另批收敛。
- **目标**：将 RSI 领域逻辑从 interfaces 下沉到独立 `apps/evolution`；不并入 `apps/goal`。
- **建议步骤**：
  1. 实施清单与引用/monkeypatch patch 点审计；
  2. 迁移预算闸与 `BaselinePolicy` 等可测策略，先保住现有行为并补模块内测试；
  3. 迁移 `run_once` 到 `apps/evolution`；
  4. 让 `interfaces/rsi_wiring.py` 仅负责装配，CLI 保持薄入口；
  5. 更新生产装配测试与架构层级清单。
- **主要涉及**：`interfaces/rsi_cli.py`、`interfaces/rsi_wiring.py`、新增 `apps/evolution`、RSI 相关测试与 `scripts/arch/layer_matrix.py`。
- **前置**：独立 R-014 实施清单；重核 monkeypatch 目标、外部命令/调用面与 halt callable 注入方式。
- **验收**：策略测试、RSI 生产接线测试、受影响 CLI 测试、层级门禁通过；外部 CLI 行为不变。
- **回滚**：3b.1、3b.2、3b.3/4 分批回滚；不可把 RSI 全量迁移压成单一提交。
- **执行记录**：[WP-8 evolution 迁移清单](./wp8-evolution-migration-checklist-20261120.md)；3b.2 RSI 面 6 文件定向测试记录为 `137 passed`；3b.3/3b.4 核对结论已记录，结构收敛另案。

### WP-9 · 三前端一核心批 3c：杂项归拢（七个独立小批）

- **状态**：M3 `d1e92e7e`、M5 `cf03f038`、M7 `4a5215a7`、M8 `e5040f7d`、M9-1/2 `82835dca`、M9-3/4 `2c8b1a20`、M11a `134224e9`、M11b `4a1a74f9` 已完成；M6 尚未开始。
- **单项目标**：
  - **M3**：收敛 session 双渲染职责。
  - **M5**：下沉 goal 判据/审计逻辑，审查动态 import。
  - **M6**：收敛 onboarding/provider 与路径职责（未开始）。
  - **M7**：统一 swarm preset 查找。
  - **M8**：统一 skills 入口。
  - **M9**：收敛 dotenv/LLM 路径与 IO；1/2、3/4 均已提交。
  - **M11**：收敛 web-session 依赖方向；M11a 鉴权原语上移、M11b `_fetch_session_owned` 过渡 re-export 清理均已提交。
- **前置**：每项单独出实施清单和引用/出口测试；确认目标模块与 interface/seam 后再动代码。
- **可并行边界**：M3、M5、M7、M8 在文件所有权确认后可独立分派；M6 与 M9 共享 config/path 热区，先串行或明确文件所有权；M11 与 M3 都可能触碰 session 文件，需串行或严格划分。
- **验收**：每个小批单独跑直接受影响测试、monkeypatch 锚点审计和 batch_gate；任何层级搬迁更新层级登记；最终阶段执行全量 pytest。
- **回滚**：每个 M 项一个提交；已完成批次各自可回滚；M11b 删除已有独立提交，不代表允许扩大到其他兼容删除批。

### WP-10 · Chat 3a.4 过渡面清理

- **状态**：标记已做；维护者裁决删除延期到下一版本、至少一个完整正式版本周期后另批。
- **目标**：将仍需维护的 router 过渡面移至 `send_turn` seam，并缩小/删除已批准的 deprecated helper；不改变现有 legacy API 删除审批边界。
- **前置**：单独 R-014 清理方案与用户批准；确认正式版本周期与候选清单。
- **验收要求**：按 R-015 对每项重查四项删除条件；把 58 个 patch/test 锚点迁到 `send_turn` seam；审计 router 过渡面；补齐 `entry_rejected` 的 400 与 legacy `send_message` 两项断言；R-016 穷举所有出口。
- **验证**：主体迁移先全量 pytest + batch_gate；删除作为末位独立提交，删除后重跑全量 pytest + batch_gate；架构图同步。
- **回滚**：迁移主体和删除分两个提交，删除回滚不撤销已完成的 seam 迁移。

### WP-11 · Legacy Chat API 兼容出口退役

- **状态**：维护者同意最终退役方向但要求延后；至少一个完整正式版本周期后复核仓库外消费者并另批，**当前未授权删除**。
- **目标**：在完整正式版本周期结束后，重新检查仓库外消费者，再单独移除已弃用 Chat 兼容出口。
- **范围**：9 个 `SessionService` exits 与 `_CHAT_ATTEMPT_TIMEOUT` re-export（最终以执行时清单为准）。
- **前置**：至少一个完整正式版本周期；外部消费者自查；单独 R-014 删除方案和明确批准。
- **验收**：R-015 四项逐出口审计、R-016 正常/异常/提前返回覆盖、R-020 主体/删除分提交；迁移指南、CHANGELOG 与兼容复审记录同步。
- **回滚**：兼容出口删除单独提交，可单独恢复；不得把它与 3a.4 或 seq_generator 混在一起。

### WP-12 · `seq_generator` 退役

- **状态**：已有静态审计证据表明生产消费者为 0；仍属退役候选，没有删除授权。
- **目标**：在短公告和独立 R-014 批准后，更新所有配套锚点并独立退役。
- **需同步的四类触点**：
  1. `web_session.py` 与 `kernel/session/models.py` 中的陈旧注释；
  2. `tests/test_seq_generator.py` 与 `tests/baselines/pr-lane-nodeids.json`；
  3. `scripts/arch/layer_matrix.py` 及其测试 baseline；
  4. `docs/architecture-diagram.html`。
- **前置**：重新验证生产与仓库外引用；R-014 批准；R-015/R-016 删除审查。
- **验收**：四类触点一致更新；相关测试/图表/门禁通过；删除本身独立提交。
- **并行**：批准后可以把互不重叠的触点更新分派给不同执行者，但由单一集成者统一检查与提交；不要与 Chat 删除批合并。
- **回滚**：一个退役提交完整包含实现和配套锚点，必要时整批恢复。

### WP-13 · 最终架构复核与文档收口

- **状态**：待以上已批准批次完成后执行。
- **目标**：重新测量四层方向、SCC、语义归属、测试出口与文档图表，报告实际改变量，不沿用旧数字。
- **范围**：架构检查脚本、架构图、框架原则与本路线图的状态列；不借机扩展重构范围。
- **验收**：结构门禁全绿；架构图与代码一致；所有临时 seam、弃用项和未完成批次均有明确状态/owner；更新复审文档。
- **回滚**：只回滚不正确的文档/数字同步，不回滚已独立验收的功能批。

## 4. 推荐分派与合并顺序

### 4.1 当前阶段：B2.2–B2.5 已完成后的收口

1. B2.2–B2.5 均已完成；B2.4 生产隔离路径仍默认关闭且 allowlist 为空，B2.5 的真实 write 仅是测试/脚本级试点。
2. I1 E5 declared 单点解析、I2 `IsolationChannel.read()` 显式结果契约、I3 父侧 ctx 校验均已实施；I2 的 R-014 执行记录列出全量 8062 passed、batch_gate ALL GREEN。
3. 已完成 S2 会话删除生命周期归一（`61676773`），Web/CLI 共用归档与投影失效；官方架构实测为 464/97,025。
4. WP-8 3b.3/3b.4 的核对完成，剩余 wiring 结构收敛另案；WP-9 M6 尚待安排，M11a/b 已完成。
5. M13 重启/持久化终态语义仍需另裁决；Chat 3a.4、Legacy Chat 和 `seq_generator` 仍受兼容周期/独立审批约束。
6. 当前后续调查聚焦 LLM 重试编排（普通请求/stream × sync/async），本轮只读，不授权重构；后续任何代码改动仍按 R-007 执行全量 pytest、batch_gate 和架构数字校验。

### 4.2 第二阶段：并行但文件所有权明确

WP-7 M13 可以与 WP-8 evolution 或经确认互不重叠的 WP-9 小批并行。每个 Agent 接收明确的目标、可编辑文件白名单、禁止触碰清单和独立验收命令。M6/M9、M3/M11 按 §3 串行化。

### 4.3 第三阶段：兼容期结束后的删除批

WP-10、WP-11、WP-12 均须满足各自的前置审批。可提前准备只读审计或迁移测试，但不能提前执行删除。合并顺序建议：

1. 先完成非删除迁移主体并全绿；
2. 单独批准并提交 WP-10 删除；
3. Legacy Chat API 在兼容周期结束且另获批准后单独提交；
4. `seq_generator` 独立提交；不得与两个 Chat 删除批合并。

### 4.4 并行 Agent 的任务说明模板

每个委派任务必须包含：

- 唯一目标与完成边界；明确是只读调研还是允许实施；
- 基线 commit 和其所依赖的前置批次；
- 可编辑文件白名单、禁止触碰文件/热区；
- 需要保留的行为契约和测试锚点；
- 先跑的聚焦测试及最终门禁；
- 验收证据、剩余风险、回滚粒度；
- 禁止顺手重构、删除或改变无关文件。

同一文件同一时刻只有一个 owner。Agent 完成后由集成者检查 diff 和测试结果，再合并到下一批；不要让多个 Agent 各自修改后再靠冲突解决器猜语义。

## 5. 全批次质量门

任何 `src/` 或 `tests/` 变更须按 `.rules/rules.md` 的 R-007：

1. 改动后先跑受影响的聚焦测试；
2. 提交前全量测试：`C:\Users\1\AppData\Local\Programs\Python\Python313\python.exe -m pytest tests -q -p no:hypothesispytest -p no:hypothesis -n 12 --dist loadgroup`。若 worker 启动/挂死，先按规则检查可用内存并逐档降并发，不得把环境问题改成永久低并发配置；
3. `scripts\batch_gate.cmd` 全绿（含层级方向、路径字面量、能力/语义放置、工具引用、kernel 名词、环境变量等检查）；
4. 源文件数量/行数变化时执行 `D:\tools\python312\python.exe scripts\arch\sync_architecture_diagram.py` 检查，并确保 `tests/test_scripts_arch.py::test_architecture_diagram_matches_code` 通过；
5. 更新直接相关设计文档与执行记录。

涉及删除的批次还必须满足 R-014、R-015、R-016、R-020：覆盖所有出口；删除排在主体迁移最后；删除前后各跑全量 pytest 与 batch_gate；主体与删除分别提交。

> 以上是项目规则要求的最终批验收。可并行开发期间先跑局部测试；不能把局部测试结果误报为提交前全量验收。

## 6. 裁决记录与仍需单独批准的事项

### 6.1 维护者已裁决（2026-11-20）

| # | 裁决 | 用户决定 | 落地/当前状态 |
|---|---|---|---|
| 1 | 隔离 seam 所有权 | 接受 L3 `kernel/agent/tools/` 归属 | 已由 `04a1255c` 完成迁移；B2.4 后有 guarded 生产接线，但开关默认关闭且 allowlist 为空。 |
| 2 | B2.2 3d 平台矩阵 | 批准 | 已由 `7171642e` 完成；POSIX 守卫进 CI，Windows CI 缺失已明示。 |
| 3 | B2.3/B2.4/B2.5 阶段治理 | 批准每阶段独立方案、验收与审批 | B2.3 3a–3c、B2.4 4a–4d、B2.5 5a–5c 均已完成；开关关闭/allowlist 为空，shell 未接。 |
| 4 | B2.5 工具范围 | 临时工作区 `file_write` 可以；shell 延后 | write 仅测试/脚本试点完成；零生产消费者，不扩大到 shell。 |
| 5 | RSI 归属 | 新建 `apps/evolution` | 3b.1/3b.2 已完成；3b.3/3b.4 核对完成，wiring 结构收敛另案。 |
| 6 | 应用批 3c | 七项拆分小批，按热区安排 | M3/M5/M7/M8、M9-1/2/3/4、M11a/b 已完成；M6 待做。 |
| 7 | Chat 3a.4 清理/删除 | 延至下一版本，至少一个完整正式版本周期后另批 | 当前保留 deprecated 标记；尚未授权删除。 |
| 8 | Legacy Chat 兼容出口 | 同意最终退役方向，但要求延后 | 至少一个完整正式版本周期后重新核仓库外消费者，并另走 R-014；当前不删。 |

### 6.2 仍需单独裁决

**已完成专项 R-014**：I1 E5 declared capability 单点解析、I2 `IsolationChannel.read()` 返回契约、I3 父侧 ctx 校验均已有执行记录；I2 最近记录全量测试 8062 passed、batch_gate ALL GREEN。

| 事项 | 当前建议 | 未裁决时的处理 |
|---|---|---|
| M13 重启/持久化终态 run | 已知内存终态保留且 approve=409 已锁；重启后 store 有记录但 registry 为空时的 404/409 需另定。 | 不加 TTL/prune，不改现有 404/409。 |
| `seq_generator` 退役 | 作为独立候选准备公告与 R-014；不能并入 Chat 删除。 | 保留代码，不删除。 |
| WP-8 / WP-9 剩余结构批 | WP-8 wiring 结构收敛另案；WP-9 M6 尚待做。M11a/b 已完成。 | 确认文件 owner 和实施方案后再安排；不得扩大到冻结删除批。 |

## 7. 本计划的完成定义

本文件记录的是“可讨论、可分派、可验收”的路线图。进入每个代码阶段前，记录该阶段 owner 并满足对应 R-014、验收、回滚和门禁；已经批准一个阶段不自动授权其后阶段。任何新发现若改变层级归属、外部契约或删除范围，应暂停该包并补充 R-014 方案，而不是在执行中扩大范围。
