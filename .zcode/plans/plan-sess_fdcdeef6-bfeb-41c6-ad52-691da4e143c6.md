# 项目文档全面更新方案

## Context

README.md 描述的是「32 CLI 命令 / 460 因子 / 三层架构」的完整量化研究产品，而代码已是另一个项目：`core/` 目录已不存在（四层变为 interfaces/apps/kernel/foundation），alpha_zoo 27,263 行已删，goal/hypothesis/portfolio/validation/export 9,328 行已删，study 12,000 行已删。项目正在按 `docs/subtraction-roadmap-v2-20261105.md` 从「量化研究框架」蜕变为「通用 Agent 框架」，批次 4-9 还要再删 31%（约 16,591 行），最终 `strategy_research` → `agentnodes`。

已确认的决策：**按终态写**（描述平台本体，业务包标为「插件示例，移除中」）、**docs 分 guides/design/archive 三层**、**包名暂用 `strategy_research` + 待改名注**。

预期结果：新人照着 README 能跑通框架本体；文档在批次 4-6 删代码时不会失效；323 个 docs 文件有明确的三层归属。

---

## 一、README.md 重写（840 行 → 约 250 行）

**定位**：`AgentNodes — 可自我进化的 Agent 框架`

结构：

| 节 | 内容 | 数据来源 |
|---|---|---|
| 首段 | 四层骨架 + 门禁可验证 + RSI 自我进化；一句「业务包是示例插件」 | `docs/layered-skeleton-design-20261103.md`、`architecture-diagram.html` |
| 核心特性 | AgentLoop / GraphEngine / CapabilityRegistry（26 kind）/ RSI Tier 0-2 / Ticket-Assignment / 事件溯源 / 四层门禁 | `kernel/agent/`、`kernel/runtimes/`、`kernel/orchestration/`、`apps/evolution/` |
| 快速开始 | `pip install -e .` → `quantnodes-research init` → 跑 TUI；含实测可跑的最小 Python 片段 | 实跑验证 |
| 架构一图 | 链到 `docs/architecture-diagram.html`，不重复画 | 门禁实测数字 |
| 功能面一览 | 24 CLI 顶层命令 / 17 MCP 工具 / 37 swarm preset（**标注哪些随批次 4-6 移除**）| 实测 |
| 文档导航 | guides / design / archive 三层入口 | 新建 |
| 开发 | `ruff check .` / `pytest` / 门禁三条命令 | `scripts/README.md` |

**删除的内容**：整个「因子体系（460+ 因子）」节、`AlphaZooAdapter` 示例、goal/hypothesis/portfolio/validation 全部 CLI 行、「三层架构」章节、`.prompts` 11 个 / `.skills` 27 份的过期数字、所有 `core.*` import 路径。

**保留但修正**：TUI 布局图（结构仍准确）、数据源表、Web UI 章节、借鉴致谢。

---

## 二、新建 docs/REFERENCE.md

装全部可查表：24 个 CLI 命令逐个（名字/用途/示例/所属批次）、17 个 MCP 工具、37 个 swarm preset 分类表、环境变量全表（`SR_*` / `OPENAI_*` / `QUANTNODES_RESEARCH_*`）、工作区目录树、compact 配置项。每张表加一列「批次 4-6 是否移除」。

---

## 三、docs/ 三层重构

```
docs/
├─ INDEX.md              ← 唯一索引（重写）
├─ guides/               ← 现行规范，~12 份
│   ├─ architecture.md                （从 layered-skeleton-design 提炼，去掉 core/ 与 alpha_zoo）
│   ├─ architecture-diagram.html      （保留原位？见下）
│   ├─ development.md                 （测试/门禁/提交规范）
│   ├─ glossary.md                    （WorkRequest/Ticket/Tier/kind 等术语）
│   └─ ...
├─ design/               ← 仍有效的设计（~40 份）
│   ├─ adr/              （ADR-000/002/003 原样）
│   ├─ subtraction/      （subtraction-roadmap-v2 + batch3 设计）
│   └─ ...
└─ archive/              ← 一次性施工记录（~250 份）
    ├─ 2026-10/
    └─ 2026-11/
```

**关键约束**：41 份文档被源码注释引用（如 `run-backtest-data-gate.md` 被 12 个文件引用）。迁移时**同步改写代码注释里的路径**，并跑 `python -m compileall` + `pytest tests/test_scripts_arch.py` 验证。`docs/architecture-diagram.html` **不移动**（门禁硬编码 `docs/architecture-diagram.html` 路径，见 `sync_architecture_diagram.py:34`）。

**分类判据**（可复现）：`stage-*` / `p*` / `w*` / `phase*` / `b*` / `r*` 编号批次前缀 → archive；`*-fix.md` 等修复记录 → archive；`architecture-*` / `*-design.md` 且仍被代码引用 → guides 或 design；`adr/` 原样进 design。

---

## 四、其他待更新文件

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | `description` 从「通用策略自动研究框架」改为 Agent 框架定位 |
| `scripts/README.md` | 已准确，只补 docs 新路径 |
| `CHANGELOG.md` | 加一节「项目重新定位」，说明拆分背景与批次进度 |
| `docs/INDEX.md` | 重写为三层索引 + 每份文档状态标注（✅现行/⚠部分失效/📦历史） |
| `DEMO.md` | 核对是否含失效命令 |

---

## 五、执行顺序

1. **实测基线**：`python scripts/arch/sync_architecture_diagram.py` + `pytest --collect-only` 记录当前数字
2. **建 docs 三层**：`git mv` 分批移动，每批后跑 `check_dangling_imports` + `compileall`
3. **改 41 处代码注释路径**
4. **重写 README.md**
5. **新建 REFERENCE.md**
6. **重写 INDEX.md**
7. **改 pyproject.toml / CHANGELOG.md**
8. **验证**（见下）

因为另一个 agent 正在跑批次 4-6，我会在**开始前和每次写文件前重新实测**，避免写入已删除模块的描述。

---

## 六、验证

| 检查 | 命令 | 期望 |
|---|---|---|
| 架构图一致 | `python scripts/arch/sync_architecture_diagram.py` | 全 `[OK]` |
| 零悬空 import | `python scripts/arch/check_dangling_imports.py` | exit 0 |
| 零坏路径字面量 | `python scripts/arch/check_path_literals.py` | exit 0 |
| 层门禁 | `pytest tests/test_layer_matrix.py -q` | 绿 |
| arch 单测 | `pytest tests/test_scripts_arch.py -q` | 绿 |
| 文档路径无死链 | 自写脚本扫 README/REFERENCE/INDEX 里的 `docs/...` 引用是否都存在 | 0 死链 |
| CLI 命令真实性 | 逐条跑 `quantnodes-research <cmd> --help` | 全部 exit 0 |
| 收集期无错 | `pytest --collect-only -q` | 0 errors（除已存在的 playwright 5 个）|

---

## 需要你注意的两点

1. **文档与代码的真实性边界**：按终态写意味着 README 会描述一些**尚未完全就绪**的东西（如包名 `agentnodes`）。我会在每处加明确标注「批次 9 改名」，不让读者误以为现在就能 `import agentnodes`。

2. **另一个 agent 的并发风险**：批次 4-6 正在执行。如果它删掉某个我已在文档里描述的功能，文档会短暂失真。建议 docs 重构**排在批次 6 之后**，或者我只写「框架本体」部分（不受批次 4-6 影响），业务功能面统一放到 REFERENCE.md 并标注批次。