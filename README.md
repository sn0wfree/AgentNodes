# AgentNodes

**可自我进化的 Agent 框架** — 四层单向依赖 · 依赖方向机器可证 · 能力端口化 · RSI 闭环

> **项目状态：拆分进行中（2026-11-05）**
> 本仓库从 QuantNodes 量化研究项目中剥离出 **agent 框架**部分独立开发，业务侧（回测、因子、行情）
> 正按 [`docs/design/subtraction-roadmap-v2-20261105.md`](docs/design/subtraction-roadmap-v2-20261105.md)
> 的批次计划逐步移除。
> 当前的 `apps/` 目录是**业务插件示例**，不是框架本体；批次 4–9 完成后它只剩
> `evolution`（RSI 闭环）与 `capabilities`（注册门面）。
> 包名 `strategy_research` → `agentnodes` 的重命名是**批次 9**，尚未执行。

---

## 这是一套什么样的框架

大部分 Agent 框架的架构图只能靠人读代码来相信。AgentNodes 的不同点在于：
**分层规则由 AST 门禁强制执行，图上的每个数字都是实测回写，过时即 CI 红。**

| 支柱 | 是什么 | 怎么被强制 |
|---|---|---|
| **四层单向依赖** | L1 interfaces → L2 apps → L3 kernel → L4 foundation，只准向下 | `tests/test_layer_matrix.py` 逐条解析 import，分簇棘轮只降不升 |
| **能力端口化** | kernel 不 import apps；kernel 要业务能力时走 `CapabilityRegistry.resolve(kind)` | `L3→L2` 从 46 条清零，基线锁死为 0 |
| **架构图与代码同源** | 层体量 / 跨层边数 / 上行违规全部 AST 实测 | `scripts/arch/sync_architecture_diagram.py`，图过时即 CI 红 |
| **自我进化（RSI）** | Tier 0 冻结 · Tier 1 提案-验证-回滚 · Tier 2 研究产物 | `apps/evolution/`，`GenomeProposal` 三处结构性拒绝 Tier 0 |

---

## 核心能力

### 运行时

- **AgentLoop** — ReAct 循环，六个 Mixin 分层组合（`kernel/agent`，22.5k 行，最大子模块）
- **GraphEngine** — 唯一执行引擎：`prepare / run / settle`，llm + react 两类节点（`kernel/runtimes`）
- **WorkflowCompiler** — 工作流编译到 GraphEngine，支持 `resume_from_store` 断点续跑（`kernel/workflow`）
- **Swarm** — 多智能体预设，编译为图执行（`kernel/swarm`，37 个内置 preset）

### 控制面

- **Gateway** — 唯一控制入口：`submit` / `receipt` / `cancel` / `recover(fold)`
- **Orchestration** — `WorkRequest` 契约 + `Mailbox`（cap 20 / 老化 / steer）+ `EntryPoint` + `BudgetGate`
- **Ticket / Assignment** — 通用任务身份与执行绑定，三态票据 / 七态作业，崩溃恢复
- **Events** — 事件溯源：`emit` / `subscribe` / `replay`，唯一事实源

### 能力供给

- **CapabilityRegistry** — `kind → factory` 的进程级注册表，apps 注册、kernel 解析（当前 22 kind）
- **ContextHub** — `collect → arbitrate → render`，中枢不 import 生产方
- **Permission** — ASK 审批闸，可插拔 evaluator
- **Memory / Hooks / Skills** — 跨会话记忆（SQLite）、只读钩子、技能包加载

### RSI 自我进化

| Tier | 内容 | 约束 |
|---|---|---|
| **Tier 0** | 冻结基线 | 永不可提案；`GenomeProposal.new()` 与 `validation_gate` 双重拒绝 |
| **Tier 1** | 行为工件（prompt / skill / tool_set / compaction） | 提案 → 验证 → ±20% 退化阈值 → 连续 3 次失败回滚 |
| **Tier 2** | 研究产物 | 业务侧自决，不过闸 |

---

## 快速开始

```bash
git clone https://github.com/sn0wfree/AgentNodes.git
cd AgentNodes
pip install -e ".[dev]"
```

配置 LLM（任选其一）：

```bash
export OPENAI_API_KEY=sk-xxx
export OPENAI_BASE_URL=https://api.deepseek.com/v1   # 可选
export OPENAI_MODEL=deepseek-chat                    # 可选
```

或走 5 步向导：

```bash
quantnodes-research init
```

启动交互界面：

```bash
quantnodes-research              # TTY → Textual full-screen TUI
quantnodes-research --repl       # 旧版 prompt_toolkit REPL
quantnodes-research --help       # 查看全部子命令
```

### 程序化使用

```python
from strategy_research.kernel.orchestration import WorkRequest, EntryPoint
from strategy_research.kernel.agent.capability import register, resolve

# 注册一个能力（业务侧职责）
register("my.service", lambda registry: MyService())

# 框架侧按 kind 解析，不知道实现是谁
service = resolve("my.service")
```

> 包名重命名（`strategy_research` → `agentnodes`）是批次 9，完成后以上 import 会同步更新。

---

## 架构

完整架构图（含各层体量、跨层边数、违规矩阵）：**[`docs/architecture-diagram.html`](docs/architecture-diagram.html)**

```
┌──────────────────────────────────────────────────────────┐
│ L1 interfaces   渠道层 — api / cli / mcp + 组装根          │
├──────────────────────────────────────────────────────────┤
│ L2 apps         业务域 — 当前为插件示例（批次 4–6 移除中）  │
├──────────────────────────────────────────────────────────┤
│ L3 kernel       平台机制 — 控制面 / 执行面 / 能力面         │
├──────────────────────────────────────────────────────────┤
│ L4 foundation   基础设施 — llm / storage / utils / ...     │
└──────────────────────────────────────────────────────────┘
```

层归属表与判定算法在 `scripts/arch/layer_matrix.py`（唯一事实源），
`tests/test_layer_matrix.py` 与 `scripts/arch/check_dangling_imports.py --layers` 共用它，不会漂移。

### 为什么业务包在 L2

框架需要业务能力，但**不允许**内核反向依赖业务。解法是把依赖方向倒过来：

```
业务侧                         框架侧
apps/__init__.py               kernel/agent/capability/
  register(kind, factory) ──→  registry.resolve(kind)
       ↑                              │
       └────── 注册时 import ─────────┘（方向：apps → kernel，合法）
```

kernel 侧所有 `resolve()` 都是**惰性 + try/except**：kind 不存在时运行时降级为 no-op，
框架其余功能不受影响。这是业务包可以被整批删除而不破坏框架的结构性原因。

---

## 功能面

| 面 | 数量 | 说明 |
|---|---|---|
| CLI 顶层命令 | 18 | 见 [`docs/REFERENCE.md`](docs/REFERENCE.md) |
| MCP 工具 | 17 | stdio + SSE 传输 |
| Swarm preset | 37 | YAML DAG，编译为图执行 |
| 能力 kind | 22 | apps 注册，kernel 解析（批次 4 已删 backtest/config 四类） |
| 测试 | 7,400+ | `pytest tests --collect-only -q` |

> ⚠️ **批次 4–6 正在移除业务功能**（回测 / 数据 / 因子相关命令与工具）。
> 上表是本文写作时的快照；每张表的「移除批次」列见 REFERENCE.md。
> 框架本体的命令（`llm` / `compact` / `skills` / `swarm` / `mcp` / `serve` / `session`）不受影响。

---

## 开发

```bash
# 架构门禁（改完代码必跑）
python scripts/arch/check_dangling_imports.py      # 悬空 import
python scripts/arch/check_path_literals.py          # 坏路径拼接
python scripts/arch/sync_architecture_diagram.py    # 架构图是否过时

# 改图后自动回写实测数字
python scripts/arch/sync_architecture_diagram.py --write

# 测试
pytest tests -q
pytest tests -n 4 --dist loadgroup -q               # 并行（8–12 分钟）

# Lint
ruff check .
```

工具目录的使用契约见 [`scripts/README.md`](scripts/README.md)。

---

## 文档

```
docs/
├─ INDEX.md          ← 从这里开始
├─ REFERENCE.md      ← 命令 / 工具 / 环境变量全表
├─ guides/           ← 现行规范（架构、开发、术语）
├─ design/           ← 仍有效的设计（含 adr/）
└─ archive/          ← 历史施工记录（备查，不维护）
```

设计先行是本项目的纪律：每个结构改动先有 `*-design.md`，后有实施 commit。

---

## 借鉴致谢

- **vibe-trading-ai**（HKUDS，MIT）— TUI 交互范式、工具层、Trace、Memory
- **llmwikify**（MIT）— Hook 系统（13 事件点）、四层骨架与「机器可证」的设计思路

---

## 许可证

MIT
