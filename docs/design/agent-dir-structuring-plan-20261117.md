# kernel/agent 目录结构化方案 —— builtin_tools 抽出评估 + 顶层文件分层

> **日期**: 2026-11-17 · **性质**: 调研 + 实施设计（**本轮只梳理，未动代码**）
> **触发问题**：
> ① `kernel/agent/builtin_tools` 能否拿出来与 `agent` 同级？
> ② `agent/` 顶层 53 个文件散落，想结构化分层（例：三个 `compact*.py` 下沉到 `compact/`）

---

## 1 · 现状实测（2026-11-17 AST 扫描）

### 1.1 `kernel/agent` 体量

| 项 | 数值 |
|---|---|
| 总量 | **101 文件 / 431 符号 / 22,540 行**（全仓最大包） |
| **顶层 .py 文件** | **53 个**（不含 `__init__.py`） |
| 子目录 | 6 个：`builtin_tools`(12 文件/52 符) · `strategy`(21/36) · `runtime`(6/14) · `sandbox`(4/8) · `capability`(2/17) · `validators`(2/7) |

顶层文件符号数 top：`compact.py` 24 · `tools.py` 21 · `prompt_schema_extractor.py` 20 · `cache.py` 14 · `tool_result.py` 14 · `deciders.py` 12 · `legacy_audit.py` 12 · `agent_telemetry.py` 11 …… `loop.py` 虽只 7 符号但 **1,580 行**。

### 1.2 顶层文件的自然簇（按前缀分组）

| 簇 | 文件数 | 成员 |
|---|---|---|
| `loop*` | **12** | loop, loop_assembly, loop_compaction, loop_endings, loop_engine, loop_engine_adapters, loop_lifecycle, loop_progress, loop_spec, loop_stream, loop_telemetry, loop_tools |
| `tool*` | 5 | tools, tool_context, tool_result, tools_capability, toolsets |
| `compact*` + loop_compaction | **4** | compact, compaction_message, compaction_runtime, loop_compaction |
| `context*` | 3 | context, context_injector, context_providers |
| `prompt*` | 2 | prompt_builder, prompt_schema_extractor |
| 其它 | 22 | _core_types, cache, event_store, memory_manager, trace, guardrails, deciders, executor, plugin, registry, dag_config, … |

### 1.3 测试影响面（字符串引用计数）

| 目标 | 引用 |
|---|---|
| `kernel.agent.builtin_tools` | **107 处 / 56 文件** |
| `kernel.agent` 顶层任意模块 | **200 处 / 107 文件** |
| `kernel.agent.compact` | 29 处 / 17 文件（+ compaction_* 2 + loop_compaction 1 = **32 / 19**） |
| 字符串路径 `kernel/agent/` | 18 处 / 7 文件 |

---

## 2 · Q1：`builtin_tools` 能否抽到与 `agent` 同级？

### 2.1 实测耦合（双向）

**出边（builtin_tools → agent 内部，9 个模块）**：

```
kernel.agent.tools          ← 11 次（最大单点）
kernel.agent.capability / combo / toolsets / plugin /
dag_config / registry / executor / sandbox           ← 各 1 次
```

**入边（agent 内部 → builtin_tools，7 个顶层文件）**：

```
chat_loop.py · context_injector.py · context_providers.py
executor.py · gateway.py · loop_assembly.py · role_factory.py
```

**外部入边（8 个文件）**：`mcp/server`(2) · `agent_plugins/quant` · `quant_display` · `goal_tools` · `evals/run_evals_llm` · `gateway/singleton` · `orchestration/ticket_store` · `workflow/node_types`

### 2.2 评估：**不建议抽出**

| # | 理由 | 量级 |
|---|---|---|
| 1 | **双向耦合成环**：抽出后 `kernel/agent ↔ kernel/builtin_tools` 成为**包级环**（7 个 agent 文件 import 它，它 import 9 个 agent 模块）—— kernel 子包之间目前无环，这是首个 | 架构性 |
| 2 | **影响面最大**：107 处测试引用 / 56 文件 + 15 个 src 文件 | 全仓最大单点 |
| 3 | **语义不成立**：这 12 个文件是 agent 的**内置能力**（file/shell/web/todo/display/help/options/workspace/bg/subagent），不是业务工具。业务工具 `goal_tools` 刚被并行会话平铺到 `apps/goal_tools.py` —— 那才是该出去的 | 概念 |
| 4 | 抽出后**不减少 agent 的包数计数归属**问题：`scan_arch.pkg_of` 会把 `kernel/builtin_tools` 算成**新包**（35→36），触发全套图数字同步 | 流程成本 |

**如果目标是「让 agent 目录变小」**：先做 §3 的子文件夹分层（**零包数变化、零图漂移**），效果是顶层 53 → 22 文件；`builtin_tools` 留在原地但已不再是顶层噪音（它本来就在子目录里）。

> **若仍坚持抽出**：必须接受 ① 包级环（或先做一次「agent → builtin_tools 单向化」重构，把 7 个入边改成依赖倒置/注册表注入，工作量远超分层本身）② 107+15 处引用全量改写 ③ 图数字全套同步。

---

## 3 · Q2：顶层 53 文件分层 —— 目标形态

### 3.1 划分表（基于 §1.2 实测簇）

| 子文件夹 | 文件数 | 成员 | 说明 |
|---|---|---|---|
| `loop/` | 12 | 全部 `loop*` | 引擎主体（loop.py 1,580 行为核心） |
| `tools/` | 5 | 全部 `tool*` | 工具注册表 / 上下文 / 结果 |
| **`compact/`** | **4** | compact, compaction_message, compaction_runtime, loop_compaction | **本轮试金石**（用户点名） |
| `context/` | 3 | context, context_injector, context_providers | 上下文注入 |
| `prompt/` | 2 | prompt_builder, prompt_schema_extractor | 提示词构造 |
| 根层保留 | 22 | _core_types, cache, event_store, memory_manager, trace, guardrails, deciders, executor, plugin, registry, dag_config, modes, pause, progress, … | 被多方共享的基础件；**先不动**（等前 5 簇落地后再评估是否继续） |

**分阶段**：先做 `compact/`（4 文件，32 测试引用），验证方法论；再决定是否推广到 loop/tools/context/prompt。

### 3.2 为什么子文件夹方案是零风险的（与抽出方案的决定性差异）

| 维度 | 抽出（Q1，同级新包） | 子文件夹（Q2，agent 内） |
|---|---|---|
| `scan_arch.pkg_of` 归并 | `kernel/builtin_tools` = **新包**（35→36） | `kernel/agent/compact/x.py` → parts[0]=kernel, parts[1]=agent → **仍归 `kernel/agent`** |
| 架构图包数 / 符号 / 行数 | 变 → 全套同步 | **完全不变 → 图零漂移** |
| `layer_matrix` 登记 | 需新增 `agentnodes.kernel.builtin_tools` 条目 | `agentnodes.kernel.agent` 已登记 L3，**最长前缀匹配自动覆盖子包，无需登记** |
| 依赖拓志 | 引入包级环 | **完全不变**（同模块同边，只改名字） |

### 3.3 两条硬约束（R-014 实测教训）

**① 不做 re-export 垫片 —— 全量改引用**

测试里有 **25 处 monkeypatch 字符串目标**，其中：

```
8 处  agentnodes.kernel.agent.compact.compact_messages   ← 本簇最大陷阱
2 处  agentnodes.kernel.agent.loop_compaction.logger
```

若 `compact/__init__.py` 做 re-export，测试 patch 的是**包命名空间**的名字，而真实模块 `compact/core.py` 内部调用走**自己的 global** → **patch 静默失效**。这正是 R-014 批次 B 在 14 个测试文件上踩过的坑。

→ **结论：`__init__.py` 只做 `from .core import __all__` 式的最小导出（供 `from ...compact import X` 的公共用法），但所有 monkeypatch 目标必须改成真实模块路径；不保留旧路径别名。**

**② 绝对导入保持**（刚完成的迁移）

移动后一律 `from agentnodes.kernel.agent.compact.core import ...`，不回退相对导入（`check_no_relative_imports` 门禁会拦）。

### 3.4 `compact/` 内部命名

`compact.py` 移进 `compact/` 会成 `compact/compact.py`（`compact.compact` 丑且易混）：

| 原文件 | 建议新路径 | 理由 |
|---|---|---|
| `compact.py` (24 符/821 行) | `compact/core.py` | 核心压缩逻辑；避开 `compact.compact` |
| `compaction_message.py` (2/243) | `compact/message.py` | 去掉重复前缀 |
| `compaction_runtime.py` (1/105) | `compact/runtime.py` | 同上 |
| `loop_compaction.py` (1/255) | `compact/loop_compaction.py` | 保留原名（它是 loop 调用的入口，名字有语义；且与 `loop/` 簇区分） |

> 备选：文件名全保留（`compact/compact.py` 等），diff 更小但路径更丑。**倾向改名**——反正引用全量重写，改名成本为零。

### 3.5 簇内依赖（实测，支持这次下沉）

```
loop_compaction ──→ compact, compaction_message, compaction_runtime   （星型枢纽）
compact / compaction_message / compaction_runtime  互不依赖 ✓
```

外部消费方（移动后需改路径）：`interfaces/api/routers/admin.py` · `interfaces/cli/commands/compact_show.py` · `kernel/session/history_convert.py` · `kernel/session/service.py` · `kernel/agent/loop.py`

---

## 4 · 批次计划（分阶段，每批独立 commit）

| 阶段 | 内容 | 影响面 | 验证 |
|---|---|---|---|
| **0 方案** | 本文档 | — | — |
| **1 试金石** | `compact/` 簇 4 文件 git mv + 改名 + 全量改引用 | src ~5 文件 + 测试 19 文件 32 处 + monkeypatch 9 处 | 全量 pytest + batch_gate + 图零漂移核对 |
| **2 评估** | 复盘试金石的 diff 规模与踩坑，决定是否推广 | — | — |
| 3+（待定） | `loop/` 12 · `tools/` 5 · `context/` 3 · `prompt/` 2 | 200 处测试引用 / 107 文件（全量） | 同上，按簇分批 |

**每批验证（沿用绝对导入迁移的纪律）**：
1. 逐文件 `git diff` 审计（变更行 = `from` 行 + `git mv` 重命名）；
2. `pytest --collect-only` 收集数不变；
3. 全量 pytest `-n 8` 0 failed；
4. `batch_gate` ALL GREEN；
5. **架构图零漂移**（子文件夹方案的预期，跑 sync 确认）；
6. `check_no_relative_imports` 仍 0（不回退相对导入）。

---

## 5 · 风险与已知代价

| 风险 | 量级 | 处置 |
|---|---|---|
| monkeypatch 静默失效（8 处 `compact.compact_messages`） | **高** | 不做 re-export 别名；逐处改成真实模块路径；试金石阶段用 `grep` 全量核对 |
| `loop_compaction` 移入 `compact/` 后，`loop.py` 的 import 变长 | 低 | 语义更好（loop → compact 簇的边界可见） |
| 改名（compact.py→core.py）扩大 diff | 低 | 引用本就全量重写，改名零边际成本 |
| 推广到 `loop/` 时 `loop.py` 与 `loop/` 包同名 | 中 | 阶段 2 再议：或 `loop/core.py`，或保留 `loop.py` 在根层 |
| 图数字漂移 | 无（子文件夹） | `pkg_of` 归并到 `kernel/agent`，实测原理见 §3.2 |

---

## 6 · 明确不做

- **不把 `builtin_tools` 抽到 agent 同级**（§2 理由：包级环 + 107 处影响面 + 语义不成立）；
- **不做 re-export 垫片**（R-014 monkeypatch 静默失效教训）；
- **不回退绝对导入**（`check_no_relative_imports` 门禁）；
- **不动 `strategy/`、`runtime/`、`sandbox/`、`capability/`、`validators/`**（已在子目录，结构合理）；
- **不一次性重组全部 53 文件**（先试金石，再评估推广）。

---

## 7 · 待拍板

1. **Q1 结论「不抽出」是否接受？**（若坚持，需先做 agent→builtin_tools 单向化重构）
2. **试金石阶段 1 是否开工？**（`compact/` 4 文件，32 测试引用）
3. **compact 内改名方案**：`core.py / message.py / runtime.py`（倾向） vs 文件名全保留？

---

## 8 · 执行结果（2026-11-17 阶段 1 试金石实测）

### 8.1 落地

`compact/` 簇 4 文件下沉完成（commit 见 git log）：

| 原路径 | 新路径 |
|---|---|
| `kernel/agent/compact.py` | `kernel/agent/compact/core.py` |
| `kernel/agent/compaction_message.py` | `kernel/agent/compact/message.py` |
| `kernel/agent/compaction_runtime.py` | `kernel/agent/compact/runtime.py` |
| `kernel/agent/loop_compaction.py` | `kernel/agent/compact/loop_compaction.py` |
| — | `kernel/agent/compact/__init__.py`（新增，**零 re-export**，仅 docstring） |

### 8.2 实测影响面（比方案预估大）

| 项 | 方案预估 | **实测** |
|---|---|---|
| 模块路径引用 | 32 处 / 19 文件（tests） | **76 处 / 30 文件**（src 25 + tests 21 + scripts 1，含包级 import 形态） |
| 包级 import 形态 `from pkg import compact` | 未单列 | **20 处**（test_admin_kill_switch 8 · test_compact_safety 4 · compaction_runtime/loop_compaction 各 1 · test_b1c 3 · test_no_cycle 1 等） |
| monkeypatch 目标 | 8 处 | **9 处**（8 × `compact.compact_messages` + 1 × `loop_compaction.logger`） |
| 新增文件数 | 0 | **+1**（`compact/__init__.py`）→ L3 208→209 文件、48,145→48,165 行 |

### 8.3 三个踩坑（均为本批实测发现，已修）

1. **规则 B 不幂等 → 二次替换**：改写脚本第一版的 `compact` 替换无负向前瞻，把规则 B 刚产出的
   `...compact.message` 又二次替换成 `...compact.core.message`（5 处固化）。
   → 加 `(?!(?:core|message|runtime|loop_compaction))` 前瞻 + 精确回正脚本修复。
   **教训：批量替换脚本必须自证幂等（跑两遍结果应相同）。**
2. **包级 import 的 group 拼接错误**：正则 group(1) 若含 `from ... import ` 再拼新路径 →
   `from X import X.compact ...` 语法错误（6 文件被语法校验拦下，未写入 —— **自校验救了这批**）。
3. **subprocess 字符串模板不被正则命中**：`test_no_loop_compaction_cycle.py` 里
   `f"import {AGENT}.{first}"` 是运行时拼接，源码无字面路径 → 4 组参数 + 3 处字面需手工改。

### 8.4 关键设计验证（§3.3 两条硬约束均成立）

- **绑定名保持**：`from pkg import compact as X` → `from pkg.compact import core as X`
  —— 后续 `X.attr` / `importlib.reload(X)` / `monkeypatch.setattr(X, ...)` **零改动**，
  `test_compact_safety` 的 8 处 reload、`test_b1c` 的 `cr._compact_mod` patch 全部自动正确。
- **monkeypatch 直达真实模块**：9 处目标全部变为 `...compact.core.compact_messages`（非包命名空间），
  无 R-014 静默失效。

### 8.5 零漂移原理验证（§3.2）

`scan_arch.pkg_of` 把 `kernel/agent/compact/*.py` 归并到 `kernel/agent` →
**包数 36 不变、符号 431 不变**；仅 files 101→102、lines 22540→22562（新增 `__init__.py` + 编辑）。
依赖拓扑不变（同模块同边）。layer_matrix 无需登记（前缀匹配）。

### 8.6 验证

- 全量 **7596 passed / 106 skipped / 0 failed**（rc=0）
- `batch_gate` **ALL GREEN**（11 步，含相对导入 / 装配纪律两道新门禁）
- 两图 JS `node --check` 通过；framework 文档数字同步（L3 209 文件 / 合计 431）
- compact 相关测试 121 passed（独立快跑）

### 8.7 遗留（非本批引入，报告不擅改）

`architecture-layered-l3-clusters.html` 头部写 **140 条包间依赖**，但实测两种口径分别为
**138**（两端 n>0）与 **143**（全部包）—— 140 与两者都对不上，是并行会话提交 `7b911121`
时就写错的数字（本批未改，留给其归属方）。本批只同步了自己引入的 kernel/agent 节点
files/lines。
