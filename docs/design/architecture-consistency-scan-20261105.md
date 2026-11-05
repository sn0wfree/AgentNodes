# 架构一致性扫描（2026-11-05）— 架构图 · 设计文档 · 代码实况

> 触发：架构讨论前的信息收集。**本文档只记录实测与不一致，不做任何代码改动。**
>
> 采集方式：graphify AST 全量图（43,132 节点 / 80,539 边）+ 语句级层门禁 +
> 运行期探针（真实 `resolve()` / 真实工具调用）。所有数字都可用文末命令复现。

---

## §0 结论摘要

五条判断，按严重度排序：

1. **端口化的降级语义在「注册层成功、能力层缺失」时失效。** 26 个业务工具被
   注册进 agent 工具表，其中至少 7 个一旦调用必然 `CapabilityNotFound`。
   注释承诺的「kind 缺失就静默跳过，比注册一个调用必然失败的工具好」——
   实际发生的正是后者。这是本轮最深的架构缺陷，**不是过时注释**。
2. **RSI 闭环从未真正闭合。** `build_rsi_loop()` 生产代码零调用；即使被调用，
   golden eval 路径写错一级（`parents[4]` → `D:\Github\evals\`），永远降级到
   代理分。两处叠加使「自我进化」目前是纯装饰。
3. **架构图 60% 的文案是手写的，机器不同步。** 四层徽标/边数/违规数由
   `sync_architecture_diagram.py` 回写（当前全绿），但 L2 四个业务域、L3 三个
   子带、L4 六个模块、能力 kind 数、工具数全部手写 → 已过期而不报警。
   最大偏差：L4 utils 图标 1.9k，实测 483 行（虚高 4 倍）。
4. **L2 事实上已经空了。** 4,489 行里 3,684 行是 `builtin_tools/`（全是调用
   已删能力的壳），222 行 chat 策略，283 行根文件。图上的「四个业务域
   14.0k+7.8k+5.7k+2.3k」四块已全部不存在。
5. **门禁有 3 个可见性盲区**：目录名 `scripts` 被硬跳过、非 `.py` 资源资产的
   跨层路径耦合、`templates/` 登记为 L2 但 0 个 `.py`。
6. **GEP-RSI v1.1 对照（§8）：骨架已实现、P 层最薄、循环无驱动。**
   Tier 0 三重冻结超出设计要求；但 P 层缺成本/延迟维度、缺 held-in/held-out
   双集、缺诊断聚类、缺分歧 kill-switch；屏障缺 hash chain、双轨审计、重放审计；
   循环缺生产驱动与假基因清理。物理四层门禁对这类缺口**结构性失明**——
   G/E/P 是语义分层，与 L1–L4 正交。

---

## §1 架构图：机器同步的部分 vs 手写腐化的部分

### 1.1 门禁当前状态（全绿）

```
python scripts/arch/sync_architecture_diagram.py
  [OK] HTML 结构合法 / 无未转义 & / rect 均在 viewBox 1600x1790 内
  [OK] L1 108 文件 / 23,024 行   [OK] L2 15 文件 / 4,489 行
  [OK] L3 189 文件 / 36,402 行   [OK] L4 59 文件 / 10,030 行
  [OK] 总计 371 文件 / 73,945 行
  [OK] L1→L2 8 · L1→L3 115 · L1→L4 52 · L2→L3 39 · L2→L4 1 · L3→L4 54
  [OK] 上行违规全 0
架构图与代码一致 [OK]
```

`sync_architecture_diagram.py` 实际回写的只有 6 类：
层徽标（文件数 + k 行）· 层表四行 · 页首/页脚总计 · 6 条跨层边数 · 4 行矩阵 · 3 处违规文案。

### 1.2 不在同步范围、已经过期的文案

| 位置 | 图上写的 | 实测 | 偏差 |
|---|---|---|---|
| `<title>` | 架构 v9.1 | `<h1>` 是 v10.2 | 自相矛盾 |
| L1/api | 40 文件 · 15 routers · 13.4k | **37 文件 · 12 routers · 10,923 行** | 高估 |
| L1/cli | 66 文件 · 11.8k | **65 文件 · 10,703 行** | 高估 |
| L2 研究域 | 14.0k 行 · 41 文件 | **不存在**（study/autoresearch/method 已删） | 整块失效 |
| L2 因子与数据 | 7.8k 行 · 25 文件 | **不存在**（批次 5 全删） | 整块失效 |
| L2 回测与验证 | 5.7k 行 · 35 文件 | **不存在**（批次 4 全删） | 整块失效 |
| L2 进化与能力 | 2.3k 行 · 13 文件 | **不存在**（evolution 已搬 L3） | 整块失效 |
| L3 控制面 | 5.7k 行 · 29 文件 | **31 文件 · 6,526 行** | 低估 |
| L3 执行面 | 6.1k 行 · 30 文件 | **31 文件 · 6,351 行** | 低估 |
| L3 能力面 | 24.9k 行 · 115 文件 | **110 文件 · 21,070 行** | 高估 |
| L4 utils | 1.9k 行 | **5 文件 · 483 行** | **虚高 4 倍** |
| L4 storage | 2.0k 行 | 13 文件 · 2,424 行 | 近似 |
| 能力注册 | 「22 → 10 kind（只剩 evolution.*）」 | **6 个 `tool.*` + 5 个 `evolution.*` = 11** | 两处都错 |
| capabilities 框 | 「32 kind → CapabilityRegistry」 | **6** | 虚高 5 倍 |
| agent 工具 | 「38 → 18」 | 框架独有 18；`import apps` 后 **44** | 语义歧义 |
| L1/api 面板 | 「chat · study · session · admin · permission」 | study router 已删 | 过时 |
| L2 研究域注 | 「study 38 文件 … 轮末 director 发 study.directive」 | study 全删 | 过时 |

**根因**：`check()` 只验证 `str(n) in text`——只要数字在文中某处出现过就算通过。
L2 域名、模块清单、kind 数这些没有对应实测锚点，写了什么都不会失败。

---

## §2 四层现状实测（AST，口径 = ADR-000）

| 层 | 文件 | 行数 | 结构 |
|---|---:|---:|---|
| L1 interfaces | 108 | 23,024 | api 37/10,923 · cli 65/10,703 · mcp 2/574 · 组装根 3/792 |
| L2 apps | 15 | 4,489 | builtin_tools 7/3,684 · chat 4/222 · 根文件 3/583 · `__init__` 94 |
| L3 kernel | 189 | 36,402 | agent 90/18,737 · workflow 15/3,219 · gateway 9/2,176 · orchestration 11/2,403 · evolution 11/1,930 · swarm 6/1,488 · runtimes 10/1,644 · scheduled_research 6/1,180 · 其余 31/3,725 |
| L4 foundation | 59 | 10,030 | llm 22/4,678 · storage 13/2,424 · observability 5/865 · web 6/616 · utils 5/483 · 其余 8/964 |

`evals`（5 文件 / 515 行）登记为 L3；`templates`（53 文件 / 5,504 行，**0 个 `.py`**）登记为 L2。

### 2.1 符号级交叉验证（graphify）

```
      →L1    →L2    →L3    →L4
L1      0      6    117     57
L2      0      0     53      2
L3      0      0      0     96
L4      0      0      0      0
```

符号级恒 ≥ 语句级（门面 re-export 折叠）。**方向一致，无隐藏上行。**

扇入 top（被多少个文件 import）：
`kernel/agent/tools.py` 83 · `kernel/agent/builtin_tools/utils.py` 42 ·
`kernel/orchestration/contracts.py` 37 · `foundation/llm/errors.py` 34 ·
`foundation/llm/config.py` 32 ·
**`interfaces/api/schemas/study.py` 32**（study 域已删，schema 仍在被 import）。

扇出 top（自己 import 了多少个文件）：`kernel/agent/loop.py` 71 ·
`interfaces/api/session/service.py` 56 · `interfaces/cli/__init__.py` 47。

---

## §3 代码不一致清单

四级分类：**A = 真实缺陷**（运行期会出错或静默失效）· **B = 死代码**（无生产调用方）·
**C = 死注释/死文案**（说的事已不存在）· **D = 命名/口径债**（不影响运行）。

### 0 级：归档口径错误（先说清「能恢复」到底指什么）

`capabilities.py` 的三处注释都写「归档 `testcase/golden/apps/`」，但**只有批次 2 真的归档了**：

| 批次 | 提交 | 源码处置 | 磁盘档案 | 可恢复性 |
|---|---|---|---|---|
| 2 · goal/hypothesis | `08303f04` | **R100 重命名**（82 条） | `golden/apps/{goal,hypothesis}/` **在** | ✅ 磁盘直接拿 |
| 3 · study/autoresearch | `411a7ee7` | **D 删除**（43 条） | `golden/apps/study/` **不存在** | ⚠ 只能 `git show 411a7ee7^:path` |
| 4 · 回测/验证域 | `00548ccd` | **D 删除**（36 条） | `golden/apps/backtest/` **不存在** | ⚠ 同上 |
| 5 · 数据域 | `cd1e79d9` | **D 删除**（23 条） | `golden/apps/data_*/` **不存在** | ⚠ 同上 |

`testcase/golden/apps/` 实际只有 6 个子目录：`alpha_zoo` · `export` · `goal` ·
`hypothesis` · `portfolio` · `validation`。

**为什么这条重要**：批次 4/5 的注释让人以为「删掉的东西都在档案里躺着」，
据此估算恢复成本会严重偏低。批次 3/4/5 的源码只在 git 历史里。

### A 级：真实缺陷

| # | 位置 | 现象 | 证据 |
|---|---|---|---|
| A1 | `apps/capabilities.py:186` | 6 个 `tool.*` kind 全部注册成功，但工厂内部 `resolve()` 的依赖 kind 全缺 → 工具出现在 agent 工具表里，调用即失败 | 探针见 §4 |
| A2 | `kernel/agent/loop.py:96-108` | `_get_goal_store()` 直接 `resolve("goal.store")`，**无 try/except**；只有外层调用方有兜底 | 源码 |
| A3 | `interfaces/rsi_wiring.py:258` | `parents[4]` → `D:\Github\evals\`；备份路径 `cwd/evals` → 也不存在。golden eval **恒定降级**到代理分 | 探针实测报错 |
| A4 | `interfaces/rsi_wiring.py:159` | `build_rsi_loop()` 生产代码**零调用**；只有 `tests/test_production_wiring.py` 调 | `git grep` |
| A5 | `interfaces/cli/commands/server.py:99,147` + `api/app.py:254` | 用 `AN_GOAL_DB_PATH`；而 `ticket_store.py:52` / `scheduled_research/store.py:27` / `hanging_events.py:22` / `loop.py:92` 用 `AGENTNODES_GOAL_DB_PATH` → `--goal-db` 只影响一半，**双头分裂** | `git grep` |
| A6 | `kernel/evolution/holdout.py:222` | 读 `study.round_done` 事件，该 kind **全仓零 emit** | `git grep` 仅 1 处（读取处） |
| A7 | `interfaces/api/session/loop_events.py:32` vs `projection_handlers.py:790` | `GOAL_UPDATED` handler 仍注册，但无 emit 方 → 整条 goal 消息投影链是死的（~100 行） | `git grep` |
| A8 | `interfaces/cli/interactive/main.py:100` | `/goal` 提示「改用 `/study`」——`/study` **也不存在** | `git grep /study` |

### B 级：死代码（无生产调用方）

| # | 符号 | 说明 |
|---|---|---|
| B1 | `Command.handler_module`（`slash_router.py:25`） | 字段**零消费者**。9 个 `/命令` 指向不存在的模块（`cli.commands.chat` / `.memory` / `.goal`），因此不报错也不生效 |
| B2 | `interfaces/api/schemas/study.py`（400 行 / 32 符号） | 扇入 32 全部来自 `interfaces/api/schemas/__init__.py` 自身的 re-export；study 域已删 |
| B2b | `tests/test_dead_code_no_imports.py:37` | 守卫名单里仍有 `StudyDirectiveComposer` —— 它守的东西已经不存在 |
| B3 | `EventType` 的 26 个 `STUDY_*` 常量 | 25 个零外部引用；唯一有引用的是 `STUDY_STARTED`，且只在 `tests/attic/` |
| B4 | `_baseline_from_source()`（`rsi_wiring.py:368`） | 无调用方 |
| B5 | `hypothesis.registry` / `hypothesis.auto_creator` 注入钩子 | `context_injector.py:355` / `loop.py:1227`，kind 永不注册 |
| B6 | `templates/.prompts/study_reviewer.md` / `study_collector.md` | `builtin_plugins.py:173,182` 仍引用，study 域已删 |
| B7 | `src/agentnodes/scripts/backfill_agent_id.py`（104 行） | 不在成员表、也不在门禁 walk 范围（目录名 `scripts` 被硬跳过）|
| B8 | `pyproject.toml:135` 的 per-file-ignore | 指向 `src/agentnodes/scripts/migrate_study_history.py` —— 文件不存在 |

### C 级：死注释 / 死文案

| # | 位置 | 说的 vs 实际 |
|---|---|---|
| C1 | `apps/__init__.py:1` | 「research-domain IP (study/goal/alpha_zoo/backtest...)」— 四个全gone |
| C2 | `apps/capabilities.py:46` | 「共 35 文件 5,982 行，归档 `testcase/golden/apps/`」— **批次 4 是直删，无档案** ← 见 §0 |
| C3 | `apps/capabilities.py:64` | 「study + autoresearch 全部归档到 `testcase/golden/apps/`」— **批次 3 是直删，无档案** ← 见 §0 |
| C4 | `apps/capabilities.py:196` | 「22 文件 6,822 行，归档 `testcase/golden/apps/`」— **批次 5 是直删，无档案** ← 见 §0 |
| C5 | `foundation/errors.py:7` | 示例写 `from agentnodes.core.errors import ...` — 该包名从未在 agentnodes 下存在 |
| C6 | `foundation/errors.py:112` | 仍定义 `BacktestError`，回测域已删；`GoalError` 反而该留 |
| C7 | `loop.py` / `ticket_store.py` / `scheduled_research` 等 **38 处** | 注释引用 `core/goal/...`、`core/study/...`、`core/llm/...` 等物理迁移前路径 |
| C8 | `interfaces/api/session/service.py:341,345,374` | 引用 `core/goal/workflow.py`、`core/study/_archived/chat_mutex_legacy.py`（都不存在）|
| C9 | `builtin_tools/__init__.py:16-17` | 「kind 缺失时静默跳过，比注册一个调用必然失败的工具好」— 实际正在做后者（A1）|
| C10 | `config_loader.py:14` / `io_utils.py:5` | 引用 `core/goal/workflow_config.py`（已归档）|

### D 级：命名 / 口径债

| # | 现象 |
|---|---|
| D1 | `agentnodes.templates` 登记为 **L2**，但 0 个 `.py`；L3 靠 `Path(__file__)` 读它（`prompt_builder.py:51`、`harness_resolver.py:171`、`workflow/builtin.py:20`、`prompt_schema_extractor.py:351,505`）→ 登记与依赖事实不符 |
| D2 | `interfaces/api/session/` 5,752 行仍在 L1；`session→routers` 5 条 / `routers→session` 7 条，L1 内部成环（W9 设计文档判为 L3 候选，未落地）|
| D3 | 归档代码里 `strategy_research.*` 旧包名（goal 内 21 条 import，接口层 45 条）——恢复时须整体改名 |
| D4 | `testcase/golden/` 下 **92 个 `.pyc`** 磁盘存在（未入库，但污染归档目录）|
| D5 | `testcase/golden/apps/goal/validator_registry.py:8` / `dag_renderer.py:15` 的旧包名在 **docstring 的 Usage 示例**里，非真 import |

---

## §4 端口化降级语义失效（最深的那个）

### 4.1 现象

框架工具注册表（不 import apps）= **18 个**。`import agentnodes.apps` 后 = **44 个**，新增 26 个业务工具。

这 26 个里，逐个实调（`ToolContext(workspace=., session_id=probe)`）：

| 工具 | 结果 |
|---|---|
| `create_goal` | `CapabilityNotFound: goal.context` |
| `list_goals` | `CapabilityNotFound: goal.models` |
| `get_goal_status` | `CapabilityNotFound: goal.store` |
| `compute_factor` | `db open failed: CapabilityNotFound: db.get_connection` |
| `check_data` | `source='config' is no longer available` |
| `run_backtest` | 参数校验层先失败（`strategy_name` 空）|
| `get_market_data` | 参数校验层先失败（`codes` 非 JSON list）|

`resolve()` 全量探针：**14 个 kind 从未注册**，调用必抛：

```
db.get_connection  db.init_db  db.save_ohlcv_to_db
datasource.base  datasource.registry  datasource.detect_market
config.runner  data.readiness  data.clean
goal.store  goal.models  goal.context
hypothesis.registry  hypothesis.auto_creator
```

已注册的只有 6 个 `tool.*`（包装层）+ 5 个 `evolution.*`。

### 4.2 为什么注释里的降级没生效

`_register_business_tools()`（`builtin_tools/__init__.py:47`）的逻辑是：

```python
tools = resolve(kind)          # 拿到 list[BaseTool] —— 这里成功了
for tool in tools:
    registry.register(tool)    # 工具进表 —— 也成功了
```

它校验的是「**包装层 kind 存在**」，不是「**工具真能跑**」。`tool.goal` 的工厂
`_tool_goal()` 只 `from ...goal_tools import ...` + 构造对象，不触碰
`goal.store`。依赖在 `execute()` 里才 `resolve`。

**结论**：端口化的失败从编译期（import 时）推迟到了运行期（调用时），而
注册层没有任何探测手段把这两种状态区分开。注释承诺的「不注册调用必然失败
的工具」在结构上做不到——除非注册时做一次能力自检。

### 4.3 这不是 goal 独有的

因子（7 个）、数据（5 个）、回测（5 个）、回测分析（3 个）、数据清洗（1 个）
全部同一形态。**26 个业务工具里，只有 goal 的 5 个有明确的恢复路径。**

---

## §5 goal 恢复的真实阻断点

归档完整度：`testcase/golden/apps/goal/` **19 个 `.py` / 6,147 行，全部入库**。
L1 侧 8 个文件 / 2,604 行入库。`testcase/tests/` 42 个 goal/workflow 测试 / 10,982 行入库。

### 5.1 只有 4 处硬阻断

| # | 依赖 | 现状 |
|---|---|---|
| `store.py:1037` | `apps.hypothesis.HypothesisRegistry` | apps/hypothesis **磁盘可恢复**（6 文件 / 1,459 行）|
| `store.py:1649` | `apps.study.method.claims.summarize_prediction_accuracy` | apps/study **仅 git 历史**（`git show 411a7ee7^:path`）|
| `workflow.py:402` | `apps.study.runner.meets_metric_targets` | 同上 |
| `workflow.py:425` | `apps.backtest.run_backtest_script` | apps/backtest **仅 git 历史**（`git show 00548ccd^:path`，35 文件 / 5,982 行）|

其余 20 条 import 指向的模块**全部存活**（`foundation.storage.sqlite` /
`kernel.swarm.*` / `kernel.workflow.{dag,types,validator}` / `foundation.observability` /
`foundation.utils.io_utils` / `kernel.agent.memory_manager`），只需改 `strategy_research` → `agentnodes` 前缀。

### 5.2 恢复工作量估计

- 改名：21 条 import 前缀替换（纯机械）
- 4 处阻断：hypothesis 只需恢复磁盘档案；study/backtest 两处要从 git 历史取回（或降级）
- L1 侧：45 条 import，其中 `apps.hypothesis` 5 条、`apps.validation` 3 条、`apps.export` 1 条也要一并处理
- 测试：42 文件 / 10,982 行搬回，同样需改名

---

## §6 门禁的可见性边界

三个盲区，都实测确认：

### 6.1 目录名 `scripts` 被硬跳过

```python
# scripts/arch/layer_matrix.py:157-159
dirnames[:] = [d for d in dirnames
               if d not in ("__pycache__", "scripts", "graphify-out")]
```

`src/agentnodes/scripts/backfill_agent_id.py`（104 行）因此既不进层归属表、
也不被扫。这个 skip 是给**仓库根**的 `scripts/`（dev 工具）准备的，误伤了
**包内**同名目录。

### 6.2 资源资产的跨层路径耦合

`templates/` 登记 L2，但被 L3 通过 `Path(__file__).parent.parent.parent / "templates"` 读取。
这是真实的 L3→L2 依赖（4 个文件 6 处），但门禁只读 `import` 语句，看不见。
`check_path_literals.py` 只检测 `"core"` 段和 `"agentnodes"/"<非层名>"` 两种形态，
不检测跨层资源引用。

### 6.3 `check()` 的数字存在性判定过弱

```python
hit = str(n) in text      # 只要数字在文中任一处出现就算通过
```

层体量这类数字恰好唯一，勉强够用；但「32 kind」「38 → 18」「15 routers」
没有任何实测锚点，写错永远绿。

---

## §7 架构讨论的 6 个议题

按「先定方向，再定边界，最后定实现」排序。

### 议题 1 · L2 还剩什么？

实测 L2 = 26 个必然失败的工具 + 222 行 chat 策略 + 283 行根文件。四个业务域全空。
三种可能：
- **(a)** 恢复 goal（+hypothesis）→ L2 重新有研究对象
- **(b)** 承认 L2 已空 → 取消这一层，工具注册并回 L1 组装根
- **(c)** 保留 L2 作为「业务插件槽」，但要求每个 kind 在注册时自证可运行

**议题 1 的答案决定议题 2–6 的框架。**

### 议题 2 · 端口化的正确边界在哪？

ADR-003 把 L3→L2 从 46 降到 0，代价是运行期失败替代编译期失败。
需要决定：
- 是否引入「能力自检」（注册时试跑 / 声明 `requires=[...]` 依赖清单）？
- 缺依赖时是「工具不注册」还是「注册但标注 degraded」？
- `resolve()` 的语义该不该区分「未注册」与「注册了但依赖缺失」？

### 议题 3 · goal / evolution / chat 三者谁在哪层？

三个身份同时存在：
- **业务台账**（目标/判据/证据）→ 天然属 L2
- **RSI 的证据源**（journal + scoreboard）→ L3 需要它，但只能通过端口
- **会话显示**（GoalPanel / SSE / `/goal`）→ L1 需要它

`evolution_agent.py:42-55` 已经用两个 Protocol（`JournalSource` / `ScoreboardSource`）
把这个结构留好了——但 `rsi_wiring.py:211-214` 把四个源全指向同一个
`EventStoreReportSource`，等于没接。

### 议题 4 · RSI 闭环真的是闭的吗？

四个断点叠加：
1. `build_rsi_loop()` 零生产调用（A4）
2. golden eval 路径错一级（A3）→ 恒定降级
3. 四源同一对象（`rsi_wiring.py:211`）→ journal/scoreboard/validation/holdout 无区分
4. `read_journal` 读 `study.round_done`（A6）→ 永空

所以现在能说的只是「五个零件各自有测试」。**要变成真闭环，需要一条真实驱动链**：
谁在什么时候调 `build_rsi_loop`？cron？会话结束？手动 CLI？

### 议题 5 · 执行面三套并存，关系是什么？

| 执行器 | 体量 | 谁在用 |
|---|---:|---|
| `AgentLoop` | 22.5k | chat / CLI / evals |
| `GraphEngine` + `workflow` 编译器 | 1,644 + 3,219 | swarm preset、workflow API |
| `SwarmRuntime` | 1,488 | preset 直接执行 |

`swarm/presets/` 有 **37 个 YAML**，其中 6 个带 goal 语义（`goal` / `completion` /
`default_criteria`）—— 但 `preset.goal` 与 `preset.completion` **全仓无读取方**
（只有 `preset.branches` 被 `swarm/runtime.py:350` 读）。
即：6 个 goal 预设的 goal 段是纯注释。

### 议题 6 · 资源资产的跨层依赖怎么建模？

`templates/`（5,504 行，0 个 `.py`）被 L3 读、被 L1 读、登记为 L2。
三个选项：
- 把 templates 当 L4 资产（零业务语义？不成立——里面全是研究方法论）
- 给资源也做端口（`resolve("templates.prompts_dir")`）
- 承认「资产层」是与四层正交的第五个维度

---

## §8 GEP-RSI v1.1 对照（补遗：为什么首轮扫描漏了它，以及补上后的结论）

### 8.0 为什么首轮扫描没有体现 GEP-RSI

三个原因，按重要性排：

1. **锚定偏差**：扫描以 `architecture-diagram.html`（物理四层）+ 两份 design doc
   为「架构设计」的口径。GEP-RSI v1.1 是未跟踪的新文件，读了但没当成规范去对表。
2. **方法论正交**：本轮的三把尺——AST import 门禁、graphify 符号图、运行期探针——
   只能测**物理分层**与**接线**。GEP-RSI 是**语义分层**（按进化角色分 G/E/P +
   屏障），两套维度正交：任何 import 违规都不会暴露「GEP-RSI 不符」，
   反之 GEP-RSI 的缺口在 import 图上也恒为 0。
3. **后果**：议题 4 把 RSI 缺陷框成「接线问题」；在 GEP-RSI 语境下它们是
   **屏障与循环的符合性缺口**，且更深的一批缺口（成本维度、双集双门、
   双轨审计、假基因清理、驱动链）根本没被列出。

### 8.1 G 层 · 基因组层（变异发生地）→ 代码落点

| GEP-RSI 构件 | 代码落点 | 状态 |
|---|---|---|
| 模块化基因条目 | `GenomeVersion.tier1_active`（`kernel/orchestration/genome_version.py`，frozen dataclass 版本链 + 磁盘 `GenomeStore`）| ✅ 载体存在，但是运行时 dict 不是 agents.md 式声明文件 |
| 五字段契约（作用域/触发/指令/证伪/谱系） | `GenomeProposal` 只有 proposal_id / target_component / change / evidence_refs / status | ⚠ **无触发条件、无证伪条件**；谱系靠 `activated_by_proposal` + 版本链（部分）|
| 变异语法限定（增/调参/删/拆合） | `change` 是自由 dict，无语法校验 | ❌ |
| Tier 0 冻结 | `TIER0_COMPONENTS = {budget, stop_policy, state_machine, methodology}`；构造期拒绝（`genome_proposals.py:82`）+ validation_gate 反序列化拒绝 + skill_memory 丢键（`skill_memory.py:98`）| ✅ **H3 三重保证，超出 GEP-RSI 要求** |
| Tier 1 白名单 | `TIER1 = {prompt, skill, graph_shape, tool_set, compaction}`，闭合集合，未知组件构造期抛错 | ✅ 顺带实现了「评估器不在变异面」（MetaRSI 软密封）|
| 假基因清理（循环第 7 步） | `deactivate()` 有单组件撤销（`genome_version.py:215`），但无周期性「从未激活/负收益」清扫 | ❌ |

### 8.2 E 层 · 表达编译层（选择执行地）→ 代码落点

| GEP-RSI 构件 | 代码落点 | 状态 |
|---|---|---|
| 选择性转录 | `SkillMemoryResolver`（`kernel/evolution/skill_memory.py`）从 `tier1_active` 组装 prompt_version / tool_set / skill / compaction_config | ✅ |
| 转录本（激活的基因 + 原因） | resolver 不记录「为什么激活/哪个提案导致」；只有 `rsi.validated` 事件带 proposal_id | ⚠ 半：可从事件流重建，无结构化转录本 |
| 回滚锚点 | `rsi_wiring._make_applier`：`rollback_to(before)` 先记锚再应用 | ✅ |
| 成本硬约束（校准 01） | `compaction` 是 Tier 1 可提组件（`collapse_keep_recent`）——上下文占用可被进化；但成本/延迟不在评分里 | ⚠ 可进化但不可度量 |

### 8.3 P 层 · 能力表型层（选择裁决地）→ 代码落点 —— **全系统最薄的一层**

| GEP-RSI 构件 | 代码落点 | 状态 |
|---|---|---|
| 表型向量（任务行为模式，校准 03） | `evals/scorecard.py` `TaskScore` = tool_recall 0.4 + 无违规 0.2 + budget 0.15 + content 0.15 + completed 0.1 | ✅ 方向正确（非人格测量）|
| **成本/延迟为并列硬维度（校准 01）** | `tokens_used` 只做 0/1 门槛不进总分；**latency 全库无采集**（`grep latency/duration/elapsed` → 0 处）| ❌ |
| 双门（held-in 显著改进 + held-out 无退化） | `validation_gate` 只在**同一批** golden 抽样上比基线 vs 候选；±20% 双侧（>+20% 拒，<−20% 判 reward-hacking escalate）| ⚠ 是「单集双侧」，不是「双集双门」|
| 分歧 kill-switch | 不存在（连双集都没有）| ❌ |
| 表型诊断（第 1 步，失败签名 (c,q,m) 聚类） | `default_policy` 只用 failure_rate + 回滚计数两个标量；`grep signature/cluster` → 0 处 | ❌ |
| P 层测量代码不参与自我评分 | `evals/` 在 L3、不在 Tier 1 白名单 → 变异面封闭成立；但 `golden_tasks.jsonl` 是包内普通文件，**无完整性校验** | ⚠ 软密封 |

### 8.4 种系屏障层 → 代码落点

| GEP-RSI 构件 | 代码落点 | 状态 |
|---|---|---|
| append-only 审计账本 | `event_log`（UNIQUE(aggregate,branch,seq) 约束）+ genome 版本链（append + 游标，frozen）| ✅ append-only 成立 |
| hash chain | **无**（`grep hash_chain/prev_hash` → 0 处）| ❌ GEP-RSI v1.1 明说 hash-chain 是必要非充分 |
| 定期用独立评估器快照重放历史 | **无** | ❌ |
| 双轨审计器（Governance Gauntlet） | **无** | ❌ |
| 层级终止 | 事实上的终止层级 = 包外 `scripts/arch` 门禁 + 测试 + 人；但 `genome_proposals.py`（白名单本体）就在包内，运行时无机制阻止改它 | ⚠ 终止层级只覆盖 import 方向 |
| kill-switch | `interfaces/cli/halt.py` 是**人工** kill switch（trip/clear），非评估分歧自动触发 | ⚠ 语义不同 |

### 8.5 RSI 循环七步对照

| 步 | GEP-RSI | 代码 | 判定 |
|---|---|---|---|
| 1 | 表型诊断（失败签名聚类） | failure_rate + 回滚计数两个标量 | ❌ |
| 2 | 有界提议 | 单组件 + Tier1 白名单 + 构造期拒绝 | ✅ |
| 3 | 屏障预检 | 构造期 + 反序列化期两道 | ✅ |
| 4 | E 层编译执行 | skill_memory + isolated_replay + 回滚锚点 | ✅ |
| 5 | P 层双门评估 | 单集双侧 ±20% | ⚠ |
| 6 | 裁决与回滚 | 3 连败回滚 + 单组件 deactivate + 提案粒度 apply→undo（RePAIR 式）| ⚠ |
| 7 | 假基因清理 | 无 | ❌ |
| 0 | **驱动**（谁让循环转起来） | `build_rsi_loop()` 生产零调用（§3 A4）| ❌ **最致命** |

### 8.6 结论

代码实现了 GEP-RSI 的**骨架**且 Tier 0 冻结超出设计要求；
**缺口集中在三处**：P 层（无成本维度、无双集、无诊断聚类、无分歧 kill-switch）、
屏障（无双轨审计、无 hash chain、无重放审计）、循环（无驱动、无假基因清理）。

与 goal 重建的关系：goal 的 journal（判据级证据）正是 GEP-RSI 第 1 步
「表型诊断」缺的细粒度信号——**goal-merge 设计文档的 R3 = GEP-RSI 第 1 步的实现前提**。
两个文档由此接上了。

---

## 附 A · 复现命令

```
# 图
D:\tools\python312\python.exe -m graphify extract . --code-only --no-cluster --out tmp/graphify3
D:\tools\python312\python.exe scripts\arch\graph_layer_audit.py tmp\graphify3\graphify-out\graph.json

# 层门禁（语句级）
D:\tools\python312\python.exe scripts\arch\check_dangling_imports.py --layers
D:\tools\python312\python.exe scripts\arch\sync_architecture_diagram.py

# 测试收集
C:\Users\1\AppData\Local\Programs\Python\Python313\python.exe -m pytest tests --collect-only -q
# → 6158 collected, 5 errors（全部是 playwright 未安装）
```

## 附 B · 本次扫描未覆盖

- `webui/frontend`（300 文件 / TS+Vue）：与四层架构的关系未评
- `docs/archive/`（266 文件）：历史文档，未核对
- `tests/attic/`（30 文件）：已知废弃，仅做引用计数
- 运行期性能 / 并发正确性：不在本次范围
