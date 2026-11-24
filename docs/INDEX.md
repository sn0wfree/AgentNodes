# 文档索引

> **读哪份、信哪份，看这一页。**
> 2026-11-05 重组为三层：`guides/`（照做就对）· `design/`（决策仍生效）· `archive/`（历史记录）。
> 根目录只留 `INDEX.md`、`REFERENCE.md` 和架构图。

---

## 0. 从这里开始

| 你想做什么 | 读这份 |
|---|---|
| 了解项目是什么、快速跑起来 | [`../README.md`](../README.md) |
| 查命令 / MCP 工具 / swarm preset / 环境变量 | [`REFERENCE.md`](REFERENCE.md) |
| 查提示词基因组研究专题（调研→设计→语言→决策队列） | [`prompt-genome/INDEX.md`](prompt-genome/INDEX.md) |
| 看架构长什么样（含实测数字） | [`architecture-diagram.html`](architecture-diagram.html) |
| **理解框架理念与当前状态** | [`框架与理念 · v1.2.html`](框架与理念 · v1.2.html)（可读版）· [`design/framework-and-principles-v1.2.md`](design/framework-and-principles-v1.2.md)（md 底稿）|
| 理解四层引擎为什么这样分 | [`design/layered-skeleton-design-20261103.md`](design/layered-skeleton-design-20261103.md) |
| 知道现在拆到哪一步、接下来删什么 | [`design/subtraction-roadmap-v2-20261105.md`](design/subtraction-roadmap-v2-20261105.md) |
| 跑测试 / 门禁 / 分片 | [`guides/test-marker-lanes-design.md`](guides/test-marker-lanes-design.md) |
| 改代码前确认没有已知坑 | [`guides/known-test-defects-20261104.md`](guides/known-test-defects-20261104.md) |

---

## 1. guides/ — 现行操作手册（14 份）

**判据**：现在照着做就是对的。内容与代码同步维护。

| 文档 | 用途 |
|---|---|
| [`code-map.md`](guides/code-map.md) | 代码地图：哪个目录放什么 |
| [`agent-tools-reference.md`](guides/agent-tools-reference.md) | Agent 工具手册 |
| [`extension-points.md`](guides/extension-points.md) | 扩展点放哪（7 机制分工 + 决策树） |
| [`toolset-convention.md`](guides/toolset-convention.md) | 工具集命名与组织约定 |
| [`llm-provider-setup.md`](guides/llm-provider-setup.md) | LLM 供应商配置 |
| [`model-catalog-design.md`](guides/model-catalog-design.md) | 模型目录（ModelHub 解析入口） |
| [`logging-unified-entry-design-20261102.md`](guides/logging-unified-entry-design-20261102.md) | 日志统一入口（`AN_LOG_*` 语义） |
| [`test-marker-lanes-design.md`](guides/test-marker-lanes-design.md) | 测试 lane（llm/e2e/slow）+ 本地验证矩阵 |
| [`ci-sharding-strategy.md`](guides/ci-sharding-strategy.md) | CI 分片（LPT + flaky 治理） |
| [`known-test-defects-20261104.md`](guides/known-test-defects-20261104.md) | 已知测试缺陷（与减法正交的既存问题） |
| [`xfail-tests.md`](guides/xfail-tests.md) | xfail 清单与理由 |
| [`legacy-sessiondb-tech-debt.md`](guides/legacy-sessiondb-tech-debt.md) | 历史 SessionDB 技术债 |
| [`legacy-chat-api-migration.md`](guides/legacy-chat-api-migration.md) | legacy chat API 迁移对照表（旧出口 → 新入口） |
| [`architecture-diagram-changelog.md`](guides/architecture-diagram-changelog.md) | 架构图版本史（v5.1 → 当前） |

---

## 2. design/ — 仍生效的设计与契约（173 份，2026-11-24 补录）

**判据**：文档里的决策仍在代码中执行。已被取代的移到 `archive/`。

### 2.1 分层与拆分（当前主线）

| 文档 | 内容 |
|---|---|
| [`layered-skeleton-design-20261103.md`](design/layered-skeleton-design-20261103.md) | 四层引擎契约（层归属表、方向规则、机器门禁） |
| [`physical-layer-migration-plan-20261103.md`](design/physical-layer-migration-plan-20261103.md) | `core/` → 四层目录的迁移协议 |
| [`structure-review-graphify-20261104.md`](design/structure-review-graphify-20261104.md) | 结构评审（graphify 第二种量纲） |
| [`structure-fix-w10-design-20261104.md`](design/structure-fix-w10-design-20261104.md) | W10 结构修复 |
| [`target-directory-structure-20261026.md`](design/target-directory-structure-20261026.md) | 目标目录结构 |
| [`agentnodes-split-plan-20261104.md`](design/agentnodes-split-plan-20261104.md) | **拆分总纲**：边界定义（哪些归框架/业务） |
| [`subtraction-roadmap-v2-20261105.md`](design/subtraction-roadmap-v2-20261105.md) | **减法路线图**：批次 4–9 的执行顺序与终态预估 |
| [`batch3-study-removal-design-20261105.md`](design/batch3-study-removal-design-20261105.md) | 批次 3 设计（study 移除 + Ticket 提取到 L3） |
| [`seam-contract-platform-research-20261030.md`](design/seam-contract-platform-research-20261030.md) | 接缝契约（方向规则 / 触点白名单） |
| [`seam-hardening-design-20261030.md`](design/seam-hardening-design-20261030.md) | 接缝加固设计 |
| [`orchestration-layer-membership.md`](design/orchestration-layer-membership.md) | 编排层成员归属 |
| [`orchestration-triad-design.md`](design/orchestration-triad-design.md) | 编排三元组（Gateway / SessionController / Executor） |
| [`entry-controller-contract.md`](design/entry-controller-contract.md) | 入口-控制器契约 |

### 2.2 执行引擎

| 文档 | 内容 |
|---|---|
| [`unified-agent-engine-design.md`](design/unified-agent-engine-design.md) | 统一执行引擎 |
| [`deep-unification-graph-engine-design-20261031.md`](design/deep-unification-graph-engine-design-20261031.md) | Swarm 编译器化 → GraphEngine |
| [`workflow-module-design.md`](design/workflow-module-design.md) | Workflow 模块（编译器 + resume） |
| [`workflow-design.md`](design/workflow-design.md) | Workflow 层设计 |
| [`p1-1-loop-strategy-abstraction.md`](design/p1-1-loop-strategy-abstraction.md) | Loop 策略抽象 |
| [`p1-2-3-4-strategies.md`](design/p1-2-3-4-strategies.md) | 四种 Loop 策略 |

### 2.3 事件与数据平面

| 文档 | 内容 |
|---|---|
| [`p0-1-event-sourcing-design.md`](design/p0-1-event-sourcing-design.md) | 事件溯源（emit/subscribe/replay） |
| [`p0-1-a4-event-log-migration.md`](design/p0-1-a4-event-log-migration.md) | event_log 迁移 |
| [`s1-event-protocol-unification-design-20261027.md`](design/s1-event-protocol-unification-design-20261027.md) | 事件协议统一（PROTOCOL_VERSION） |
| [`projector-incremental.md`](design/projector-incremental.md) | 增量投影器 |
| [`phase2-web-session-projector-design.md`](design/phase2-web-session-projector-design.md) | Web session 投影 |
| [`p0-2-capability-seams.md`](design/p0-2-capability-seams.md) | 能力接缝 |
| [`p5-1-chat-gateway-parity-design-20261027.md`](design/p5-1-chat-gateway-parity-design-20261027.md) | Chat ↔ Gateway 一致性 |
| [`p5-1-v2-chat-persistence-design-20261027.md`](design/p5-1-v2-chat-persistence-design-20261027.md) | Chat 持久化 v2 |
| [`p5-1b-streaming-tools-design-20261027.md`](design/p5-1b-streaming-tools-design-20261027.md) | 流式工具调用 |
| [`history-sessions-design.md`](design/history-sessions-design.md) | 历史会话 |

### 2.4 上下文与压缩

| 文档 | 内容 |
|---|---|
| [`context-hub-discussion.md`](design/context-hub-discussion.md) | ContextHub 三条硬约束 |
| [`context-overflow-fix.md`](design/context-overflow-fix.md) | 上下文溢出修复 |
| [`compact-design.md`](design/compact-design.md) | 压缩机制 |
| [`compaction-final-fix.md`](design/compaction-final-fix.md) | 压缩最终修复 |
| [`compaction-history-filter.md`](design/compaction-history-filter.md) | 压缩历史过滤 |

### 2.5 交互面

| 文档 | 内容 |
|---|---|
| [`chat-service-design.md`](design/chat-service-design.md) | Chat 服务 |
| [`chat-message-queue-design.md`](design/chat-message-queue-design.md) | 消息队列 |
| [`web-ui-design.md`](design/web-ui-design.md) | Web UI（React + FastAPI） |
| [`tui-final-design.md`](design/tui-final-design.md) | TUI 最终设计 |
| [`tui-redesign-plan.md`](design/tui-redesign-plan.md) | TUI 重设计 |
| [`tui-display-philosophy.md`](design/tui-display-philosophy.md) | TUI 显示哲学 |
| [`right-panel-agent-driven.md`](design/right-panel-agent-driven.md) | 右侧面板 agent 驱动 |
| [`ui-workbench-design-20261115.md`](design/ui-workbench-design-20261115.md) | **UI 轨道主文档**：变迁链 + 8 条设计精髓 + 三表面蓝图 + §11 Grok Bot 对照（待 §6 确认） |
| [`ui-a-token-system-design-20261114.md`](design/ui-a-token-system-design-20261114.md) | U-A 令牌体系：中性化转向 + L1/L2/L3 + alpha 表/specular/刻度表 |
| [`ui-frontend-unit-test-plan-20261116.md`](design/ui-frontend-unit-test-plan-20261116.md) | 前端单元测试计划（R-014）+ §5.5–§5.8 T1–T4 实测回填（122 文件 / 1323 用例 / 89.60%） |
| [`ui-zcode-source-research-20261114.md`](ui-zcode-source-research-20261114.md) | ZCode UI 源码级逆向（§1-§10，U-A/U-B/U-D/U-E 依据） |
| [`ui-grok-bot-source-research-20261116.md`](ui-grok-bot-source-research-20261116.md) | Grok Bot UI 源码级逆向（双 alpha 表/玻璃可关层/刻度/gradient 三条件判定） |

### 2.6 其他契约

| 文档 | 内容 |
|---|---|
| [`system-one-survey-20261109.md`](design/system-one-survey-20261109.md) | System One 调研（Kev 接口事实 + 决策点普查） |
| [`system-one-decision-layer-design-20261109.md`](design/system-one-decision-layer-design-20261109.md) | System One 决策层接入（讨论稿，8 个开放问题） |
| [`typesafe-langchain-integration-survey-20261110.md`](design/typesafe-langchain-integration-survey-20261110.md) | LangChain TypeSafe 集成包调研（0.0.2 产物实测 + 与本项目交叉） |
| [`crewai-survey-20261116.md`](design/crewai-survey-20261116.md) | crewAI 调研（60k Star 微信文核查 + Crews/Flows 事实 + 与本项目对照） |
| [`crewai-design-borrow-20261116.md`](design/crewai-design-borrow-20261116.md) | crewAI 可借鉴项取舍（P0 节点产物契约；7 个开放问题） |
| [`plug-in-contract-design.md`](design/plug-in-contract-design.md) | 插件契约 |
| [`truthfulness-common-rules-design.md`](design/truthfulness-common-rules-design.md) | 真实性通用规则 |
| [`phase3-dual-factory-design.md`](design/phase3-dual-factory-design.md) | 双工厂 |
| [`phase5-guardrails-design.md`](design/phase5-guardrails-design.md) | 护栏 |

### 2.7 项目进度与复盘

| 文档 | 内容 |
|---|---|
| [`project-progress-assessment-20261116.md`](design/project-progress-assessment-20261116.md) | **项目进度评估（复盘）**：四条工作线完成度 + 健康度实测 + 13 项遗留清单 + 处置建议（待批准）|
| [`merge-main-and-framework-v12-plan-20261116.md`](design/merge-main-and-framework-v12-plan-20261116.md) | **合并 main + 框架与理念 v1.2 方案（R-014）**：FF 合并可行性 + 架构变化盘点 + v1.2 十一节设计与验收判据（待批准）|
| [`framework-and-principles-v1.2.md`](design/framework-and-principles-v1.2.md) | **框架与理念 v1.2（现行）**：七理念检查栏回填 + 双轴总图 + **§4 MVP 三层能力集（C1–C28）** + W/R 完成度 + §6 变化总账；体量数字由 `check_framework_numbers.py` 钉住 |
| [`session-lifecycle-kernel-migration-20261116.md`](design/session-lifecycle-kernel-migration-20261116.md) | **会话生命周期下沉 kernel（方案，R-014 · 未实施）**：14 文件 / 5,774 行迁移，六批次 A–F + 回滚方式 + 验收判据；含 K1–K4 四道硬约束 |
| [`framework-and-principles-20261105.md`](design/framework-and-principles-20261105.md) | 框架与理念 v1.1（历史版，保留对照）——**已被 v1.2 取代** |

### 2.8 ADR

| 文档 | 决策 |
|---|---|
| [`adr/ADR-000-baseline-recount.md`](design/adr/ADR-000-baseline-recount.md) | 基线口径：每条 import 语句计 1 |
| [`adr/ADR-002-l1-adapter-policy.md`](design/adr/ADR-002-l1-adapter-policy.md) | L1 适配器策略 |
| [`adr/ADR-003-l3-l2-disposition.md`](design/adr/ADR-003-l3-l2-disposition.md) | L3→L2 处置（CapabilityRegistry 端口化） |

### 2.9 架构收敛与复审执行记录（2026-11-06 → 11-22 · 95 份）

**判据**：架构收敛四阶段（减法批次收口 → loop 统一 → L1 三前端一核心 → 隔离执行/L1/会话深 化四轮复审）的方案与执行记录。全部已实施并留痕；上行违规 0 由门禁持续钉住。主线入口：[`architecture-refactor-execution-roadmap-20261119.md`](design/architecture-refactor-execution-roadmap-20261119.md)。

#### A. 主线与复审轮次

| 文档 | 内容 |
|---|---|
| [`architecture-refactor-execution-roadmap-20261119.md`](design/architecture-refactor-execution-roadmap-20261119.md) | **架构收敛执行路线图（主线）**：WP 批次与验收 |
| [`project-architecture-review-and-refactor-plan-20261118.md`](design/project-architecture-review-and-refactor-plan-20261118.md) | 架构评审与重构总计划 |
| [`architecture-review-20261120-round2-dispositions.md`](design/architecture-review-20261120-round2-dispositions.md) | 第二轮复审候选处置记录（#6 冻结裁决） |
| [`round4-c1-c2-c3-r014-20261121.md`](design/round4-c1-c2-c3-r014-20261121.md) | 第四轮 C1-C3 方案+执行记录（事件翻译/窄 port/构造归一） |
| [`round4-c4-c8-r014-20261121.md`](design/round4-c4-c8-r014-20261121.md) | 第四轮 C4-C8 方案+执行记录（写回契约/ensure_sink/session_keys 等） |
| [`review-144502-crosscheck-20261119.md`](design/review-144502-crosscheck-20261119.md) | 复审交叉核对 |
| [`sequence6-audit-20261119.md`](design/sequence6-audit-20261119.md) | sequence6 审计 |
| [`m13-workflow-registry-review-20261119.md`](design/m13-workflow-registry-review-20261119.md) | workflow registry 审查 |
| [`pending-approvals-20261119.md`](design/pending-approvals-20261119.md) | 待批准清单（旧出口删除批，时间门槛约束） |
| [`legacy-chat-api-compat-review-20261118.md`](design/legacy-chat-api-compat-review-20261118.md) | legacy chat 兼容面审查（先批审后批删） |

#### B. 隔离执行链（B2.2 → B2.5 + 深化）

| 文档 | 内容 |
|---|---|
| [`isolated-execution-b22-design-20261119.md`](design/isolated-execution-b22-design-20261119.md) | 隔离执行 B2 总设计（seam/通道/平台守卫） |
| [`isolated-executor-deepening-r014-20261120.md`](design/isolated-executor-deepening-r014-20261120.md) | 隔离执行器深化（事件单源/编排下沉 IsolatedToolExecutor） |
| [`isolated-executor-entry-convergence-r014-20261120.md`](design/isolated-executor-entry-convergence-r014-20261120.md) | 入口收敛（transient 复用 IsolationWorker） |
| [`admission-module-extraction-r014-20261120.md`](design/admission-module-extraction-r014-20261120.md) | IsolationAdmission 深模块抽取（准入纯函数化） |
| [`b23-context-crossing-r014-20261120.md`](design/b23-context-crossing-r014-20261120.md) | 上下文跨界 codec（ctx_v 白名单 + probe） |
| [`b24-production-wiring-r014-20261120.md`](design/b24-production-wiring-r014-20261120.md) | 生产接线（per-call id / deadline/lease / E5 重注入） |
| [`b25-tool-pilot-r014-20261120.md`](design/b25-tool-pilot-r014-20261120.md) | 真实工具试点 S5 + 准入 C1-C3 复核 |
| [`ctx-parent-validation-r014-20261120.md`](design/ctx-parent-validation-r014-20261120.md) | 父侧 ctx 校验（validate_wire + 公开入口把关） |
| [`declared-capability-single-source-r014-20261120.md`](design/declared-capability-single-source-r014-20261120.md) | E5 declared 单点解析（declared_capability） |
| [`channel-read-contract-r014-20261120.md`](design/channel-read-contract-r014-20261120.md) | 通道读契约（ReadResult 三 kind + stderr_tail） |
| [`executor-host-seam-r014-20261120.md`](design/executor-host-seam-r014-20261120.md) | _ExecHost 宿主适配器（4 能力面） |
| [`wp8-evolution-migration-checklist-20261120.md`](design/wp8-evolution-migration-checklist-20261120.md) | WP-8 evolution 迁移核对清单 |

#### C. L1 三前端一核心 + 会话生命周期

| 文档 | 内容 |
|---|---|
| [`l1-three-frontends-one-core-design-20261119.md`](design/l1-three-frontends-one-core-design-20261119.md) | L1 三前端一核心总设计（M 系列） |
| [`composition-root-convergence-20261119.md`](design/composition-root-convergence-20261119.md) | 组合根收敛（M12） |
| [`chat-send-turn-baseline-3a0-20261119.md`](design/chat-send-turn-baseline-3a0-20261119.md) | send_turn 基线（3a.0 冻结） |
| [`chat-send-turn-usecase-extraction-20261119.md`](design/chat-send-turn-usecase-extraction-20261119.md) | send_turn 用例抽取（3a.3） |
| [`session-deletion-lifecycle-r014-20261120.md`](design/session-deletion-lifecycle-r014-20261120.md) | 删除生命周期归一（归档+投影失效单点） |
| [`session-shim-test-surface-r014-20261120.md`](design/session-shim-test-surface-r014-20261120.md) | session 垫片测试面（kernel.session 直测） |
| [`router-service-seam-r014-20261120.md`](design/router-service-seam-r014-20261120.md) | router 服务取用 seam（session_service_from） |
| [`available-actions-single-policy-r014-20261120.md`](design/available-actions-single-policy-r014-20261120.md) | 可用动作矩阵单策略（含 goal lease 抑制） |

#### D. goal / RSI 深化

| 文档 | 内容 |
|---|---|
| [`goal-coverage-unification-r014-20261120.md`](design/goal-coverage-unification-r014-20261120.md) | 覆盖判定单源（all_criteria_covered/rows_for） |
| [`goal-completion-deep-operation-r014-20261120.md`](design/goal-completion-deep-operation-r014-20261120.md) | 完成编排归一（CompletionContext 单参 + complete_goal） |
| [`goal-hook-runner-interface-r014-20261120.md`](design/goal-hook-runner-interface-r014-20261120.md) | hook/runner 公开面 + from_snapshot |
| [`consumer-injection-port-r014-20261120.md`](design/consumer-injection-port-r014-20261120.md) | 消费者注入口归一（删 run_cycle 第二 seam） |
| [`strong-candidates-g1-s1-r014-20261120.md`](design/strong-candidates-g1-s1-r014-20261120.md) | G1 emit 显式 port + S1 归属校验深 helper |
| [`gateway-timeout-and-strict-history-design-20261118.md`](design/gateway-timeout-and-strict-history-design-20261118.md) | Gateway 超时与严格历史（E5 契约） |
| [`goal-gateway-session-lease-design-20261118.md`](design/goal-gateway-session-lease-design-20261118.md) | goal ↔ gateway 执行租约 |

#### E. loop 统一与执行引擎（2026-11-07 → 11-13）

| 文档 | 内容 |
|---|---|
| [`loop-unification-design-20261111.md`](design/loop-unification-design-20261111.md) | loop 统一总设计 |
| [`loop-engine-inventory-20261111.md`](design/loop-engine-inventory-20261111.md) | 双引擎盘点 |
| [`loop-engine-industry-survey-20261111.md`](design/loop-engine-industry-survey-20261111.md) | 引擎业界调研 |
| [`lobehub-agent-loop-survey-20261112.md`](design/lobehub-agent-loop-survey-20261112.md) | LobeHub agent loop 调研 |
| [`loop-final-design-20261112.md`](design/loop-final-design-20261112.md) | loop 终版设计 |
| [`step-protocol-migration-20261112.md`](design/step-protocol-migration-20261112.md) | step 协议迁移 |
| [`decider-crash-isolation-20261113.md`](design/decider-crash-isolation-20261113.md) | decider 崩溃隔离 |
| [`ctx-hash-id-design-20261112.md`](design/ctx-hash-id-design-20261112.md) | ctx 指纹（U6） |
| [`langgraph-implicit-dep-fix-20261113.md`](design/langgraph-implicit-dep-fix-20261113.md) | langgraph 隐式依赖修复 |
| [`unified-loop-adoption-20261107.md`](design/unified-loop-adoption-20261107.md) | 统一 loop 采纳（B3） |
| [`unified-loop-adoption-eval-20261107.md`](design/unified-loop-adoption-eval-20261107.md) | 采纳评估 |
| [`v2-live-llm-e2e-20261109.md`](design/v2-live-llm-e2e-20261109.md) | v2 真 LLM E2E |
| [`v3-path-convergence-decision-20261109.md`](design/v3-path-convergence-decision-20261109.md) | v3 路径收敛决策 |

#### F. 减法批次与阶段收口（2026-11-05 → 11-09）

| 文档 | 内容 |
|---|---|
| [`batch4-backtest-removal-design-20261105.md`](design/batch4-backtest-removal-design-20261105.md) | 批次 4（backtest 移除） |
| [`batch4d-kernel-noun-removal-20261106.md`](design/batch4d-kernel-noun-removal-20261106.md) | 批次 4d（kernel 名词清理） |
| [`batch5-data-domain-removal-design-20261105.md`](design/batch5-data-domain-removal-design-20261105.md) | 批次 5（数据域移除） |
| [`batch6-permission-hardening-20261107.md`](design/batch6-permission-hardening-20261107.md) | 批次 6（permission 加固） |
| [`batch7-capability-enable-20261107.md`](design/batch7-capability-enable-20261107.md) | 批次 7（capability enable + 技能归档） |
| [`batch9-b1-protocol-unification-20261107.md`](design/batch9-b1-protocol-unification-20261107.md) | 批次 9（B1 协议统一 + 重命名） |
| [`batch10-b2-deciders-20261107.md`](design/batch10-b2-deciders-20261107.md) | 批次 10（B2 deciders） |
| [`batch11-b3-skeleton-shadow-20261108.md`](design/batch11-b3-skeleton-shadow-20261108.md) | 批次 11（B3 skeleton shadow） |
| [`batch12-b3-stage2-subtraction-20261109.md`](design/batch12-b3-stage2-subtraction-20261109.md) | 批次 12（B3 stage2 减法） |
| [`batch13-appendix-same-class-audit-20261109.md`](design/batch13-appendix-same-class-audit-20261109.md) | 批次 13（同类审计） |
| [`batch13-b3-s3-mixin-dissolution-20261109.md`](design/batch13-b3-s3-mixin-dissolution-20261109.md) | 批次 13（B3-S3 mixin 解体） |
| [`b1-status-and-b2-plan-20261107.md`](design/b1-status-and-b2-plan-20261107.md) | B1 状态与 B2 计划 |
| [`goal-capability-merge-design-20261105.md`](design/goal-capability-merge-design-20261105.md) | goal capability 合并设计 |
| [`phase1-closure-report-20261107.md`](design/phase1-closure-report-20261107.md) | Phase 1 收口报告 |
| [`phase1-definition-of-done-20261107.md`](design/phase1-definition-of-done-20261107.md) | Phase 1 DoD |
| [`phase1-completion-verification-plan-20261109.md`](design/phase1-completion-verification-plan-20261109.md) | Phase 1 完成验证计划 |
| [`three-phase-roadmap-20261106.md`](design/three-phase-roadmap-20261106.md) | 三阶段路线图 |
| [`progress-and-plan-20261106.md`](design/progress-and-plan-20261106.md) | 进度与计划 |
| [`framework-review-20261106.md`](design/framework-review-20261106.md) | 框架评审（v1.1 后） |

#### G. 配置 / 结构治理与专项调研（2026-11-05 → 11-22）

| 文档 | 内容 |
|---|---|
| [`llm-config-dual-path-and-yaml-20261111.md`](design/llm-config-dual-path-and-yaml-20261111.md) | LLM 配置双路径 + YAML |
| [`llm-config-construction-convergence-20261116.md`](design/llm-config-construction-convergence-20261116.md) | LLM 配置构造收敛 |
| [`cli-an-prefix-20261111.md`](design/cli-an-prefix-20261111.md) | CLI `an` 前缀决策 |
| [`settings-declaration-hygiene-20261111.md`](design/settings-declaration-hygiene-20261111.md) | settings 声明卫生 |
| [`legacy-env-audit-command-20261110.md`](design/legacy-env-audit-command-20261110.md) | legacy 环境变量审计命令（E-2） |
| [`param-naming-unification-20261109.md`](design/param-naming-unification-20261109.md) | 参数命名统一 |
| [`data-dir-unification-20261109.md`](design/data-dir-unification-20261109.md) | 数据目录统一 |
| [`test-suite-health-and-speedup-20261109.md`](design/test-suite-health-and-speedup-20261109.md) | 测试套件健康与提速 |
| [`workflow-sqlite-lock-fix-20261114.md`](design/workflow-sqlite-lock-fix-20261114.md) | workflow SQLite 锁修复 |
| [`u6-2-terminal-cleanup-design-20261114.md`](design/u6-2-terminal-cleanup-design-20261114.md) | U6.2 终端清理 |
| [`prepare-algo-kernel-extract-20261114.md`](design/prepare-algo-kernel-extract-20261114.md) | algo kernel 抽取准备 |
| [`agent-dir-structuring-plan-20261117.md`](design/agent-dir-structuring-plan-20261117.md) | agent 目录结构规划 |
| [`builtin-tools-flatten-placement-survey-20261117.md`](design/builtin-tools-flatten-placement-survey-20261117.md) | builtin_tools 平铺选址调查 |
| [`absolute-imports-migration-20261117.md`](design/absolute-imports-migration-20261117.md) | 绝对 import 迁移 |
| [`arch-diagram-l3-clusters-v2-20261117.md`](design/arch-diagram-l3-clusters-v2-20261117.md) | L3 聚合架构图 v2 |
| [`mvp-tier-revision-20261117.md`](design/mvp-tier-revision-20261117.md) | MVP 归层修订 |
| [`service-coverage-plan-20261117.md`](design/service-coverage-plan-20261117.md) | SessionService 覆盖计划 |
| [`foundation-structure-review-20261117.md`](design/foundation-structure-review-20261117.md) | foundation 结构评审 |
| [`kernel-module-splitting-design-20261118.md`](design/kernel-module-splitting-design-20261118.md) | kernel 模块拆分评估（K5：validation_gate 不拆） |
| [`logging-unified-hub-design-20261117.md`](design/logging-unified-hub-design-20261117.md) | 日志统一 hub |
| [`asset-value-review-20261111.md`](design/asset-value-review-20261111.md) | 资产价值评审 |
| [`layer-attribution-review-20261105.md`](design/layer-attribution-review-20261105.md) | 层归属评审 |
| [`architecture-consistency-scan-20261105.md`](design/architecture-consistency-scan-20261105.md) | 架构一致性扫描 |
| [`industry-survey-knowledge-base-20261106.md`](design/industry-survey-knowledge-base-20261106.md) | 业界调研知识库 |
| [`ui-ua-reuse-assessment-20261122.md`](design/ui-ua-reuse-assessment-20261122.md) | U-A 复用评估（UI 轨道） |
| [`ui-ua-frontend-execution-plan-20261122.md`](design/ui-ua-frontend-execution-plan-20261122.md) | U-A 前端实现分步计划（会话 MVP → 完整前端 + 验收清单） |
| [`ux-side-panel-industry-survey-20261123.md`](design/ux-side-panel-industry-survey-20261123.md) | 侧边栏三栏设计业界调研（rail/会话栏/右面板 10 模式 + 映射建议） |
| [`p1-4-trace-field-mapping-20261123.md`](design/p1-4-trace-field-mapping-20261123.md) | P1-4 Trace 字段映射（示意字段→现有/可推导/缺失 + 脱敏/错误矩阵） |
| [`p2-2-rsi-readonly-contract-20261123.md`](design/p2-2-rsi-readonly-contract-20261123.md) | P2-2 RSI 只读契约骨架（引擎真/UI 契约零，无端点不建 UI） |
| [`p1-1-token-migration-plan-20261123.md`](design/p1-1-token-migration-plan-20261123.md) | P1-1 令牌迁移方案（示意29↔现网令牌映射/消费通道93:1/五批结构/设计内部12项未裁决策） |
| [`rename-entry-consolidation-20261123.md`](design/rename-entry-consolidation-20261123.md) | 重命名入口合并方案（顶栏/侧栏两实现合一，单一 TopBar 编辑 + 侧栏 hover 触发切换） |
| [`remove-session-tabs-20261123.md`](design/remove-session-tabs-20261123.md) | 移除顶栏 SessionTabs 方案（未采纳，保留作反方论证记录） |
| [`auto-title-and-remove-tab-plus-20261123.md`](design/auto-title-and-remove-tab-plus-20261123.md) | B+A 方案：首次发送自动标题（接回已有 auto_title_session + SSE patch）+ 删 tab 加号 |
| [`trace-panel-and-dag-hide-20261123.md`](design/trace-panel-and-dag-hide-20261123.md) | Trace 独立入口+权限（403 已存在）+ DAG 前端5入口撤销/API24路由保留方案（R-014 待批准） |
| [`sidebar-navigation-redesign-20261123.md`](design/sidebar-navigation-redesign-20261123.md) | 导航侧边框调整方案+实施——DAG 5入口撤销(路由/API保留)、Trace 独立入口(调研 ZCode/OpenClaw/OpenCode/Codex/Claude)、Agents 等 P1-P3 候选 |
| [`nav-rail-collapse-tooltip-20261123.md`](design/nav-rail-collapse-tooltip-20261123.md) | 图标栏两态折叠 + 统一 Tooltip 方案（R-014 待批准/零代码）——衔接侧栏改造，纯前端，键盘可达 |
| [`e2e-complete-build-and-repair-20261123.md`](design/e2e-complete-build-and-repair-20261123.md) | 完整 E2E 构建与问题修复方案（R-014 待批准/零代码）——E0 信任修复/E1 四 skip 转真断言/E2 路由 smoke/E3 业务流/E4 稳定性，14 路由覆盖差集与 6 孤儿基线清单 |
| [`model-picker-key-gating-20261124.md`](design/model-picker-key-gating-20261124.md) | Chat 模型下拉按密钥状态置灰方案（R-014 已批准）——key_configured=false 的模型 disabled+标注「未配置密钥」，修清除密钥后仍可选中导致发送失败；数据后端已给、仅改展示层 |
| [`context-usage-agent-survey-20261124.md`](design/context-usage-agent-survey-20261124.md) | 各 agent 上下文用量实现调研（零代码）——结论：生产 chat 无任何 token 事件下发(翻译层丢弃)、DAG/swarm 后端根本没算；9 条路径口径对照 + 17 项缺口清单 + 9 条后端修复建议(P0-a 事件透传等)+ 4 个待决策点 |
| [`composer-layout-industry-benchmark-20261124.md`](design/composer-layout-industry-benchmark-20261124.md) | 输入框布局对标调研（零代码）——ZCode 一手 bundle 源码直证：大圆角容器包住编辑器+工具条、左+更多/模式/后台、右用量/模型+推理档位/语音/圆形品牌蓝发送、排队消息编辑条、用量7段弹层、无常驻Markdown按钮；MimoCode/WorkBuddy 无公开布局资料(仅确认产品身份)；含 5 项建议(A 框内工具条/B 圆形发送按钮/C 分段用量依赖后端/D 编辑态/E 语音) |
| [`composer-layout-abcd-20261124.md`](design/composer-layout-abcd-20261124.md) | 输入框布局对标改造方案——A 状态行并入输入框容器(rounded-2xl 大卡片)、B 圆形品牌色发送按钮（用户已批准实施）；C 7段分段用量、D 排队消息编辑态待后端契约调研后细化 |
| [`color-audit-20261124.md`](design/color-audit-20261124.md) | 前端配色审查（手动操作+WCAG 量化，零代码）——暗色基本健康(7项边缘)、亮色系统性弱(53项低对比)；根因 emerald/violet/rose 语义色 60+ 处硬编码未令牌化；建议 B-3 语义令牌化 |
| [`semantic-color-unification-20261124.md`](design/semantic-color-unification-20261124.md) | 语义色统一方案（R-014 待批准/零代码）——令牌已存在(success/danger/warning/viz)仅缺 Tailwind 映射+60+处组件接线；C1补令牌映射/C2高频替换/C3全量清扫+门禁/C4验收；目标单点可调、亮色低对比53→≤10 |
| [`composer-toolbar-evaluation-20261124.md`](design/composer-toolbar-evaluation-20261124.md) | 输入框工具行评估（零代码）——6 类控件均真接后端契约；实测 4 个问题：Plan/Build 持久化失效(__srModeSession 零调用)、角色下拉混裸id+编排助手+顺序不稳、列表/引用行中插错、格式化无快捷键；业界对照：8 图标常驻过密(375 下 4 行)宜改选中浮出 |
| [`composer-toolbar-p0p1-fix-20261124.md`](design/composer-toolbar-p0p1-fix-20261124.md) | 输入框工具行 P0→P1 修复方案（R-014 待批准/零代码）——F0 mode 持久化(store 自持 sessionId 删 window 钩子)/F1 personas 过滤(11 项固定序,排除内部角色+编排助手)/F2 applyMarkdown 行首多行语义/F3 删 8 图标+补 Cmd+B/I/E 快捷键；含去图标的能力取舍说明；375 宽 4 行→2 行 |

---

## 3. prompt-genome/ — 提示词基因组研究专题（8 份）

**判据**：单一研究线（2026-11-17 起：业界调研 → 概念框架 → 规则基因组设计 → GRL 规则语言）的专属归档文件夹，**自带索引**——这是三层结构（guides/design/archive）之外的专题例外，入口统一走：

| 文档 | 内容 |
|---|---|
| [`prompt-genome/INDEX.md`](prompt-genome/INDEX.md) | **专题入口**：文档地图 + 阅读路径 + 决策队列（★ 六项）+ GRL↔设计 schema 接口表 |
| [`prompt-genome/提示词基因框架-业界先例调研-20261117.md`](prompt-genome/提示词基因框架-业界先例调研-20261117.md) | 业界先例地图（45 项先例 / 空白地图 / 框架草案） |
| [`prompt-genome/CL4R1T4S-项目评估-20261117.md`](prompt-genome/CL4R1T4S-项目评估-20261117.md) | 语料评估（判决：值得读 / 不值得整仓吃进管道） |
| [`prompt-genome/提示词与基因组-概念对映研究-20261117.md`](prompt-genome/提示词与基因组-概念对映研究-20261117.md) | 概念对映（R1–R6 / 18 概念判定 / 失效边界） |
| [`prompt-genome/rule-genome-design-20261117.md`](prompt-genome/rule-genome-design-20261117.md) | 规则基因组设计 v2（trace 绑定版 + §10 未决 22 项） |
| [`prompt-genome/rule-genome-synthesis-20261118.md`](prompt-genome/rule-genome-synthesis-20261118.md) | 汇总锚点（六轮收敛 + 拍板要点 + 下一步） |
| [`prompt-genome/GRL-基因规约语-20261118.md`](prompt-genome/GRL-基因规约语-20261118.md) | GRL 等位层规则语言（并行会话，5526 条款闭环自证） |
| [`prompt-genome/规则基因组体系 · 概念示意图 · 20261117.html`](prompt-genome/规则基因组体系 · 概念示意图 · 20261117.html) | ID 脊柱全链图（含未决热点标注） |

> GRL 工具包 zip 为未入库工作快照，不列目。

---

## 4. archive/ — 历史施工记录（266 份）

**判据**：已完成批次的执行记录、被取代的设计、一次性调研。**不维护，仅备查**——
里面的数字与路径反映的是当时状态，不要按它操作。

```
archive/
├── 2026-08/   早期（session / study 技术债）
├── 2026-09/   调研与评估
├── 2026-10/   stage-* 施工、C1/B/R 系列、架构再评估
├── 2026-11/   拆分批次、W 系列、graphify 评审
└── undated/   无日期标记的设计稿
```

按主题找：

| 主题 | 位置 |
|---|---|
| `stage-*` 施工记录（B1-B5 / C1 / R1-R6 / D1 / E1-E2 / F1-F4） | `archive/2026-10/` |
| `p*-*` 分阶段设计（P0-P5） | `archive/undated/` 与 `archive/2026-11/` |
| `w*-*` 周批次（W0-W12） | `archive/2026-11/` |
| 调研报告（industry / deepseek / vibe-trading / openclaw） | `archive/undated/` 与 `archive/2026-10/` |
| 过去的架构图（v6-v8 各版 svg/html） | `docs/` 根目录（保留原文件名） |
| 业务侧设计（study / goal / alpha_zoo / backtest） | `archive/2026-11/` |

> **状态说明**：`archive/` 里的文档大量引用了已删除的 `core/` 路径、`apps/goal`、
> `apps/alpha_zoo` 等。这是**历史事实**，不是待修的 bug——它们记录的是当时的架构。

---

## 5. 根目录保留的文件

| 文件 | 为什么留在根 |
|---|---|
| `architecture-diagram.html` | **门禁硬编码此路径**（`sync_architecture_diagram.py`），移动会让 CI 失效 |
| `REFERENCE.md` | README 的直接跳转目标，放在浅层更易发现 |
| `INDEX.md` | 本文件 |
| `architecture-*.svg` / `openclaw-architecture.html` 等 | 架构图的附属资源，被图或历史文档引用 |
| `pyright-*.json` | 工具输出快照，被 `scripts/pyright_regressions.py` 读取 |

---

## 6. 维护约定

1. **新文档先判层**：操作手册 → `guides/`；设计决策 → `design/`；一次性记录 → `archive/<YYYY-MM>/`；**专题研究线 → 自带索引的专属文件夹**（先例：`prompt-genome/`）。
2. **设计先行**：结构改动先写 `design/*-design-*.md`，再实施。完成的实施记录进 `archive/`。
3. **不重写历史**：`archive/` 的文档保留原文，即使里面的路径已失效。
4. **改名准则**：`design/` 里的文档若被代码注释引用，移动时必须同步改代码
   （`scripts/arch/check_dangling_imports.py` 不查 docs 路径，需要人工确认）。
5. **索引同步**：新增 `guides/` 或 `design/` 文档时更新本文件。
