# 三步走路线图（v1.0 · 2026-11-06）

> **本文档地位**：仓库**长期定位与三步走路线**的正式记录。
> 区别于 `progress-and-plan-20261106.md`（进度快照 + 批次执行计划），
> 本文回答的是**"这个项目要做成什么，分几步，每步的验收线在哪"**。
>
> **拍板来源**：2026-11-06 用户明确表态——
> 「做成一个通用 agent，并且后续加入 RSI 后可以自我进化，同时配合我的
> 另外一个项目（llmwikify）可以接入各种各样知识完成一个真正的人工智能
> 操作系统平台；量化研究只是其中的一个未来发展方向之一；我觉得现在分
> 三步走，先搭建一个通用平台+插件，第二步就是 RSI，第三步，添加核心工具包
> + 知识库 = 特质 agent，进行多样化发展。」

---

## §0 一句话

> **通用 Agent 平台 + RSI 自我进化 + 知识库接入 = 人工智能操作系统平台。**

量化研究不是内核，是**第一个可选的应用方向**。

---

## §1 为什么是这个比喻：操作系统

「人工智能操作系统」不是修辞，它对应四件可检查的事：

| 操作系统概念 | 本项目对应 | 判据 |
|---|---|---|
| **内核** | `kernel/`（L3）— 执行循环、图引擎、工具注册、权限、事件、进化机制 | **不含任何业务域名词** |
| **应用/软件** | `apps/`（L2）— goal 长程研究、量化插件包、未来的其他领域 | 可整目录删除，内核仍能启动 |
| **设备驱动/系统调用** | `CapabilityRegistry`（ADR-003 端口化）| kernel 只认 `kind` 接口，不认实现 |
| **文件系统/知识层** | `llmwikify`（外部项目）| 通过端口接入，不是内核内置 |
| **标准库** | `TOOLSETS` 的通用集合 + `builtin_tools` | 平台无关、任何 agent 都可能用到 |

**最硬的一条判据**：

> 删掉整个 `apps/` 目录，`kernel` 仍能启动并跑一个通用对话 agent。
>
> 这就是「内核」的定义。Linux kernel 不认识 Firefox，但能运行它。

---

## §2 三步走总览

| 步 | 目标 | 一句话验收 | 状态 |
|---|---|---|---|
| **第一步** | **通用平台 + 插件** | 配置层每个引用都能解析；kernel 零业务名词 | 🔄 进行中（4a/4b 已完成）|
| **第二步** | **RSI 自我进化** | 进化循环跑在干净地基上，双门可判真假 | ⏸ 部分前置于第一步（见 §4）|
| **第三步** | **核心工具包 + 知识库 = 特质 agent** | 一个领域专用 agent 能端到端跑通 | ⏸ 素材已备（§5）|

**顺序不能反的原因**：RSI 是「让系统变好」的机制。如果系统连
「配置的工具有没有实现」都不保证，就没有干净基线判断 RSI 到底是
改好了还是改坏了。**先有可测的地基，才有可信的进化。**

---

## §3 第一步 · 通用平台 + 插件

### 3.1 目标

> **kernel 只回答「怎么执行」，不回答「执行什么业务」。**

### 3.2 验收标准（可测，不靠感觉）

四条全部机器可验证：

| # | 标准 | 验证方式 |
|---|---|---|
| **A1** | 配置层每个工具引用都能解析到真实实现 | `check_tool_refs.py` 零 FAIL |
| **A2** | `kernel/` 不含业务域名词 | `check_semantics_placement.py` 扩充后零 FAIL |
| **A3** | 每个插件的声明都能兑现（工具 + 执行器 + 提示词）| 三层校验零误判 |
| **A4** | 空系统能跑通 | 无任何插件时，`chat` / `explorer` / `coder` 可用 |

### 3.3 定义「业务名词」的边界

**这一条最容易含糊，必须写死。**

| 属于 kernel（允许）| 属于 apps（禁止出现在 kernel）|
|---|---|
| `read` / `write` / `list` / `shell` / `git_diff` | `factor_analysis` / `compute_factor` |
| `websearch` / `webfetch` / `read_document` | `get_market_data` / `search_symbol` |
| `task` / `skill` / `todowrite` / `tool_help` | `run_backtest` / `strategy_compare` |
| `show_chart` / `show_report`（通用可视化）| `check_data` / `clean_data` |
| 「研究」「分析」「执行」等**过程**词汇 | 「因子」「回测」「组合」「归因」等**领域**词汇 |

**判据**：这个词能否用于法律 / 医学 / 金融 / 代码任意一个领域？
能 → 通用；只对某一个领域有意义 → 业务。

### 3.4 已完成（批次 4a / 4b）

| 项 | 结果 | commit |
|---|---|---|
| `TOOLSETS` 收缩 | 16 集合 → **8 个通用**；悬空 **32 → 0** | `5f040984` |
| 插件迁 L2 | `kernel/agent/builtin_plugins.py`（已删除）→ `apps/agent_plugins/quant.py`（17 个插件定义完整保留）| `5f040984` |
| kernel 默认注册表 | 改为**空表** + `reset_default_registry()` | `5f040984` |
| 三层插件校验 | `tools` + `python_function` + `prompt_file`；可用插件 5 → 3 | `5f040984` |
| **F1** tool_set 形状 | 双侧修复（产出 + 读取），老库兼容 | `4c275de9` |
| **F2** anchor 式回滚 | 三连败不再回退已批准的好版本 | `4c275de9` |

### 3.5 剩余（批次 4c）

| 项 | 内容 | 风险 |
|---|---|---|
| C1 | 新建 `check_tool_refs.py`，纳入 `batch_gate` 第 7 步 | 低 |
| C2 | `templates/workflows/` 两个悬空模板处置 | 低 |
| C3 | **kernel 业务名词清零**（8 处）| **中高** — 涉及权限枚举 |
| C4 | `role_factory._ROLE_PROMPT_FILES`（14 个业务角色映射）迁 L2 | 中 |
| C5 | `apps/goal/workflow_config.py` 的 9-agent 拓扑标 pending | 中 |

**C3 的特殊性**：`permission/schema.py:47-50` 的 `RUN_BACKTEST` /
`GET_MARKET_DATA` / `COMPUTE_FACTOR` 是**权限判定的枚举成员**。删掉意味着
旧的 permission 配置（`~/.agentnodes/permissions.*`）里的这些工具名失效。
**这不是纯清理，是一次兼容性变更**，需要迁移方案而不是直接删。

### 3.6 RSI 归属（已拍板：机制留 L3，内容迁 L2）

| 留 `kernel/evolution/`（L3 机制）| 迁 `apps/`（L2 内容）|
|---|---|
| `ValidationGate`（双门判定逻辑）| `golden_tasks.jsonl`（70 条量化任务）|
| `GenomeVersionStore`（版本链状态机）| `default_policy` 的启发式（阈值/工具集）|
| `SkillMemoryResolver`（Tier 1 只读解析）| held-out / held-in 数据集 |
| `GenomeProposal`（Tier 检查）| `golden_eval` 的具体任务定义 |
| `HoldoutSet` / `Trajectory`（数据结构）| 评估指标的具体计算 |

**判据**：kernel 只认**接口**（Protocol），不认**内容**（具体任务 / 阈值 / 工具名）。

---

## §4 第二步 · RSI 自我进化

### 4.1 目标

> **进化循环跑在干净地基上，双门可判真假，成果可追溯、可回滚。**

### 4.2 为什么部分工作和第一步重叠

RSI 的三条闸门（W1 能力自检 / W2 成本双闸 / W3 双集双门）已经在
2026-11-06 之前的批次建完了——**因为它们本身就是"让地基可测"的机制**。
这是有意的重叠：**闸门机制是地基的一部分，不是进化的一部分。**

真正属于第二步的是「把进化循环**跑起来**」：

| 项 | 内容 | 状态 |
|---|---|---|
| R3 | journal/scoreboard 端口接入 + autoresearch preset 化 | ⏸ 待第一步完成 |
| W5 | 假基因清理（零使用覆盖判定 + RSI 末尾 + 独立 CLI 双入口）| ⏸ 已拍板口径 |
| W4 收尾 | 评估完整性指纹 + 回放状态隔离 | ⏸ 指纹 P1，密封子进程 P2 |
| W6 | 事件 hash chain | ⏸ P2 |

### 4.3 「日常无感，犯错才体现」（已拍板，双向）

> 用户原话：「RSI 和日常使用理论上应该不冲突的；或者在平时的时候应该完全
> 无感 RSI，只有犯错的时候，能够体现出来。」

| 方向 | 含义 |
|---|---|
| **日常犯错时，RSI 暗中修** | RSI 平时隐身；日常出现失败 / 判据未达成 / 退化时才触发诊断与修正 |
| **RSI 犯错时，才看得到它** | RSI 自己出问题（双门分歧 / 回滚 / 清扫误伤）才在日常可见面告警 |

### 4.4 止血分层（依据四路调研，待最终确认）

调研结论：**业界没有任何一家用全局单一 kill-switch**；SRE 的「什么证据
足以全停」只有三种（数据完整性 / 维护窗口 / 自身燃烧率），**RSI 故障不在其列**。

| 层 | 动作 | 影响面 |
|---|---|---|
| **L1 回滚** | `rollback_to(anchor)` 回上一个过双门的版本 | 只影响下一次装配；in-flight 不受影响 |
| **L2 停循环** | 停 RSI（`scope=rsi`）| RSI 自己停，日常无感 |
| **L3 告警** | 穿透到日常可见面 | 人来看（N4 的唯一例外）|
| **L4 全停** | `scope=all` + 安全态 | 仅：证据链被污染 / 人工 `/halt` / 日常自身燃烧率 |

**未定的三点**：
1. 单次分歧是否只 trip `rsi` 域（倾向：是）
2. 连续 N 次升级的 N（倾向：用 Thresholdout 分歧预算替代拍脑袋的 N）
3. 现行基因组健康检查（held-out 体检）排期

---

## §5 第三步 · 核心工具包 + 知识库 = 特质 agent

### 5.1 目标

> **一个领域专用 agent 能端到端跑通：有专业工具，有领域知识。**

### 5.2 素材已备：3,091 行 / 21 个工具

批次 1 删除的 5 个文件**全部可从 git 恢复**，且**天生就是插件形态**
（内部通过 `resolve("factor.compute")` 等 capability 端口取依赖）：

| 文件 | 行数 | 工具 |
|---|---|---|
| `factor_tools.py` | 1,164 | `compute_factor` · `factor_analysis` · `pattern_recognition` · `factor_cross_sectional_analysis` · `factor_quintile_returns` · `factor_ic_decay` · `factor_turnover` |
| `data_tools.py` | 790 | `get_market_data` · `list_data_sources` · `search_symbol` · `import_data` · `check_data` |
| `backtest_tools.py` | 513 | `run_backtest` · `config_load` · `data_prepare` · `data_gate` · `engine_run` |
| `backtest_analysis_tools.py` | 450 | `strategy_compare` · `drawdown_analysis` · `benchmark_comparison` |
| `data_clean_tools.py` | 174 | `clean_data` |
| **合计** | **3,091** | **21 个工具** |

**恢复锚**：`git show "9e2432de^:src/agentnodes/apps/builtin_tools/<file>"`
（工具名清单同时记在 `apps/agent_plugins/quant.py::PENDING_TOOLS`）

> **注意**：上表 5 个文件**当前不在工作树里**（批次 1 已删除），只能通过
> 上面的 `git show` 取出。本文档按「历史文件名」引用它们，不是现存路径。

### 5.3 恢复时还需要什么

工具实现本身只是壳，它们依赖的**底层 capability** 在批次 4/5 也被直删了：

| 缺失的 kind | 提供者 | 考古锚 |
|---|---|---|
| `factor.compute` / `factor.compute_error` / `factor.long_to_wide` | 因子计算引擎 | `git show cd1e79d9^:src/strategy_research/apps/...` |
| `db.get_connection` / `db.init_db` / `db.save_ohlcv_to_db` | DuckDB 连接 | 同上（数据域）|
| `datasource.registry` / `datasource.base` / `datasource.detect_market` | 数据源抽象 | 同上 |
| `data.readiness` / `data.clean` | 数据质量 | 同上 |
| `backtest.from_yaml` / `backtest.script` / `backtest.decide` / `config.runner` | 回测引擎 | `git show 00548ccd^:src/strategy_research/apps/...` |

**这一步的工作量主要在补 capability，不在恢复工具文件。**

### 5.4 知识库接入（llmwikify）

| 事项 | 说明 |
|---|---|
| 接入方式 | 通过 `CapabilityRegistry` 端口（如 `knowledge.search` / `knowledge.load`），kernel 不直接依赖 |
| 与 `skill` / `list_skills` 的关系 | `templates/.skills/` 是静态技能包；llmwikify 是动态知识库，两者互补 |
| 待定 | 接口形态、检索粒度、如何参与 RSI 的 `skill_set` 提案 |

---

## §6 三条跨阶段原则

### 6.1 Graph 的边只在 Loop 踩过的地方画

不为想象中的需求加抽象。每一步的产出都必须有**实测证据**支撑
（file:line 出处、探针输出、测试断言）。

### 6.2 配置即契约，契约必须有门禁

| 配置面 | 门禁 | 状态 |
|---|---|---|
| `CAPABILITIES` 的 `requires` | `check_capability_requires.py`（AST 双向 diff）| ✅ |
| 语义轴落位 | `check_semantics_placement.py` | ✅ |
| 层方向 / 悬空 import | `batch_gate` 第 2 步 | ✅ |
| 路径字面量 | `batch_gate` 第 3 步 | ✅ |
| **工具名字符串** | **`check_tool_refs.py`**（待建）| ⬜ **待建（4c）** |

**这条原则的由来**：同一个病发作过两次——
第一次是 `CapabilityRegistry` 里声明了 kind 却没有实现（W1 解决），
第二次是配置表里写了工具名却没有实现（56 处悬空）。**换了个数据结构，
病就复发了。** 所以必须逐个配置面建门禁。

### 6.3 空集合不是「通过」，是「没有可检查项」

**这是 4a 修复过程中实测抓到的教训**：

```
backtest  tools=()  → 判定 AVAILABLE   ← 错
decide    tools=()  → 判定 AVAILABLE   ← 错
实测：list_python_executors() == []，两个插件声明的执行器根本不存在
```

`tools=()` 的插件「没有悬空引用」，恰恰是最该拦的——它声明了执行器，
但执行器不存在。**"没有可检查项"必须单独判定，不能默认通过。**

---

## §7 与旧文档的关系

| 文档 | 定位 | 与本文件的关系 |
|---|---|---|
| `framework-and-principles-20261105.md` | v1.1 框架与七条理念 + D1–D6 决策 | **上位文档**：理念不变，批次顺序被本文取代 |
| `progress-and-plan-20261106.md` | 进度快照 + 批次执行计划 | **执行层**：本文定方向，它记进度 |
| `industry-survey-knowledge-base-20261106.md` | 四路调研知识库 | **依据**：第二步止血分层的调研出处 |
| `architecture-consistency-scan-20261105.md` | 一致性扫描（56 处悬空引用的原始出处）| **证据** |
| 本文 | **长期路线图** | — |

**版本沿革**：`refactor/v6-gateway` 在 `5f040984` 打 tag `v6-pre-platform`，
新分支 `platform-kernel` 从该点继续（批次 4b 起）。

---

## §8 附：术语对照

| 本文术语 | 含义 | 常见误解 |
|---|---|---|
| **kernel / 内核** | `kernel/` 目录（L3），通用执行机制 | ≠ 整个仓库 |
| **插件** | `apps/` 下的业务域包（含 agent 插件、工具、preset）| ≠ 第三方扩展 |
| **toolset** | 工具白名单的分类（`minimal` / `chat` / `coder` …）| ≠ 插件 |
| **tool** | 单个可调用工具（`read` / `shell` …）| ≠ capability |
| **capability / kind** | 端口化的能力标识（`goal.store` / `db.get_connection`）| ≠ tool |
| **Tier 0/1/2** | 基因组可变性分级（冻结 / 需验证 / 研究产物）| ≠ 插件分层 |
| **假基因** | 被 activate 后从未被任何 run 踩过的 Tier 1 组件 | ≠ 未启用组件 |

**最容易混的一组**：`tool` → `toolset` → `plugin` → `capability` 是四个
不同的抽象层，各自有自己的门禁与消费者。
