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
| 看架构长什么样（含实测数字） | [`architecture-diagram.html`](architecture-diagram.html) |
| 理解四层骨架为什么这样分 | [`design/layered-skeleton-design-20261103.md`](design/layered-skeleton-design-20261103.md) |
| 知道现在拆到哪一步、接下来删什么 | [`design/subtraction-roadmap-v2-20261105.md`](design/subtraction-roadmap-v2-20261105.md) |
| 跑测试 / 门禁 / 分片 | [`guides/test-marker-lanes-design.md`](guides/test-marker-lanes-design.md) |
| 改代码前确认没有已知坑 | [`guides/known-test-defects-20261104.md`](guides/known-test-defects-20261104.md) |

---

## 1. guides/ — 现行操作手册（13 份）

**判据**：现在照着做就是对的。内容与代码同步维护。

| 文档 | 用途 |
|---|---|
| [`code-map.md`](guides/code-map.md) | 代码地图：哪个目录放什么 |
| [`agent-tools-reference.md`](guides/agent-tools-reference.md) | Agent 工具手册 |
| [`extension-points.md`](guides/extension-points.md) | 扩展点放哪（7 机制分工 + 决策树） |
| [`toolset-convention.md`](guides/toolset-convention.md) | 工具集命名与组织约定 |
| [`llm-provider-setup.md`](guides/llm-provider-setup.md) | LLM 供应商配置 |
| [`model-catalog-design.md`](guides/model-catalog-design.md) | 模型目录（ModelHub 解析入口） |
| [`logging-unified-entry-design-20261102.md`](guides/logging-unified-entry-design-20261102.md) | 日志统一入口（`SR_LOG_*` 语义） |
| [`test-marker-lanes-design.md`](guides/test-marker-lanes-design.md) | 测试 lane（llm/e2e/slow）+ 本地验证矩阵 |
| [`ci-sharding-strategy.md`](guides/ci-sharding-strategy.md) | CI 分片（LPT + flaky 治理） |
| [`known-test-defects-20261104.md`](guides/known-test-defects-20261104.md) | 已知测试缺陷（与减法正交的既存问题） |
| [`xfail-tests.md`](guides/xfail-tests.md) | xfail 清单与理由 |
| [`legacy-sessiondb-tech-debt.md`](guides/legacy-sessiondb-tech-debt.md) | 历史 SessionDB 技术债 |
| [`architecture-diagram-changelog.md`](guides/architecture-diagram-changelog.md) | 架构图版本史（v5.1 → 当前） |

---

## 2. design/ — 仍生效的设计与契约（46 份）

**判据**：文档里的决策仍在代码中执行。已被取代的移到 `archive/`。

### 2.1 分层与拆分（当前主线）

| 文档 | 内容 |
|---|---|
| [`layered-skeleton-design-20261103.md`](design/layered-skeleton-design-20261103.md) | 四层骨架契约（层归属表、方向规则、机器门禁） |
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

### 2.6 其他契约

| 文档 | 内容 |
|---|---|
| [`plug-in-contract-design.md`](design/plug-in-contract-design.md) | 插件契约 |
| [`truthfulness-common-rules-design.md`](design/truthfulness-common-rules-design.md) | 真实性通用规则 |
| [`phase3-dual-factory-design.md`](design/phase3-dual-factory-design.md) | 双工厂 |
| [`phase5-guardrails-design.md`](design/phase5-guardrails-design.md) | 护栏 |

### 2.7 ADR

| 文档 | 决策 |
|---|---|
| [`adr/ADR-000-baseline-recount.md`](design/adr/ADR-000-baseline-recount.md) | 基线口径：每条 import 语句计 1 |
| [`adr/ADR-002-l1-adapter-policy.md`](design/adr/ADR-002-l1-adapter-policy.md) | L1 适配器策略 |
| [`adr/ADR-003-l3-l2-disposition.md`](design/adr/ADR-003-l3-l2-disposition.md) | L3→L2 处置（CapabilityRegistry 端口化） |

---

## 3. archive/ — 历史施工记录（264 份）

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

## 4. 根目录保留的文件

| 文件 | 为什么留在根 |
|---|---|
| `architecture-diagram.html` | **门禁硬编码此路径**（`sync_architecture_diagram.py`），移动会让 CI 失效 |
| `REFERENCE.md` | README 的直接跳转目标，放在浅层更易发现 |
| `INDEX.md` | 本文件 |
| `architecture-*.svg` / `openclaw-architecture.html` 等 | 架构图的附属资源，被图或历史文档引用 |
| `pyright-*.json` | 工具输出快照，被 `scripts/pyright_regressions.py` 读取 |

---

## 5. 维护约定

1. **新文档先判层**：操作手册 → `guides/`；设计决策 → `design/`；一次性记录 → `archive/<YYYY-MM>/`。
2. **设计先行**：结构改动先写 `design/*-design-*.md`，再实施。完成的实施记录进 `archive/`。
3. **不重写历史**：`archive/` 的文档保留原文，即使里面的路径已失效。
4. **改名准则**：`design/` 里的文档若被代码注释引用，移动时必须同步改代码
   （`scripts/arch/check_dangling_imports.py` 不查 docs 路径，需要人工确认）。
5. **索引同步**：新增 `guides/` 或 `design/` 文档时更新本文件。
