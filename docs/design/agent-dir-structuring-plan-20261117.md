# kernel/agent 目录结构化方案 —— builtin_tools 抽出评估 + 顶层文件分层

> **日期**: 2026-11-17 · **性质**: 调研 + 实施设计 · **状态**: 阶段 8 实施完成；全量验收有 1 项无关失败
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

---

## 9 · 阶段 2 实测（2026-11-17，prompt + context 簇）

### 9.1 落地

| 原路径 | 新路径 |
|---|---|
| `kernel/agent/prompt_builder.py` | `kernel/agent/prompt/builder.py` |
| `kernel/agent/prompt_schema_extractor.py` | `kernel/agent/prompt/schema_extractor.py` |
| `kernel/agent/context.py` | `kernel/agent/context/core.py` |
| `kernel/agent/context_injector.py` | `kernel/agent/context/injector.py` |
| `kernel/agent/context_providers.py` | `kernel/agent/context/providers.py` |
| — | `prompt/__init__.py` + `context/__init__.py`（新增，零 re-export） |

引用改写 **79 处 / 38 文件**（B=74 模块路径 · A=3 包级 import · C=2 文件路径）。

### 9.2 幂等性教训的**换形态复现**（已修）

阶段 1 的教训是「规则 B 不幂等 → 二次替换固化」。阶段 2 换了形态重演：

- 规则 A 的**输出** `from agentnodes.kernel.agent.prompt import builder as pb_mod` 里含
  `agentnodes.kernel.agent.prompt`；
- 规则 B 的短前缀正则 `agentnodes\.kernel\.agent\.prompt\b(?!\.builder)` 负向前瞻
  只排了 `.builder`，没排 ` import` → 第二遍匹配 A 的输出，改写成
  `from agentnodes.kernel.agent.prompt.builder import builder as pb_mod`
  —— **语法合法但语义错误**（`builder` 模块里没有 `builder` 这个名字）。

**修复**：负向前瞻补 `(?![\s]+import)`；第二遍验证 0 文件 0 处（幂等达成）。

> **通用规则（本次固化）**：批量替换脚本的幂等性必须同时满足
> ① 已生成的目标形态不再命中（排 `.子模块`）② **其他规则的输出形态也不再命中**（排 ` import`）。
> 验证方法：**跑两遍，第二遍必须 0 改动**。

### 9.3 `__file__` 相对路径被移动破坏（10 个测试失败，已修）

`prompt/builder.py:51` 与 `prompt/schema_extractor.py:377/531` 共 3 处：

```python
Path(__file__).parent.parent.parent / "templates" / ".prompts"
# 原注释：Resolves to: src/agentnodes/templates/.prompts/
```

原文件在 `kernel/agent/`（3 层 = `agentnodes/`）；下沉到 `kernel/agent/prompt/` 后 3 层 =
`kernel/` → 解析到 `kernel/templates/.prompts`（不存在）→ 角色发现静默失败 →
`_NullBuilder` 兜底 / prompt 为空 / `list_roles()` 缺角色，**10 个测试红**。

**修复**：改为按包根定位，未来再移动也不错位：

```python
next(p for p in Path(__file__).parents if p.name == "agentnodes") / "templates" / ".prompts"
```

> **通用规则（本次固化）**：移动文件前必须 grep `__file__` / `parents[` / `.parent` ——
> 层数敏感的路径计算是子文件夹化的**头号隐藏破坏面**（症状是"静默兜底"而非报错，极难定位）。
> compact 簇 4 文件无 `__file__` 用法，故阶段 1 未暴露此问题。

### 9.4 与阶段 1 对比

| 项 | 阶段 1（compact） | 阶段 2（prompt+context） |
|---|---|---|
| 引用改写 | 76+5 处 / 30 文件 | 79 处 / 38 文件 |
| 包级 import 形态 | 20 处 | 3 处 |
| monkeypatch | 9 处 | 2 处 |
| importlib.reload | 8 处 | 0 |
| subprocess 模板 | 4 组 | 0 |
| **`__file__` 路径破坏** | 无 | **3 处 / 10 测试红** |
| 幂等坑 | 规则 B 自身不幂等 | 规则 B 命中规则 A 的输出（换形态） |

### 9.5 验证

- 全量 **7596 passed / 106 skipped / 0 failed**（rc=0）
- `batch_gate` **ALL GREEN**（11 步）
- prompt/context 相关 **164 passed**（含路径修复前的 10 个红全部转绿）
- 幂等验证：改写脚本第二遍 **0 文件 0 处**
- 两图 JS `node --check` 通过；framework 文档数字同步（L3 211 文件 / 合计 433）
- `kernel/agent`：104 文件 / 22614 行 / **符号 431 不变、包数 36 不变**（零漂移原理持续成立）

---

## 10 · 阶段 3 实测（2026-11-17，tools 簇）

### 10.1 落地

| 原路径 | 新路径 |
|---|---|
| `kernel/agent/tools.py` | `kernel/agent/tools/core.py` |
| `kernel/agent/tool_result.py` | `kernel/agent/tools/result.py` |
| `kernel/agent/tool_context.py` | `kernel/agent/tools/context.py` |
| `kernel/agent/tools_capability.py` | `kernel/agent/tools/capability.py` |
| `kernel/agent/toolsets.py` | `kernel/agent/tools/toolsets.py` |
| — | `kernel/agent/tools/__init__.py`（新增，零 re-export） |

引用改写 **101 处 / 81 文件**（B=99 模块路径 · C=2 文件路径；三批里影响面最大）。

### 10.2 坑 1：幂等前瞻**过度泛化**（阶段 2 教训的错误泛化）

阶段 2 为防「规则 B 命中规则 A 的输出」，给短前缀加了 `(?![\s]+import)`。
阶段 3 直接沿用 → **把本该改写的 101 处里的主力形态一起排掉**：

```
from agentnodes.kernel.agent.tools import ToolRegistry   ← 模块路径 + 直接 import 符号
     ^^^^^^^^ tools 后是「 import」，前瞻 (?![\s]+import) 成立 → 被排除 → 没改
```

结果只改了 27/99 处，而 `agentnodes.kernel.agent.tools` 已变成包 →
`from ...agent.tools import ToolRegistry` 会 ImportError。

**修正**：前瞻只排「import 的**是本包子模块名**」这一种形态：

```python
agentnodes\.kernel\.agent\.tools\b
(?!\.(?:core|result|context|capability|toolsets))        # 已生成子模块形态
(?!\s+import\s+(?:core|result|context|capability|toolsets)\b)  # 规则 A 的输出形态
```

> **通用规则修正（覆盖阶段 1/2 的表述）**：幂等前瞻要**精确排除「其他规则的输出形态」**，
> 不能笼统排除某种**语法片段**（` import`）—— 同一片段既可能是别的规则的输出，
> 也可能是本规则**该处理的输入**。判据是「排除的是不是**具体那个形态**」。
> 验证仍是：跑两遍，第二遍 0 改动（阶段 3 第二遍 0 文件 0 处 ✓）。

### 10.3 坑 2：合成树夹具必须保持原样

`tests/test_scripts_arch.py` 与 `tests/test_scripts_migration.py` 用
`src/agentnodes/kernel/agent/tools.py` 作**假路径**测相对导入解析器
（在自己的 tmp 树里造文件，与真文件位置无关）。规则 C 若把这两处也改成
`tools/core.py`，则 `from ..agent.tools import ok` 的目标从**模块**变成**目录**
→ 解析器判定改变 → 两个门禁测试红。

**处置**：规则 C 改完后**还原**这两处夹具路径（验证脚本第 2 项断言夹具保持 2 处）。

### 10.4 tools 簇没有 `__file__` 路径计算（阶段 2 的隐藏破坏源不在）

5 个文件均无 `__file__` / `parents[` / `.parent` 用法 → 无阶段 2 那种「静默兜底」风险。
（其余两批：compact 无、prompt/context 有 3 处 → 已修）

### 10.5 与前两批对比

| 项 | 阶段 1（compact） | 阶段 2（prompt+context） | 阶段 3（tools） |
|---|---|---|---|
| 引用改写 | 81 处 / 30 文件 | 79 处 / 38 文件 | **101 处 / 81 文件** |
| 包级 import 形态 | 20 处 | 3 处 | **0 处** |
| monkeypatch | 9 处 | 2 处 | **0 处** |
| importlib.reload | 8 处 | 0 处 | 0 处 |
| `__file__` 路径破坏 | 无 | **3 处 / 10 测试红** | 无 |
| 幂等坑 | 规则 B 自身不幂等 | 规则 B 命中规则 A 输出 | **前瞻过度泛化（漏改 72 处）** |

### 10.6 验证

- 全量 **7596 passed / 106 skipped / 0 failed**（rc=0）
- `batch_gate` **ALL GREEN**（11 步，含 check_tool_refs 工具引用门禁）
- tools 相关 **154 passed**
- 幂等：第二遍 **0 文件 0 处**
- 旧形态残留 0 / 二次替换污染 0 / 合成树夹具保持 2 处
- `kernel/agent`：105 文件 / 22636 行 / **符号 431 不变、包数 36 不变**

---

## 11 · 阶段 4 实测（2026-11-17，loop 簇 · 方案 A 全去重前缀）

### 11.1 落地（11 文件 → loop/ 子包）

| 原路径 | 新路径 |
|---|---|
| `loop.py` | `loop/core.py` |
| `loop_assembly.py` | `loop/assembly.py` |
| `loop_endings.py` | `loop/endings.py` |
| `loop_engine.py` | `loop/engine.py` |
| `loop_engine_adapters.py` | `loop/engine_adapters.py` |
| `loop_lifecycle.py` | `loop/lifecycle.py` |
| `loop_progress.py` | `loop/progress.py` |
| `loop_spec.py` | `loop/spec.py` |
| `loop_stream.py` | `loop/stream.py` |
| `loop_telemetry.py` | `loop/telemetry.py` |
| `loop_tools.py` | `loop/tools.py` ⚠️ 与 `agent.tools`（阶段 3）视觉相近，路径唯一 |
| — | `loop/__init__.py`（新增，零 re-export） |

引用改写 **123 处 / 77 文件**（B=116 · A=5 · C=2）。

### 11.2 新坑：**源码路径断言的 Path 拼接形态**

本仓有一类**结构契约测试**会**读源码文件内容做 AST 分析**（`test_step_contract.py`、
`test_architecture_gates.py`）。它们引用源码路径有**两种形态**：

| 形态 | 例 | 规则 C 覆盖 |
|---|---|---|
| A 字符串 | `"kernel/agent/loop.py"` | ✅ |
| **B Path 拼接** | `SRC / "loop.py"` | ❌ **覆盖不到** → 5 个测试红（FileNotFoundError） |

**处置**：补规则 —— 移动文件后必须 grep **两种形态**：
`/\s*"<文件名>\.py"`（Path 拼接）与 `"[^"]*<文件名>\.py"`（字符串）。

**通用规则（本次固化）**：移动文件后，除了 import 引用，还要 grep
**「按路径读源码」的所有引用形态** —— 字符串 / Path 拼接 / f-string 拼接 / 参数元组，
四类都要查（前四轮分别踩了其中三类）。

### 11.3 阶段 1/3 的坑在阶段 4 重现（均已按既定规则处理）

| 坑 | 阶段 4 表现 | 处置 |
|---|---|---|
| f-string 模板（阶段 1） | `test_no_loop_compaction_cycle.py` 的 4 组参数元组 `"loop"` + `{AGENT}.loop import` | 手工改 5 处（`"loop"` → `"loop.core"`）——「import 包 ≠ import 模块」，不改则语义变 |
| 合成树夹具（阶段 3） | `test_scripts_arch.py` 5 处 `src/agentnodes/kernel/agent/loop.py` 假路径 | 规则 C 改完**还原**（验证断言「夹具保持 5 处」） |

### 11.4 loop 簇的正面结论（与预想不同）

| 风险项 | 实测 |
|---|---|
| **包内顶层 import 环** | **0 个** ✓（仅 `engine_adapters → core` 单向） |
| **`__file__` 路径计算** | **0 处** ✓（无阶段 2 那种静默兜底风险） |
| monkeypatch | 2 处（`loop.git_commit`）→ 规则 B 自动改到 `loop.core.git_commit` ✓ |

### 11.5 四轮总表（可复用的迁移方法论）

| 项 | 阶段1 compact | 阶段2 prompt+context | 阶段3 tools | 阶段4 loop |
|---|---|---|---|---|
| 引用改写 | 81 处 / 30 文件 | 79 处 / 38 文件 | 101 处 / 81 文件 | **123 处 / 77 文件** |
| 包级 import 形态 | 20 | 3 | 0 | 5 |
| monkeypatch | 9 | 2 | 0 | 2 |
| `importlib.reload` | 8 | 0 | 0 | 0 |
| `__file__` 路径破坏 | 无 | **3 处 / 10 测试红** | 无 | 无 |
| 源码路径断言(Path 拼接) | — | — | — | **5 测试红** |
| f-string 模板 | 4 组 | 0 | 0 | 5 处 |
| 合成树夹具 | — | — | **2 处** | **5 处** |
| 幂等坑 | 规则B自身 | 规则B命中规则A输出 | **前瞻过度泛化** | 无（规则已成熟） |
| 验证 | 全量绿 | 全量绿 | 全量绿 | 全量绿 |

**沉淀的通用规则（每轮都有新坑，规则持续加固）**：
1. 移动前 grep `__file__` / `parents[` / `.parent` —— 层数敏感路径是**头号隐藏破坏面**
   （症状是静默兜底而非报错）；
2. 移动后 grep 四类引用形态：import 语句 / 字符串路径 / **Path 拼接路径** / f-string 拼接；
3. 替换脚本必须**幂等**（跑两遍，第二遍 0 改动），且前瞻要**精确排除「其他规则的输出形态」**，
   不能笼统排除语法片段；
4. 合成树夹具（自足测试的假路径）必须还原 —— 它们与真文件位置无关；
5. 每批：逐文件 diff 审计 + collect 锚点 + 全量 pytest + batch_gate + 架构图同步。

### 11.6 验证

- 全量 **7596 passed / 106 skipped / 0 failed**（rc=0）
- `batch_gate` **ALL GREEN**（11 步）
- loop 相关 **160 passed**；契约测试（step_contract + architecture_gates）29 passed
- 幂等：第二遍 **0 文件 0 处**
- 旧形态残留 0 / 二次替换污染 0 / 夹具保持 5 处
- `kernel/agent`：106 文件 / 22665 行 / **符号 431 不变、包数 36 不变**

### 11.7 遗留（报告不擅改）

`scripts/` 下 51 处一次性探针脚本（`agent_probe*.py` / `g2_gap.py` / `inventory.py` 等）仍引用
`core/agent/loop.py` 这类**早已不存在的旧包名路径**（`core/agent/` 在 agentnodes 时代就没了）——
这些脚本本来就失效，与本次移动无关，按「发现无关死代码 → 提，不删」处理。

---

## 12 · 剩余 28 个顶层文件的分组规划（2026-11-17 实测；state 归属另行定稿）

### 12.1 现状实测（分组依据）

| 维度 | 数据 |
|---|---|
| 簇内依赖（这 28 个之间） | `event_store→cache/memory_manager/projection_port` · `memory_manager→cache` · `dag_config→plugin/registry` · `registry→plugin` · `executor→dag_config/plugin/registry` · `deciders→_core_types` · `modes→deciders` · `step_adapters→_core_types` |
| 影响面（外部引用次数） | chat_loop 31 · event_store 29 · memory_manager 25 · _core_types 18 · role_factory 17 · plugin 16 · cache 15 · executor 14 · 其余 ≤8 |
| `__file__` 层数敏感 | **3 个**：`redaction.py:105`（parents[2]）· `trace.py:281,283`（parents[2]）· `legacy_audit.py:125`（parents[3]）；`harness_resolver.py:171` 用 `import agentnodes as _sr` → **不受移动影响** |
| 状态簇的环 | `event_store`（顶层）→ `memory_manager`（顶层）→ `event_store`（**函数内**）→ **无模块级初始化环** ✓ |

### 12.2 分组定稿（9 组，28 文件全覆盖）

| 组 | 文件 | 语义 | 簇内依赖 | 影响面 |
|---|---|---|---|---|
| **`kernel/session/state/`** | cache, event_store, memory_manager, projection_port | session 事件、历史与状态投影；从 `kernel/agent` 跨域迁入 | 有环（无顶层环） | 70 |
| **`factory/`** | chat_loop, role_factory, harness_resolver, frontmatter | agent 构造与装配期解析 | role_factory→loop/assembly/toolsets | 56 |
| **`plugins/`** | plugin, registry, dag_config, executor, combo | agent 定义 / 注册 / DAG 编排 / 执行 | 无环 | 48 |
| **`guards/`** | guardrails, text_guard, circuit_breaker, redaction | 守卫与安全 | 无 | 16 |
| **`policy/`** | deciders, modes, step_adapters | 停止/继续判定与模式注册 | deciders→_core_types | 14 |
| **`telemetry/`** | agent_telemetry, trace, legacy_audit | 遥测 / trace 落盘 / 引擎审计 | 无 | 7 |
| **`signals/`** | pause, progress | 挂起恢复 / 进展发射（运行时信号） | 无 | 4 |
| **`tools/`**（阶段 3 已建） | 追加 gateway, exec_registry | 工具网关 / 执行注册表 | 无 | 3 |
| **顶层保留** | `_core_types` | loop 核心抽象类型（11 个簇外依赖方，跨簇共享协议） | — | 18 |

### 12.3 阶段划分（低风险先行，每阶段一个 commit）

| 阶段 | 内容 | 文件 | 影响面 | 特殊事项 |
|---|---|---|---|---|
| **5** | signals(2) + tools 追加(2) + policy(3) | 7 | 21 | 最小批，快速验证方法论 |
| **6** | guards(4) + telemetry(3) | 7 | 23 | **修 3 处 `__file__` 层数敏感**（redaction / trace / legacy_audit → 改包根定位） |
| **7** | plugins(5) + factory(4) | 9 | 104 | factory 内 chat_loop 影响面最大（31） |
| **8** | state(4) → `kernel/session/state/` | 4 | 70 | 跨域迁移；先遵循 §16 明确事实源、装配、投影与缓存契约 |

**阶段 7 分步执行**（每步独立验证、独立提交）：plugins(5) → factory helper（frontmatter / harness_resolver）→ factory 构造模块（role_factory / chat_loop）→ 全量验收与文档回填。

### 12.4 每阶段固定动作（前四轮已验证的方法论，§11.5）

1. **移动前** grep `__file__` / `parents[` / `.parent` → 层数敏感的**先改成按 `agentnodes` 包根定位**；
2. **移动后** grep 四类引用形态：import 语句 / 字符串路径 / **Path 拼接路径** / f-string 拼接；
3. 替换脚本**幂等**（跑两遍，第二遍 0 改动）+ 前瞻精确排除「其他规则的输出形态」+ 合成树夹具还原；
4. 逐文件 `git diff` 审计（变更行必须全是 `from` 行）→ collect 锚点 → 全量 pytest → `batch_gate` → 架构图同步；
5. 本文档回填实测数字。

### 12.5 明确不做

- `_core_types` 留在顶层（11 个簇外依赖方，跨簇共享协议类型；下沉会制造「协议类型藏在某簇里」的耦合）。
- 不改 `guards`/`policy` 等簇内的模块间 import 形态（保持拓扑不变）。
- `state` 簇整体迁入 `kernel/session/state/`，不拆分缓存/事件/记忆的既有依赖环（顶层/函数内分层已验证无初始化环；强行拆开会引入额外抽象）。

## 13 · 阶段 5 实测（2026-11-17）

### 13.1 落地

- 7 个模块按计划归入 `policy/`、`signals/`、`tools/`；新增 `policy/__init__.py` 与 `signals/__init__.py`，扩展既有 `tools/__init__.py`。
- 更新 `src/`、`tests/`、`scripts/` 中的导入和路径引用；旧模块路径扫描为 0。同步修正 `test_step_contract.py` 的源码路径断言，以及挂起恢复测试中的旧路径注释。
- `kernel/agent`：108 文件 / 22,668 行；L3：215 文件 / 48,297 行；全仓：437 文件 / 92,177 行。层包数不变，架构边方向无变化。

### 13.2 验证

- 阶段 5 受影响测试：**161 passed**；工具引用门禁测试：**15 passed**；阶段 5 移动模块 Ruff：**All checks passed**。
- `batch_gate.cmd`：**ALL GREEN**（收集锚点 7,702）；架构图同步检查与 `check_framework_numbers.py` 均通过。
- 全量 pytest（串行）：**7,595 passed / 106 skipped / 1 failed**。唯一失败为 `test_workspace_git_tools.py::TestFoundationGit::test_git_commit_rich_message_format`，不涉及本阶段改动；设置 `PYTHONUTF8=1` 后单测通过。保留为 Windows 编码环境的独立观察项，不在本阶段扩大修复范围。

**结论**：阶段 5 的迁移和架构验收完成；全仓默认环境仍有上述 1 项独立测试失败，不将其记作全量测试全绿。

## 14 · 阶段 6 实测（2026-11-17）

### 14.1 落地

- 7 个模块按计划迁入 `guards/`、`telemetry/`，新增两个包入口；生产代码、测试及当前架构文档引用已同步。
- 将 `redaction`、`trace`、`legacy_audit` 的文件层级路径计算改为从 `agentnodes` 包位置定位，并新增回归测试覆盖包根、runs/sessions 默认目录及仓库根。
- `kernel/agent`：110 文件 / 22,676 行；L3：217 文件 / 48,305 行；全仓：439 文件 / 92,185 行。跨层边计数不变，上行违规为 0。

### 14.2 验证

- 阶段 6 受影响测试：**180 passed**；迁移路径扫描无旧模块引用。
- `batch_gate.cmd`：**ALL GREEN**（11/11，收集锚点 7,705）；`check_framework_numbers.py`、`check_dangling_imports.py --layers` 均通过。
- 全量 pytest（串行）：**7,598 passed / 106 skipped / 1 failed**。唯一失败仍为 `test_workspace_git_tools.py::TestFoundationGit::test_git_commit_rich_message_format`；与本阶段无关，`PYTHONUTF8=1` 下单测通过。
- guards/telemetry Ruff 检查通过（排除已确认的历史诊断）；`telemetry/legacy_audit.py` 基线已有 2 项 C901 与 1 项 F841，本阶段未做无关清理。

**结论**：阶段 6 的迁移和架构门禁完成；全量 pytest 默认 Windows 编码环境仍有 1 项独立失败，不记为全量测试全绿。

## 15 · 阶段 7 实测（2026-11-17）

### 15.1 落地

- 9 个计划模块分别迁入 `plugins/`、`factory/`；新增两个包入口，并同步生产导入、测试导入、monkeypatch 字符串和活动设计文档引用。历史探针脚本中的 `core/agent/...` 旧路径按计划保留。
- 分三批提交：plugins `020e39ba`、factory helpers `a8485491`、factory 构造模块 `5ccdeb47`；最终验收与本文档另行提交。
- L3：219 文件 / 48,319 行；全仓：441 文件 / 92,199 行；跨层边计数不变，上行违规为 0。

### 15.2 验证

- 子批受影响测试：plugins **196 passed / 2 skipped**；factory helpers **157 passed**；factory 构造模块 **168 passed / 8 skipped**。
- 全量 pytest（`PYTHONUTF8=1`、12 workers）：**7,599 passed / 106 skipped / 0 failed**。
- `batch_gate.cmd`：**ALL GREEN**（11/11，收集 7,705）；语义放置 17 项检查通过，旧正式模块导入路径扫描为 0。
- `sync_architecture_diagram.py`、`check_framework_numbers.py` 均通过；迁移测试子集 Ruff 检查通过。全目标文件扫描仍有既存 import 排序/未使用导入告警，本阶段未扩大到无关清理。

**结论**：阶段 7 迁移与最终验收完成；插件、装配辅助与高影响构造模块已按职责收敛到子包。

## 16 · 阶段 8 设计定稿：state 簇边界（2026-11-17）

> **性质**：阶段 8 实施设计；本节记录已确认的目标边界，实际实施进展见 §16.6。

### 16.1 目标包路径

四个模块整体迁入 `kernel/session/state/`：`cache.py`、`event_store.py`、`memory_manager.py`、`projection_port.py`。`Projector`、`ProjectionStore`、`SessionStore` 和 session 生命周期服务继续留在 `kernel/session/`。

选择 `kernel/session/state/`，因为该簇以 session 为主要数据键和生命周期，并与 `kernel/session` 的存储、投影实现协作。拒绝 `kernel/agent/state/`：它会错误暗示这些状态能力属于 agent 私有，且让 session 子域依赖 agent 子域。暂不选更泛化的 `kernel/state/`：尚无证据表明本簇要服务非 session 状态；若未来出现实际跨域消费者，再单独评估。

### 16.2 数据所有权与事实源

本决策只定义会话消息时间线，不把 session 元数据、attempt 等所有状态一并改成事件溯源。

| 数据 / 能力 | 权威者与写入责任 | 约束 |
|---|---|---|
| `event_log` 中的会话事件 | EventStore 拥有事件流语义；PlatformStore 负责底层持久化 | 是会话消息时间线的事实源，可用于重放与投影重建 |
| `messages` / `message_parts` | Projector / ProjectionStore 是唯一常规写入者 | 是可重建的读模型；禁止业务路径绕过投影器直接写入，维护/修复操作须显式标识 |
| session 元数据、attempt、生命周期 | 保持在 `kernel/session` 现有职责内 | 不因 state 簇迁移而转交给 MemoryManager 或 EventStore |
| 进程内缓存与锁 | 各自组件拥有 | 易失、可丢弃，不是持久化事实源 |
| session history 外观 | MemoryManager | `append` 发出事件；`get` 遵循统一的 history 读取契约；`clear` 发边界事件，不代表删除历史 |

不允许 `event_log` 与 `messages` 成为可独立写入的双事实源。读取侧应统一 history 接口：以投影满足常规查询；投影缺失或损坏时，以 event_log 为依据重建或修复。

### 16.3 装配与一致性保证

- API、TUI 和其他入口必须遵循同一套明确的装配规则，确保 EventStore 的投影依赖不是因 fallback 或默认工厂而静默缺失。
- 需要写入投影的模式必须注入 ProjectionSink；缺失时显式报错。若保留 event-only 模式，必须由显式配置启用，并说明该模式下 messages 的可见性与读取保证。
- 默认采用**同步写穿尝试**：EventStore 先持久化事件，再在返回前调用投影 sink。正常时 messages 在本次 emit 返回前刷新；投影失败时 event_log 仍是已持久化事实，失败须记录并可从 event_log 重建，不回滚事件。
- 正常持久化模式下，`append` / `emit` 成功的最低保证是事件已可靠写入 event_log；持久化失败必须向调用方传播。自动修复失败而切到 InMemoryStore 时，成功仅代表进程内可读，`is_degraded` / health 必须表明非持久化。MemoryManager 的 `emergency_*` 返回值同样是带 health 标记的内存降级结果，不是已持久化成功。
- event-only 调用必须明确采用 `flush_to_messages=False`；需要投影的调用必须注入 sink，缺失即构造失败。投影错误、可见性和重建行为均以针对性测试固定。

当前装配缺口需在迁移中覆盖：[API fallback](../../src/agentnodes/interfaces/api/dependencies.py) 未注入 sink；`MemoryManagerFactory` 未暴露 sink 参数；`SessionStore` 的投影读取与 EventStore 注入 sink 使用不同 Projector 实例。迁移不能只改 import 而保留隐式不一致。

### 16.4 缓存、生命周期与删除语义

- 暂不合并为全局缓存。EventStore、MemoryManager、Projector 各自拥有其缓存；组合根负责创建和关闭组件，缓存所属组件负责本地清理。
- session 生命周期操作必须有明确的投影/缓存失效契约；覆盖 EventStore、MemoryManager、Projector 和 SessionStore 的相关缓存。现有 `web_session` 失效逻辑查找 EventStore 不具备的 `invalidate`，阶段 8 需补齐或改由正确所有者承接。
- 语义固定如下：`clear` 追加边界事件，不删除历史；`archive` 是逻辑软归档，保留事件和投影；投影重建只更新可重建读模型；`purge` 是显式永久清理，必须同时处理事件、投影、session 元数据及相关缓存，不得由普通 clear/archive 隐式触发。
- 关闭顺序固定为：容器关闭 MemoryManager、EventStore 和 SessionStore 后，最后关闭共享 Projector；SessionStore 只关闭自己懒创建的 Projector，MemoryManager 关闭其懒创建的 EventStore 与自有 SQLiteBackend，EventStore 关闭 PlatformStore 与事件库连接。TUI 关闭自有 MemoryManager 和 Projector；短生命周期的管理路由必须在 `finally` 中关闭临时 EventStore。

### 16.5 阶段 8 实施门槛

1. 盘点所有入口的 EventStore / MemoryManager 构造路径及 messages 写入路径，确定并测试统一的 history 读写契约。
2. 统一组合根接线；为 sink 缺失、显式 event-only、投影失败/重建补充有正向前提的测试。
3. 明确并验证 §16.3 的投影时序、错误传播与可见性保证。
4. 将四个模块整体迁入 `kernel/session/state/`，按 §12.4 更新生产/测试导入及字符串路径，并保持既有依赖环拓扑。
5. 覆盖 clear、archive、投影重建、purge 的数据边界与缓存失效；完成受影响测试、全量 pytest、`batch_gate`、架构图同步后，回填实测结果。

**回滚边界**：模块迁移和装配/行为契约调整应分批提交；若迁移验证失败，可单独回退路径迁移，不得因此回退已确认的数据所有权文档。

### 16.6 当前实施进展（2026-11-17）

- **装配**：API container 与 fallback providers 共用 Projector；TUI 在应用生命周期内构造并关闭 MemoryManager / Projector；`/clear` 使用注入的 MemoryManager。`flush_to_messages=True` 缺 sink 时显式失败。
- **写入保证**：EventStore 持久化失败现在传播；投影缓存失效通过 EventStore 转发给 sink。
- **迁移**：四个模块已移入 `kernel/session/state/`；生产、测试和脚本引用完成机械更新，路径审计在 `src/`、`tests/`、`scripts/` 未发现旧模块路径。
- **定向验证**：最后重跑的受影响测试集 **118 passed / 1 skipped / 0 failed**。
- **全量验收**：pytest **7605 passed / 106 skipped / 1 failed**；唯一失败为未修改文件 `tests/test_workspace_git_tools.py::TestFoundationGit::test_git_commit_rich_message_format`，断言的假设文本与实际提交信息不一致。`batch_gate.cmd` 全绿；架构图已同步，实测 442 文件 / 92,337 行、上行违规 0。
- **Ruff**：本次涉及的 Python 文件检查报告 60 项，主要为导入排序与未使用导入；未批量自动修复，以免夹带无关清理。

### 16.7 阶段 0 收尾核验（2026-11-18）

- **装配与所有权**：API container 与 fallback providers 共用同一 Projector；容器负责关闭共享实例，SessionStore 不关闭注入实例。TUI 在应用生命周期内创建并关闭自己的 MemoryManager / Projector。
- **写入与读取**：EventStore / MemoryManager 写入 `event_log`，Projector / ProjectionStore 常规写入 `messages` 投影。SessionStore 默认经 Projector 从 `event_log` 读取，异常时回退 `messages` 表；MemoryManager 的 cache miss 直接读 `messages` 表，CLI reader 和部分 web session 查询也直接读该投影。
- **明确的后续边界**：上述读取入口仍未完全汇合；投影失败时，直读 `messages` 的入口可能暂时看到旧投影。`event_log` 仍是事实源；在架构总计划 §12 的“统一会话事实源”阶段收敛读接口、缺失检测与重建行为，不把该工作混入本次 State 路径迁移。
- **生命周期修复**：EventStore 关闭时释放其 PlatformStore 连接；MemoryManager 委托关闭自有 EventStore；`/api/admin/metrics` 在健康检查后关闭临时 EventStore。共享 Projector 仍由容器/TUI 的组合根关闭。
- **验证**：所有已修改测试文件 **335 passed / 1 skipped**；`batch_gate.cmd` **ALL GREEN**；架构图同步检查与框架数字核对均通过。架构统计为 **442 文件 / 92,343 行**，上行违规 **0**。
