# 进度与规划 · 2026-11-06

> **本文档地位**：本轮回合（批次 1–3 执行后）的**状态快照 + 规划重置点**。
> 上一份规划是 `framework-and-principles-20261105.md` §3.3 的批次表（v1.1）；
> **本文档修正该表的批次 4 及之后安排**，并把新实测出的基础框架缺口列为前置。
>
> 所有数字来自本轮实测（探针脚本与命令见各节「证据」栏），不引用旧文档数字。

---

## §1 一句话现状

**RSI 的三条闸门（W1 能力自检 / W2 成本双闸 / W3 双集双门）已建成，
但闸门的下游是断的**：F1 让通过的提案把工具集清零，F2 让三连败回退掉上一个
已批准的好版本；而基础框架本身存在 **56 处悬空工具引用**（实测算术见 §3）。
在此之上继续做 RSI 迭代（R3 / W5）是在坏地基上加层。

---

## §2 已完成的三个批次

### 批次 1 · `9e2432de` — W1 能力自检

| 交付 | 位置 |
|---|---|
| `CapabilityRegistry.register(requires=...)` + 两阶段装配 + `_promote_skipped` | `kernel/agent/capability/registry.py` |
| `check_capability_requires.py`（AST 双向 diff，欠声明/过声明均 FAIL，`--bootstrap` / `--selftest`）| `scripts/arch/` |
| `check_semantics_placement.py` + `semantics_placement.yaml`（语义轴静态门禁，15 条）| `scripts/arch/` |
| `batch_gate.cmd`（6 步，ASCII-only） | `scripts/` |
| 删除 5 组永不注册的业务工具，3,091 行 | — |

**实测结果**：幽灵工具 44 → 18，零特殊分支。

### 批次 2 · `bf7163ac` — R1 goal 恢复 ∥ W4 RSI 驱动链

| 泳道 | 交付 |
|---|---|
| R1 | `apps/goal/` 恢复 18 文件；3 个 kind（`goal.store` / `goal.models` / `goal.context`，requires 均 ∅）；27 个测试搬回（511 passed） |
| W4 | A3 golden 路径修复（`parents[4]` → `parents[1]`，框架第一版起就有的 bug）；task_signature 降维（5 个封闭意图桶）；`skip_monitor.py`；`interfaces/rsi_cli.py` 三道门（halt → 预算 session≤1/daily≤3 → 证据增量）；halt 持久化 `~/.agentnodes/halt.json`；F7 基线滚动中位数 + 按 genome_version 分区失效 |

**W4 未完成项见 §4。**

### 批次 3 · `788d0139` — R2 chat 接线 ∥ W2 成本双闸 ∥ W3 双集双门

| 泳道 | 交付 |
|---|---|
| R2 | 5 个 L1 文件恢复；A5 双头环境变量统一（规范名 `AGENTNODES_GOAL_DB_PATH`）；10 个测试搬回（131 passed） |
| W2 | `RunRecord.duration_ms`、`TaskScore.cost`；`normalized_cost()`；成本闸 0.30 硬拒 / 0.15 escalate / MIN_COST_SAMPLES=3；**禁混合均值**；自修三个隐形单点 |
| W3 | sha256 分桶 47 held-in / 23 held-out，零重叠；held-out 进程外持有 `~/.agentnodes/eval_heldout/`；双门 + 分歧 kill-switch；三态探针全过 |

**验证基线**：6834 passed / 71 skipped / 0 failed；batch_gate ALL GREEN；collect 6,905。

---

## §3 新实测：基础框架的悬空引用（本轮核心发现）

### 3.1 工具真相

```
注册工具（build_default_registry）: 18
  git_diff, list, list_history, list_skills, options_pricing, read,
  read_document, run_bg_command, shell, show_chart, show_report,
  skill, task, todowrite, tool_help, webfetch, websearch, write
```

### 3.2 悬空引用统计

| 配置面 | 消费者（活代码） | 悬空数 |
|---|---|---|
| `kernel/agent/toolsets.py::TOOLSETS` | `role_factory.py:52`（`as _ROLE_TOOL_WHITELIST`）| **32 处 / 12 个集合（16 个中）** |
| `kernel/agent/builtin_plugins.py::BUILTIN_PLUGINS` | `registry.py:11,100`（预加载成默认插件注册表）| **24 处 / 10 个插件** |
| `templates/workflows/alpha_research.json` | 模板加载器 | 整个模板 4 节点全废 |
| `templates/workflows/data_quality_audit.json` | 同上 | `check_data` 不存在 |
| `apps/goal/workflow_config.py::build_autoresearch_workflow_config` | goal 工作流 | 5 个工具 + `run_backtest_script` |
| `evals/nightly-baseline.json` | 基线快照（**历史资产，不该改**）| 记录着已删工具 |

**合计悬空引用：56 处**（32 + 24；模板与 preset 另计）。

### 3.3 具体缺哪些工具

TOOLSETS 里被引用但**不存在**的（去重后）：

```
benchmark_comparison, check_data, clean_data, compute_factor,
drawdown_analysis, factor_analysis, factor_cross_sectional_analysis,
factor_ic_decay, factor_quintile_returns, factor_turnover,
get_market_data, list_data_sources, list_goals, run_backtest,
search_symbol, strategy_compare
```

—— **全部是量化域工具**，正是批次 1 删除的 5 组。

### 3.4 为什么 W1 没抓到

W1 的自检对象是 `CapabilityRegistry` 的 **kind → factory** 关系。
`TOOLSETS` 与 `BUILTIN_PLUGINS` 里的工具名是**字符串字面量**，
不经过 registry 的注册路径，因此 W1 的 `requires` 机制结构性看不见它们。

> **这是同一类病的第二次发作，换了个数据结构。**
> 第一次：`CapabilityRegistry` 里 kind 声明了却没有实现 → W1 解决。
> 第二次：配置表里写了工具名却没有实现 → **无门禁**。

---

## §4 W4 交付审计（批次 2 声称 vs 实测）

| W4 验收条款（framework-and-principles §3.3）| 实测 | 状态 |
|---|---|---|
| A3 golden 路径修复 | `rsi_wiring` 已改，`tasks loaded: 70` | ✅ |
| task_signature 降维 + 进 report payload | `holdout.py` v2 口径 + `ticket_store` 注入 | ✅ |
| skip 聚合 + 限频 | `skip_monitor.py` | ✅ |
| CLI 三道门 + 状态落盘 | `rsi_cli.py` | ✅ |
| halt 持久化 | `halt.py` | ✅ |
| **F1 tool_set 形状契约断裂** | **仍活着（§5.1 实测）** | ❌ |
| **F2 三连败回滚落靶** | **仍活着（§5.2 实测）** | ❌ |
| F7 基线缓存失效 | `rsi_wiring.py:440-493` 滚动中位数 + 版本分区 | ✅ |
| **评估完整性指纹（pin 全集 + 常量包外比对 + 密封子进程）** | `fingerprint` / `manifest` / `subprocess` **零命中** | ❌ |
| **回放状态隔离** | 未见独立实现 | ❌ |
| **cron 驱动** | 无；文档 §409 也只把阶段 1 定义为手动 CLI | ❌（口径待定）|

**结论**：W4 交付约一半。F1/F2 两项 P1 地雷未修，指纹与密封子进程未做。

---

## §5 两个仍活着的 P1 bug（实测证据）

### 5.1 F1 · tool_set 形状契约断裂

```
$ python -c "...default_policy 产出..."
proposal.change    = {'tools': ['read', 'list']}
tier1_active       = {'tool_set': {'tools': ['read', 'list']}}
frozenset(raw)     = frozenset({'tools'})          ← 只剩字面量 "tools"
```

链路：

```
default_policy.py:68,76  产出 {"tools": [...]}
  → validation_gate.activate() 原样写入 tier1_active["tool_set"]
  → skill_memory.py:108  frozenset(raw_tools) → {'tools'}
  → harness_resolver.py:83  allowed = {'tools'}
  → apply_tool_set: [t for t in available if t in allowed] → []  ← 工具清零
```

**影响**：任何一次通过的 `tool_set` 提案，都会让下一轮装配的工具集变成空。
这使 W3 的双门形同虚设——过了闸反而更糟。

### 5.2 F2 · 三连败回退掉上一个已批准版本

```
$ python tmp_probe_f2.py
initial: gv-0 {}
after good-1: gv-c7a08b59 {'prompt': {'v': 1}}
  fail#1: status=rejected     genome=gv-c7a08b59
  fail#2: status=rejected     genome=gv-c7a08b59
  fail#3: status=rolled_back  genome=gv-0  ← 好版本被回退
FINAL: gv-0 {}
```

`_rollback`（`validation_gate.py:990-1005`）调 `genome.rollback()` =
盲目 `cursor−1`，回退的是**上一个已批准的合法版本**，而非「候选的临时状态」。

**影响**：评审 F2 原本点名的「退化不可逆」，至今未修。

---

## §6 为什么必须先修基础框架再做 RSI

1. **没有干净基线**：配置层本就 56 处悬空，RSI 的改动无法与噪声区分
2. **F1 会主动破坏**：通过的提案清零工具集，而当前工具集本就大半是空的 → 破坏不可见
3. **F2 会误伤**：回退到上一个版本，而上一个版本可能本就引用已删工具
4. **R3 直接踩 F1**：R3 要接入 journal/scoreboard 端口喂 `default_policy`，
   而 policy 产出的正是 `{"tools": [...]}` 形状 → 接上就触发 F1
5. **W5 直接踩 F2**：W5 要调 `deactivate`，而 `_rollback` 的 cursor 语义未修 → 清理会误伤

---

## §7 修订后的规划

### 7.0 仓库定位（2026-11-06 用户拍板 · 三步走）

> **目标：通用 Agent 平台 + RSI 自我进化 + 知识库接入（llmwikify）= 人工智能操作系统平台。**
> 量化研究只是未来发展方向之一，不是内核的一部分。
>
> **三步走（用户原话）**：先搭建一个通用平台 + 插件；第二步就是 RSI；
> 第三步，添加核心工具包 + 知识库 = 特质 agent，进行多样化发展。

| 步 | 目标 | 内容 |
|---|---|---|
| **第一步** | **通用平台 + 插件** | kernel 零业务名词；工具、拓扑、agent 定义全部作为插件 |
| **第二步** | **RSI 自我进化** | 在稳定平台上接入进化机制，有干净基线可测 |
| **第三步** | **核心工具包 + 知识库 = 特质 agent** | 量化工具包、llmwikify 知识层接入，形成领域专用 agent |

**这个定位直接决定了 56 处悬空引用的处置方向**：不是「恢复量化工具」，
而是「把这些工具从 kernel 搬到插件域」——它们是**第三步的核心工具包素材**，
不是内核内容。

**好消息**：批次 1 删除的 5 个文件 **3,091 行 / 21 个工具全部可从 git 恢复**
（`git show "9e2432de^:<path>"`），且它们的实现方式**天生就是插件形态**——
内部通过 `resolve("factor.compute")` / `resolve("db.get_connection")` 等
capability 端口取依赖，不直接 import 具体实现。这使它们搬到 L2 插件域
几乎不需要改造。

实测清单：

| 文件 | 行数 | 工具 |
|---|---|---|
| `factor_tools.py` | 1,164 | compute_factor, factor_analysis, pattern_recognition, factor_cross_sectional_analysis, factor_quintile_returns, factor_ic_decay, factor_turnover |
| `data_tools.py` | 790 | get_market_data, list_data_sources, search_symbol, import_data, check_data |
| `backtest_tools.py` | 513 | run_backtest, config_load, data_prepare, data_gate, engine_run |
| `backtest_analysis_tools.py` | 450 | strategy_compare, drawdown_analysis, benchmark_comparison |
| `data_clean_tools.py` | 174 | clean_data |
| **合计** | **3,091** | **21 个工具** |

### 7.0.1 验收标准（已拍板）

> **基础框架完成 = 配置层的每一个字符串引用都能解析到真实实现，且这件事有门禁守着；
> `kernel/` 不含任何业务域名词。**

可测、可门禁化，不靠「感觉完整了」。

### 7.1 新的批次顺序

| 批次 | 内容 | 理由 |
|---|---|---|
| **4（新）** | **基础框架收口**：悬空引用清零（56 处）+ 新门禁 `check_tool_refs.py` + F1 + F2 | 先让地基可测 |
| **5** | R3（journal/scoreboard 端口接入 + autoresearch preset 化）∥ W5（假基因清理）| 原批次 4，依赖新批次 4 |
| **6** | W4 收尾：评估完整性指纹 + 回放状态隔离（+ cron 若决定要）| 原 W4 未完成项 |
| **P2** | W6 事件 hash chain；密封子进程；调研材料落档 | 非阻塞 |

### 7.2 批次 4 的内容（按三步走定位）

**A. 悬空引用清零**

- `TOOLSETS`（32 处）与 `BUILTIN_PLUGINS`（24 处）里的量化域名词：
  **收缩为通用能力集**，量化工具移入「待恢复的插件包」清单（第三步用）
- `templates/workflows/*.json` 两个模板：改为通用模板或删除
- `apps/goal/workflow_config.py::build_autoresearch_workflow_config`：R3 会删它
- `evals/nightly-baseline.json`：历史快照，**豁免但标注**

**B. 新门禁 `check_tool_refs.py`**

覆盖四个配置面：`TOOLSETS` · `BUILTIN_PLUGINS.tools` · `templates/workflows/*.json`
· 插件定义。与 `check_capability_requires.py` 同构（AST 提取 + 双向 diff + `--selftest`），
纳入 `batch_gate.cmd` 作为第 7 步。

**C. F1 + F2 修复**（§5 两个 P1 bug）

**D. `kernel/` 业务名词清零**

`check_semantics_placement.py` 已有语义轴检查能力，扩充为同时检查
`kernel/` 下的业务域名词（量化、回测、因子等）。

### 7.3 RSI 归属（已拍板：机制留 L3，内容迁 L2）

| 留 `kernel/evolution/`（L3 机制）| 迁 `apps/`（L2 内容）|
|---|---|
| `ValidationGate`（双门判定逻辑）| `golden_tasks.jsonl`（70 条量化任务）|
| `GenomeVersionStore`（版本链状态机）| `default_policy` 的启发式（阈值/工具集）|
| `SkillMemoryResolver`（Tier 1 只读解析）| held-out / held-in 数据集|
| `GenomeProposal`（Tier 检查）| `golden_eval` 的具体任务定义 |
| `HoldoutSet` / `Trajectory`（数据结构）| 评估指标的具体计算 |

**判据**：kernel 只认**接口**（Protocol），不认**内容**（具体任务/阈值/工具名）。

### 7.4 第三步的素材（已确认可用）

批次 1 删除的 3,091 行 / 21 个工具是第三步「核心工具包」的现成素材：

- 它们的实现通过 `resolve("factor.compute")` 等 capability 端口取依赖
- 搬到 `apps/` 或独立插件包时，只需补齐 `factor.compute` / `db.get_connection`
  等底层 capability 的实现
- 这也反过来验证了 W1 端口化设计的正确性：**业务工具从第一天起就是按插件写的**

---

## §8 待决问题（部分已拍板）

| # | 问题 | 状态 |
|---|---|---|
| Q1 | 56 处悬空引用怎么处置 | ✅ **已定**：按「通用平台 + 插件」定位，收缩为通用能力集，量化工具移入第三步插件清单 |
| Q2 | `role_factory` 与 `registry` 两个消费者怎么办 | ✅ **随 Q1**：降级到真实工具，不恢复业务工具到 kernel |
| Q3 | 新门禁现在建吗 | ✅ **现在建**（否则重复发作）|
| Q4 | W4 的指纹与密封子进程还做不做 | ⬜ **待定**：倾向指纹做（P1 防基线投毒），密封子进程降 P2 |
| Q5 | F1 修法 | ⬜ **待定**：倾向两侧都改（已入库 genome 可能已有坏形状）|
| Q6 | F2 修法 | ⬜ **待定**：倾向 anchor 式（激活前记锚，评审原方案）|
| Q7 | cron 驱动要不要 | ⬜ **待定**：倾向后置（先手动 CLI 跑稳）|

---

## §9 已确认的口径（本轮拍板，2026-11-06）

| # | 裁决 | 来源 |
|---|---|---|
| 1 | **W5 假基因判定 = 零使用覆盖**（Tier 1 组件被 activate 后从未被任何 run 踩过 → 假基因）| 用户拍板 |
| 2 | **W5 触发 = RSI cycle 末尾 + 独立 CLI 两个入口** | 用户拍板 |
| 3 | **架构图 `--write` 超范围写入 = 接受**（走既定转正通道，内容是 AST 实测回写）| 用户拍板 |
| 4 | **W2 的 cost 饱和边界写进 D3 记录**（`cost_saturated=true` 让失明可见，不改公式）| 用户拍板 |
| 5 | **「日常无感，犯错才体现」= 双向**：日常犯错时 RSI 暗中修 + RSI 犯错时才看得见 | 用户拍板 |
| 6 | **halt 分域语义仍待深入讨论**（单次分歧是否只 trip `rsi` 域）| 未定 |

---

## §10 调研材料（未落档）

四路调研已完成（停止机制分域 / 行为变更安全 / reward hacking 与评估卫生 / 熔断与 SRE），
含大量可引用出处与 6 处明确标注「查不到/未核实」。

核心结论（详见对话记录，待落档为 `docs/design/industry-survey-rsi-safety-20261106.md`）：

1. **没有任何一家产品用全局单一 kill-switch**；共识是 3–4 个取消域，父子单向向下传播
2. **业界没有「会话级版本钉住」的一等公民特性**——我们的 `harness_resolver`
   「只在装配期问一次」正好是缺的那个原语
3. **双门的成熟对应物 = Thresholdout**（Dwork et al., Science 2015 / arXiv:1506.02629）：
   分歧时扣过拟合预算，预算耗尽停止回答
4. **SRE 的「什么证据足以全停」只有三种**（数据完整性 / 维护窗口 / 自身 page 级燃烧率），
   RSI 故障**不在其列**；正确动作是回滚 + 停 RSI，不是停日常
5. 术语校正：用 **OEC + guardrail metrics**，**不要用 counterfactual metric**（非标准术语）

---

## 附：探针脚本

| 脚本 | 用途 |
|---|---|
| `tmp_probe_tools.py` | 统计 TOOLSETS / BUILTIN_PLUGINS 悬空引用 |
| `tmp_probe_f2.py` | 复现 F2 三连败回退掉好版本 |
| （inline）| 复现 F1 `frozenset({'tools':[...]})` == `{'tools'}` |

> 三个脚本均为临时探针，未提交。正式化时迁入 `scripts/arch/` 或测试。
