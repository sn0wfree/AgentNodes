# 架构图版本史（archive）

> 从 `docs/architecture-diagram.html` 外移（2026-11-03 v8 瘦身）。
> 图身只保留结构与盒级状态；本文件承载 v5.1 → v8.4 的全部演进记录。

- **v10.2（2026-11-05，做减法批次 5：移除数据域）**：
  **L2 层第三次腰斩 —— 10,294 → 2,995 行（-71%）、40 → 17 文件**。
  批次 4+5 合起来把 L2 从 16,284 打到 2,995 —— **只剩原来的 18%**。
  ① **移除 22 文件 / 6,822 行**：`apps/data_source/`（13 文件 2,141 行，8 个行情 loader）·
  `apps/compute_factor.py`（1,388）· `apps/db.py`（1,273）· `apps/tools/`（4/822）·
  `apps/data_import.py`（496）· `apps/data_readiness.py`（426）· `apps/preflight.py`（276）。
  全部归档 `testcase/golden/apps/`。
  ② **能力注册 22 → 10 kind**：删 `db.*`（4）· `factor.*`（3）· `datasource.*`（3）·
  `data.readiness` · `data.clean`。**只剩 `evolution.*` 10 个** —— 这正是框架该有的样子：
  能力供给端口只剩「自我进化」一件事。
  ③ **agent 工具 38 → 18**：从 `build_default_registry` 摘掉 15 个业务工具注册
  （RunBacktestTool · ComputeFactorTool · FactorAnalysisTool · DataCleanTool ·
  StrategyCompare · ListDataSourcesTool · SearchSymbolTool · CheckDataTool …）
  + 4 个 goal 死代码工具。**文件保留到批次 6 一起删**；本批次只保证 agent 的
  tool 列表里不再出现「调用必然失败」的项。
  ④ **两个刻意保留的框架接缝**：
  - `foundation/storage/data_store.py`（`DataStore` Protocol）—— 能力契约，不是实现
  - `apps/data_store_registry.py` —— 注册表保留但**初始为空**；`get_store()` 抛
    `KeyError` 并提示「在装配根 register_store(name, cls)」。`app.py` 已改 try/except
    留空槽。
  `apps/duckdb_store.py`（DuckDBDataStore 实现）随 `db.py` 一起删 —— 它是
  `DataStore` 的**实现**，不是契约。
  ⑤ **一处显式降级**：`templates/prepare.py` 两处 `from apps.{db,compute_factor}`
  已有 try/except 降级（DuckDB → CSV / 完整因子 → 简单因子），加
  `# dangling-import: ok` 豁免标记而非改代码。
  ⑥ 逐项实测：L1 **108/22,980** · L2 **17/2,995** · L3 **178/37,645** ·
  L4 **64/10,925** · 总计 **367 文件 / 74,545 行**（原 390/82,163，-9.3%）。
  跨层边数：L1→L2 **27 → 13** · L1→L3 106 → 105 · L1→L4 53 → 51 ·
  L2→L3 6 · L2→L4 5 → **3** · L3→L4 49。
  上行违规仍是 **2 条**（L3→L1 2 · L2→L1 0 · L3→L2 0），棘轮基线未动。
  校验：`sync_architecture_diagram.py --write` 回写后全 `[OK]` ·
  `check_dangling_imports` **0 悬空** · 收集 **6718 tests / 0 errors**。

- **v10.1（2026-11-05，做减法批次 4：移除回测与验证域）**：
  **L2 层第二次腰斩 —— 16,284 → 10,294 行（-37%）、75 → 40 文件**。
  ① **移除 8 个包/文件**（35 文件 / 5,982 行）：
  `apps/engine/`（21 文件 2,350 行，多市场回测引擎）· `apps/strategy_acceptance/`（4/883）·
  `apps/backtest.py`（811）· `apps/config_runner.py`（605）· `apps/factor_validate.py`（581）·
  `apps/backtest_engine/`（5/498）· `apps/run_card.py`（242）· `apps/backtest_models.py`（12）。
  全部归档 `testcase/golden/apps/`。
  ② **L1 同步移除**：`routers/run.py`（177 行，整删）· `cli` 的 8 个子命令
  （run / evaluate / list / validate / backtest / check / engine / accept）·
  `commands/merged.py` 的 backtest/check 两组（只剩 serve）。
  ③ **能力注册 26 → 22 kind**：删 `backtest.from_yaml` / `backtest.script` /
  `backtest.decide` / `config.runner` 四个。
  **注意**：`datasource.*` / `data.readiness` / `data.clean` 三个**没有**删 ——
  它们的实现住在 `apps/{data_source,data_readiness,tools}/`，属批次 5。
  第一版探测用正则窗口把相邻函数的 import 算进来了，虚报成 8 个；AST 精确核算后是 4 个。
  ④ **连带删除 4 处 kernel 业务 executor**：`kernel/agent/exec_registry.py` 与
  `kernel/swarm/runtime.py` 各注册了 `run_backtest_script` / `decide` 两个 executor，
  它们 resolve 已删的 kind。这两个 executor 本身也是回测业务，故一并删除
  （不是改成 no-op —— 保留一个永远抛 CapabilityNotFound 的注册项没有意义）。
  ⑤ **一处显式降级**：`builtin_tools/data_tools.py` 的 `source='config'` 路径
  resolve `config.runner`。改为捕获 `CapabilityNotFound` 并返回可操作错误
  （`source='explicit' with codes/start_date/end_date`），不静默 `cfg=None`。
  ⑥ 逐项实测：L1 **108/23,279** · L2 **40/10,294** · L3 **178/37,665** ·
  L4 **64/10,925** · 总计 **390 文件 / 82,163 行**（原 426/88,771，-7.4%）。
  跨层边数：L1→L2 **36 → 27** · L1→L3 106 · L1→L4 53 → 51 ·
  L2→L3 6 · L2→L4 25 → **5** · L3→L4 49。
  上行违规仍是 **2 条**（L3→L1 2 · L2→L1 0 · L3→L2 0），棘轮基线未动。
  校验：`sync_architecture_diagram.py --write` 回写后全 `[OK]` ·
  `check_dangling_imports` **0 悬空** · 收集 **7044 tests / 0 errors**。

- **v10.0（2026-11-05，做减法批次 3：移除 study/autoresearch）**：
  **L2 层腰斩 —— 28,922 → 16,284 行（-44%）、113 → 75 文件**。这是四层版式落地以来
  单批最大的一次减法。
  ① **移除**：`apps/study`（35 文件 / ~10,700 行）· `apps/autoresearch.py`（1,364 行），
  全部归档 `testcase/golden/apps/`。
  ② **L1 同步移除** 4 文件 / ~2,900 行：`routers/study.py`（33 处 study import）·
  `routers/study_helpers.py` · `routers/slash_commands.py` · `cli/commands/autoresearch.py`；
  连带 `scripts/migrate_study_history.py`。
  ③ **Ticket/Assignment 迁到 L3**（本批最重要的裁定）：
  `apps/study/ticket.py`（325 行）→ `kernel/orchestration/ticket.py`；
  `ticket_sink.py` + `store.py` 的 ticket 段（2 表 DDL + 7 CRUD + 2 row 转换）
  → `kernel/orchestration/ticket_store.py`（505 行）。
  **依据**：逐字段复核后，`objective` / `authorization_scope` / `priority` /
  `fence_token` / `run_id` 全是通用任务身份概念，全文 325 行业务词汇
  （策略/因子/回测/行情/研究/收益/风险）出现 **0 次**。原 docstring 的
  「携带研究域业务身份」理由不成立。对照 `apps/study/models.py` 的
  `MetricTarget` / `StudyRecord` / `strategy_name` / `workspace_path` 才是业务。
  ④ **两个「名字带 study、实现零 study 语义」的通用件归位**：
  `ensure_study_session`（30 行，只做 `INSERT OR IGNORE INTO sessions`）
  → `interfaces/api/session/web_session_db.py::ensure_session_row`；
  `_default_db_path` → `ticket_store.py::default_db_path`。
  ⑤ **三个通用 slash handler 保留**：`/compact` · `/clear` · `/help` 迁到
  `routers/slash_common.py`；`/study` 系列（~400 行）随包移除。
  ⑥ **调度器骨架保留**：`kernel/scheduled_research/`（6 文件 1,194 行）不归档 ——
  cron 计算 + `scheduled_jobs` 表 + asyncio 循环是通用能力。只有
  `target='study'` 失效，改为**显式抛 RuntimeError**（不静默 no-op：
  静默会让用户以为定时任务跑成功了）。
  ⑦ **能力注册 32 → 26 kind**：删除 `study.graph` / `study.store` /
  `study.scheduler` / `study.status` / `study.bootstrap` / `study.ticket_sink`。
  ⑧ **上行违规 3 → 2 条**：**`L2→L1` 归零** —— 那个唯一的违规正是
  `apps/study/scheduler.py:779` 的 lazy import，随 study 一起消失。
  只剩 `L3→L1` 2 条（展示层引用）。**这是批次 1 以来棘轮基线第一次真正下降。**
  ⑨ 逐项实测：L1 **109/23,863** · L2 **75/16,284** · L3 **178/37,699** ·
  L4 **64/10,925** · 总计 **426 文件 / 88,771 行**（原 467/104,337，-15%）。
  跨层边数：L1→L2 **87 → 36** · L1→L3 106（不变）· L1→L4 53 → 51 ·
  L2→L3 27 → **6** · L2→L4 42 → 25 · L3→L4 49（不变）。
  校验：门禁 `sync_architecture_diagram.py --write` 回写后全 `[OK]` ·
  `check_dangling_imports` **0 悬空** · 收集 **0 errors**。

- **v9.2（2026-11-05，批次 2 收尾：门禁补漏 · 自动回写落地）**：**图面结构与版式一字未改**，
  只刷新了两个总数（L1 26,613 → **26,623** 行 · 总计 104,175 → **104,185** 行，
  来自 `cmd_skill` 修复的 +10 行）。逐项实测：L1 112/26,623 · L2 114/29,679 ·
  L3 176/36,958 · L4 64/10,925 · 总计 466 文件 / 104,185 行 · 跨层边数
  L1→L2 91 · L1→L3 106 · L1→L4 53 · L2→L3 25 · L2→L4 42 · L3→L4 48 ·
  上行违规仍是 3 条（L2→L1 1 · L3→L1 2 · L3→L2 0）。
  本版记的是**三个门禁缺陷**——它们让批次 2 的残留错误看起来像「已通过」：
  ① **悬空导入门禁漏检 `import a.b.c as x` 形式**：`check_dangling_imports.py` 的
  `scan()` 只遍历 `ast.ImportFrom`，**完全不看** `ast.Import`**。批次 2 删掉
  `interfaces/api/routers/workflow.py` 后，`tests/test_workflow_approval_api.py` 与
  `tests/test_workflow_ownership.py` 用 `import ...routers.workflow as wf` 继续引用它——
  门禁当时是**绿的**，而这 24 个用例在 fixture setup 期才炸（收集期不报错，
  连 collection 都看不出来）。现补 `ast.Import` 分支，并把该形态写成回归测试
  （`tests/test_scripts_arch.py::test_dangling_detects_plain_import_alias`）。
  ② **同门禁漏检 `from <包> import <名字>` 的「名字消失」**：
  `from strategy_research.interfaces.api.routers import workflow` 的宿主包还在，
  旧门禁只解析 `node.module`（= routers），看不出被 import 的名字已不存在。
  `tests/test_workflow_control_api.py` 的 10 个用例就藏在这里。现补第三种检查
  （包 `__init__.py` 的顶层绑定 + 子模块存在性；PEP 562 `__getattr__` 兜底时放过）。
  ③ **架构图门禁在 Windows 默认 GBK 控制台上崩**：末行 `print("... ✅")` 抛
  `UnicodeEncodeError`——**在「全 [OK]」之后**崩，退出码 1，看起来像检查失败。
  现 `sys.stdout.reconfigure(errors="replace")` 兜底，并把 ✅ 换成全文一致的 `[OK]`。
  另：**`--write` 从未实现**（docstring 里承诺过），「每次大改后自动更新架构图」
  此前靠人手工改数字。现已落地：只改数字、锚定上下文、写后复核不过则还原、
  零命中即报、幂等（5 条测试断言）。三个 workflow 路由测试文件归档到
  `testcase/tests/`（它们测的是已移除的 `/api/goal/workflow/*` 面）。
  顺带修掉一个**新发现的死引用**：`cli/commands/show.py::cmd_skill` 引的
  `SkillsLoader` 在仓库里从不存在（`kernel/skills` 只有 `SkillRegistry` /
  `parse_skill_file`），`/skill` 命令一直在打印「failed」——改用 `SkillRegistry`
  + 随包模板目录，与 `kernel/agent/harness_resolver._load_skills` 同一读法。
  校验：`check_dangling_imports.py` **0 悬空** · `sync_architecture_diagram.py` 退出码 0 ·
  `tests/test_scripts_arch.py` 23 passed · `tests/test_layer_matrix.py` 2 passed。

- **v9.1（2026-11-05，做减法批次 2 后刷新）**：**数据与模块分组刷新，版式不变**。
  ① **移除五个业务包**：`apps/goal`(19 文件 6,147 行) · `apps/hypothesis`(6/1,459) ·
  `apps/validation`(9/801) · `apps/portfolio`(6/619) · `apps/export`(2/302)，
  合计 42 文件 9,328 行，全部归档到 `testcase/golden/apps/`；连带移除 L1 出口
  8 文件 2,604 行（routers goal/hypothesis/validation/workflow · cli slash_goal/export ·
  tui dag_view/workflow_worker），归档 `testcase/golden/interfaces/`。
  ② **层体量重测**：L1 112/26.6k（原 120/30.3k）· **L2 114/29.8k（原 154/38.9k，-23%）** ·
  L3 175/36.7k 不变 · L4 64/10.9k 不变；总计 **465 文件 / 103,988 行**（原 513/116.9k）。
  ③ **模块卡重排**：研究域 14.0k/41 文件（study 12.0k + autoresearch 1.4k + templates 0.5k，
  内含 method/ 7 文件 1.1k）· 因子与数据 7.8k/25 文件 · 回测与验证 5.7k/35 文件 ·
  进化与能力供给 2.3k/13 文件（含 apps/__init__ 注册门面）。四域之和经严格分区断言
  ＝层总量 114 文件 29,755 行，无遗漏无重复。
  ④ **能力注册 38 → 32 kind**：goal.store/models/context/expression 与
  hypothesis.registry/auto_creator 六个 kind 随包移除；kernel 侧为惰性 resolve +
  try/except，kind 缺失时运行时降级为 no-op。
  ⑤ **两件 goal 残件并入 study**：`objective_policy.py`（拒绝实盘下单目标，37 行）·
  `scoreboard.py`（AEGIS 杠杆 Beta 后验 + 疲劳检测，210 行，消费者仅 phase_engine）。
  ⑥ **跨层边数刷新**：L1→L2 171→**91** · L1→L3 120→**106** · L2→L3 39→**25**（11 个文件触点）·
  L1→L4 53 · L2→L4 48→**42** · L3→L4 48 均重测。上行违规仍是 **3 条**
  （L2→L1 1 · L3→L1 2 · L3→L2 0），棘轮基线未动。
  ⑦ **新增一表**「做减法批次 2：移除业务包」，逐项写明体量与归档去向（含 goal 残件归位）。
  ⑧ **两个通用件迁入 kernel**（不是 goal 残件，是纯机制）：
  `kernel/swarm/expression_evaluator.py`（255 行，原 `apps/goal/expression_evaluator.py`）
  —— 分支条件 DSL，消费者只有 `SwarmRuntime._apply_branches`，与 goal 台账无关；
  连带修复 `swarm/runtime.py` 的**模块级 resolve 缺陷**（`backtest.script` /
  `backtest.decide` 在 import 时解析，apps 未加载即抛 CapabilityNotFound——
  这个既存 bug 在批次 2 删包后暴露，现改为闭包内 resolve，与
  `exec_registry.py` 的正确做法一致）。
  校验：门禁 `sync_architecture_diagram.py` 全绿（层体量/边数/违规逐项 AST 实测一致）·
  HTML 标签零错配 · 零未转义 `&` · viewBox 1600×1790 内零越界。

- **v9.0（2026-11-04，用户指示重画）**：**结构重画 —— 脊-翼双平面 → 四层堆叠 + 功能模块化**。
  这是自上图 v5.1 以来第一次**换掉版式**（此前 v6→v8.4 都是同版式的迭代）。
  ① **版式**：弃用「左脊 kernel / 右翼 apps / 面板三栏 + ①-⑦ 编号叙事」，
  改为**四个横向层带自下而上堆叠**（L1 顶 → L4 底），层间用蓝色箭头表示合法下行；
  层带左侧统一放 L 编号徽标（层名 / 中文职责 / 文件数 / 行数）。编号 ①-⑦ 作废——
  它们原本服务「工单旅程」叙事，与四层结构正交，同时存在会让人以为是两套分层。
  ② **功能模块化**（本次的核心诉求）：每层按**真实包结构**划功能域，不再按
  「是不是研究域」划——
  - L1：按渠道分 `api`(44/15.3k) · `cli`(70/13.3k) · `mcp`(2/0.9k) + 一个
    `组装根`(3/0.8k：gateway_wiring / rsi_wiring / tier0_snapshot)
  - L2：按业务能力分四个域——`研究域`(20.8k：study 11.8k+goal 6.1k+hypothesis 1.5k+
    autoresearch 1.4k) · `因子与数据`(33.0k：alpha_zoo 25.7k 最大单体) ·
    `回测与验证`(7.3k) · `进化与能力供给`(2.3k：evolution 1.9k + capabilities 0.4k)
  - L3：按机制职责分三个子带——`控制面`(5.6k) · `执行面`(6.1k) · `能力面`(24.8k)
  - L4：按技术能力分六块——llm 4.7k · storage 2.0k · utils 1.9k ·
    observability 0.9k · web+git 0.7k · 其余 0.6k
  ③ **新增两张表**：「层职责与模块化原则」（每层的职责边界 + 划分依据）与
  「模块间协作：三条通道」（下行调用 / 端口回调 / 事件流）。后者是本次重画
  想讲清楚的关键——**端口回调**（apps 注册、kernel 解析）是 L3→L2 从 46 降到 0
  的唯一手段，不是靠把 import 藏进函数。
  ④ 数据全部 AST 重测：**984 文件 / 144,086 行**（L1 120/30.3k · L2 625/66.1k ·
  L3 175/36.7k · L4 64/10.9k），跨层边数 L1→L2 171 · L1→L3 120 · L1→L4 53 ·
  L2→L3 39 · L2→L4 48 · L3→L4 48，上行违规 L2→L1 1 · L3→L1 2 · **L3→L2 0**。
  ⑤ **刻意未变**：四层归属表、门禁口径（ADR-000 物理口径）、棘轮基线机制、
  9 触点白名单——重画只动版式与模块分组，不动任何判定规则。
  校验：HTML 标签零错配 · 零未转义 `&` · viewBox 1600×1790 内零越界 ·
  7 项跨层边数与 4 层体量逐项对照 AST 实测一致。

- **v8.5（2026-11-04，RSI 三阶段落地后刷新）**：**数据与状态刷新，结构不变**。
  ① ⑦ 矩阵与棘轮重测（AST 实测，非推算）：**上行违规 55 → 3**——
  簇A `L3→L2` 46 → **0**（G2b 端口化完成，CapabilityRegistry 38 kind 已注册）；
  L4 簇（`L4→L2` 2 · `L4→L3` 4）→ **0**；剩余仅 `L3→L1` 2 + `L2→L1` 1。
  下行边数同步刷新：L1→L2 171 · L1→L3 120 · L1→L4 53 · L2→L3 39 ·
  L2→L4 48 · L3→L4 48。② ②Gateway 盒：容量组闸 ⏳→✅（chat 32 / study CPU-1 /
  cron 2，满→留队不留失）、CapabilityRegistry ⏳→✅、**新增** Ticket/Assignment
  行（3 态 + 七态 · 崩溃恢复 · Worker Slot 订阅）。③ 右翼新增「RSI 进化域」
  盒（apps/evolution 1.09k 行：holdout 只读 · EvolutionAgent 5 工具 ·
  validation_gate ±20%/3 次回滚 · golden_eval 信号）。④ ⑥ 区「基因组（RSI）」
  盒由黄色待建改蓝色已建：Tier 0 冻结快照 · Tier 1 四类已通 · Tier 2 研究产物 ·
  版本链持久化 · 提案应用后才评测。⑤ 右栏体量速览重测为**按层统计**
  （L1 120 文件 30.3k · L2 625 文件 66.1k · L3 175 文件 36.7k ·
  L4 64 文件 10.9k，含 `scripts/templates` 不计入口径不变）；落地状态加
  「RSI 三阶段」一行，⏳ 列表去掉容量组闸、加 graph_shape。⑥ 接缝规范
  「L2 统一注册 ⏳」→✅、「L3 一致性套件 ⏳」删除（未列入本阶段），新增
  「组装根在 L1」。
  依据：`scripts/arch/check_dangling_imports.py --layers` 实测 +
  逐层文件/行数 AST 统计；提交 `7aaec632`（接线+持久化）、`c2d2af33`
  （Tier 1 四类 + golden eval）。脊-翼双平面与四层骨架经复核仍成立，不重画。

- **v8.4（2026-11-04，物理分层收官后刷新）**：**数据与路径刷新，结构不变**。
  ① 「成员与体量速览」重测：apps 22 包 57.6k → **31 包 62.3k（45%）**、
  kernel 35.7k → 34.6k（控制面 4.1k→4.5k、执行面 6.0k→6.1k、能力面
  24.6k→24.0k/agent 21.7k）、interfaces api 15.2k→15.3k、foundation
  10.8k→10.7k；② 门禁行「基线 107」→「上行 49 条登记 · L4 簇 0」
  （ADR-000 口径重算 + graphify 交叉验证修正）；③ 两处旧路径
  `core/study/`→`apps/study/`、`core/tools`→`apps/tools`。
  依据：graphify 重扫（45982 节点 / 89897 边）+ 逐层 LOC 实测；
  四层骨架与脊-翅双平面布局经复核仍然成立，无需重画。

- **v8（2026-11-03）**：图归结构——每盒=名字+一行本质+盒级状态；台账外移（决策记录→discussion-record §3、依赖阶梯→并入 ⑦ DAG、版本史→本文件）；删「与现状的差别」表；SVG 文本元素 ~240→~130；画布 2170→1830。
- **v8.3（2026-11-03，ADR-000）**：⑦ 矩阵与簇数字刷新为 ADR-000 物理口径（每语句计 1）——上行违规 55 = L2>L1 1 + L3>L1 2 + L3>L2 46 + L4>L2 2 + L4>L3 4；旧双计口径（107/92/102）作废。
- **v8.2（2026-11-03，评审后）**：③ 带内补能力面条带（24.6k 六包，W3-7）；⑦ 矩阵加口径待定标记（实跑 L3→L2=102 vs 图面 92，ADR-000 决议后刷新）；两处简化备案（端口回调实为两跳、④ 旅程序≠时序）入设计文档 §2.4.1。
- **v8.1（2026-11-03）**：加宽美化——画布 1600→1880 · 页面 max-width 1660→1940 · 各段彩色编号徽标 · 三列间距与翼内盒距优化。

v5.1 → v6：入口层塌进 Gateway，管线 8 段收 4 段，回执两态。v6 → v6.1：mailbox 落细则（先序后闸 / cap 20 / 老化）、新增容量组闸横带、home_session 落图、执行面标 T1/T2/T3。v6.1 → v6.2：执行面三 runtime 收敛为 **GraphEngine 唯一 runtime** + 四种节点类型 + preset 治理分级；DagRuntime 除名。数据层拆分：平台数据面并入平台虚线框，研究域独立成第 ⑤ 层。

- **v6.2 → v6.3（2026-10-30）：对照代码逐格核实，虚实线与 ✅/⏳/⏸ 标注对齐实现现状。**已上线：E1 契约/两态回执、mailbox（cap20+溢出摘要+4 队列模式）、SessionController 1a 最小版（P-4 身份）、GraphEngine（llm/react · react 内部=AgentLoop 平移）、fanout-t2→ExecutorBridge、ContextHub（接线+仲裁+压缩同源）、不变量 ×5+verify_all、统一库事件化（B4 单一写者）、文档治理。建而未接线 ⚠：SessionFence、PlatformStore。仍未建 ⏳：RuntimeHub、容量组闸、优先级老化、steer 真注入（现为降级桩）、独立 cpu/graph 节点、Director、RunContext、方法论归位 core/study/method/、P-3/P-5、每 session 分库、Scheduler 唯一、PROTOCOL_VERSION、宿主进程（qs gateway）。挂起 ⏸：P5-1c 删旧 chat 分支（唯一待办，docs/archive/2026-10/todo-remove-legacy-chat-branch-20261027.md）。**R1（2026-10-30）：fence 挂根 ✅**——SessionFence 接进 SessionController（claim→validate→release），INV-4「产物落库必过 fence」进注册表，⚠ 孤儿清一个；剩余孤儿仅 PlatformStore。详细勘查见 docs/archive/2026-10/stage-r1-fence-wiring-design-20261030.md。**R2（2026-10-30，E1=C 用户拍板）✅**：常驻消费者（server.py 宿主启停 · grace）、run.answer 事件、/chat/send receipt 型 + ?wait=true（无消费者回落同步）、steer 真注入（fence 路由 + react 安全点合批 + 滞留晋升，降级桩删除）、优先级老化（aged>interrupt>FIFO · work.aged · ConfigHub aging_s）。设计见 docs/archive/2026-10/stage-r2-consumer-steer-aging-design-20261030.md。**R3（2026-10-30，勘查后重定界）✅**：CLI/TUI 入口核实早已接（更正此前误判）；study 早已走 automation mailbox（阶段 3c/4）；真实缺口=同 session 跨 kind 裸并发——fence 域统一为物理 session id，chat 消费者跳过被占 session、study 执行占栏/等待/释放，session=串行域对全部 kind 成立。设计见 docs/archive/2026-10/stage-r3-session-domain-design-20261030.md。**R4（2026-10-30，D2=延后分库）✅**：P-5 指针四件套与 ArtifactChannel 本就完整（13 测试）但零调用方——现接进生产：singleton 注入 data_dir、Gateway 暴露 artifacts、study round metrics 超 64KB 转指针落 D-4 目录并登记、回测结果是第一个真实用户；PROTOCOL_VERSION 成为事件信封唯一来源，replay 遇不匹配版本明确拒绝（不猜语义）。设计见 docs/archive/2026-10/stage-r4-data-plane-design-20261030.md。**R5（2026-10-30，勘查后判定大部分不做）**：cpu 节点不做（等价物已有：回测后台子进程 + fanout-t2 进程池）；graph 子图不做（零用户）；subagent lane 不做（subagent 当前同步串行，无并发问题）；只建 **ModelHub**——provider/model 解析唯一入口，未知 provider 拒绝不降级。三项「不做」的重开条件见 docs/archive/2026-10/stage-r5-execution-plane-assessment-20261030.md。**R6（2026-10-30，分档）**：✅ Director 确定性版（停因→建议，只发 study.directive 事件，不读预算不自动开 run）；⏸ 方法论归位延后（前置=先解 phase_engine↔runner 循环导入）；⏹ RunContext 包对象不做（机制已以散装 state 键存在）；⏹ preset Tier 0 治理不做（无可被外部修改的 preset 文件）。见 docs/archive/2026-10/stage-r6-research-domain-assessment-20261030.md。**R7/B1（2026-10-30）**：修复 R2 引入的三处技术债（sink 累积导致重复 SSE、wait_turn 返旧结果、结果无界）；interrupt 语义落地——CancelScope + 引擎节点边界取消，被取消的 run 不发 run.answer、栅栏照常释放、下一 turn 立即接上。见 stage-r7-r2-techdebt-design / stage-b1-interrupt-design。**B2（2026-10-30）✅**：fold 重建落地——recover() 从事件流恢复 turn 计数（续号不撞号）、session_key→uuid 映射（同 key 落回同一 aggregate）、started 无 terminal 的 run 标 run.interrupted（幂等）；auto_resume=false 兑现（mailbox 待办不重建，重试靠重提）；宿主 server.py 先 recover 再启消费者。见 stage-b2-fold-recovery-design。**B3（2026-10-30）**：勘查判定「7 阶段」与现有事件生命周期一一同构（单线程串行下无并发可管理）、「6 正交真值」从未被定义（所属 RunHandle 形态已被 v6 取代）——两者 ⏹ 不做；真缺口是 receipt 契约的非阻塞 poll 端，落地 session_status 快照。见 stage-b3-controller-assessment。**接缝加固（2026-10-30）✅**：用户提出拆分评估 → 结论「分而不拆」——平台→研究域实测零反向依赖、研究域→平台仅 9 触点，用门禁锁死（平台 7 子层 × 研究域 5 成员全禁反向 + 9 触点白名单）；顺带发现并修正 hanging_events 错放 study/（它是平台防护件，迁 observability）。见 docs/seam-hardening-design / seam-contract-platform-research-20261030.md。**解循环导入（2026-10-30）✅**：phase_engine↔runner 互为 import 的实质是 8 常量+1 枚举+1 纯函数住在 runner——下沉 study/contracts.py，依赖变 DAG（runner→phase_engine→contracts），AST 门禁防回潮。见 break-circular-import-design。**覆盖率工程（2026-10-30）✅**：.coveragerc + scripts/cov_gate.py 分档门禁 + 156 个新单测；A 平台内核 91.2% ✅、B 87.5%、C 88.5%（活跃核心 88.8%，起点 86.6%）。剩余缺口为异步执行路径，归集成测试。见 docs/coverage-90-plan。**D1 方法论归位（2026-10-30）✅**：勘查修正搬家集——phase_engine 仍依赖执行侧（graph_preset/scenario_router）留作「引擎的方法适配器」，6 个纯方法件（aegis/attribution/claims/guidance/metric_targets/review_loop）归位 core/study/method/，门禁禁 method→engine。见 docs/d1-method-relocation-design。**深度统一 B1（2026-10-31）✅**：SwarmRuntime 自研层循环退役，`execute()` 变为 SwarmPreset→GraphEngine 编译器（每层 split→fanout→join 链，层屏障语义保真）；契约零改动，三个消费面（goal workflow / CLI swarm / MCP swarm）一行未动。引擎侧仅加 opt-in `include_result`。见 docs/deep-unification-graph-engine-design。**B2 ✅**：checkpoint 收口——层间自动存档 + `/goal checkpoint resume` 真续跑（此前只展示）。**R-A（2026-10-31）✅**：`branches[].action=retry` 此前是空转（加回层又被「已有结果即跳过」过滤掉），改为显式 retry_queue 穿过图状态，新字段 `branch_max_retries` 封顶。**workflow 迁引擎 R-B0…R-B4（2026-10-31）✅**：`node_outputs` 补 `output` 列（老库自动 ALTER）→ `WorkflowGraphCompiler`（影子验证 7/7）→ 主路径切换 + `resume_from_store`（修掉「重启即卡死」：此前 /approve 依赖内存实例，进程重启就 404）→ 编译器内 replan 环 + 删旧段循环（executor.py 708→391 行）。**产品缺陷三修（2026-11-01）✅**：审批决策竞态（提前到达的决策被 clear+drain 丢弃，最长阻塞 approval_timeout=1800s）；JWT 密钥被 Windows 文本模式写坏（`os.open` 缺 `O_BINARY`，0x0A→0x0D0x0A，重启后所有 token 失效；另有 `read_bytes().strip()` 剥掉随机密钥首尾空白）；goal 证据在 retry 后重复计数。**验证矩阵（2026-11-01）✅**：`scripts/run_test_matrix.py` 按 lane 分进程 + 文件分片并行，PR 门 393s→179s，完整三层 7.5 分钟；`scripts/pytest_shard.py --exclude` 修掉「标记 slow 的大文件独占分片后被全反选→空转」。见 docs/guides/test-marker-lanes-design.md §9。

- **v6.5 → v7（2026-11-03）：四层骨架叠加（画框架定方向 · 不改代码）**——①渠道=L1 interfaces · ②Gateway+③执行面=L3 runtime · ④平台数据=L3 事件语义+L4 存储机制 · ⑤研究域=L2 domain；新增 L4 foundation 横带与 ⑥ 四层依赖总表（与 test_layer_matrix 门禁同源：跨层 import 矩阵 + 上行违规 107 分簇棘轮；簇A 92=工具层绑定领域能力→G2b CapabilityRegistry 端口化，簇B/C 15→G2a 注册反转/归位）。依赖轴（静态 import）与数据流轴（管线蓝箭头）正交——研究域居管线下游、依赖序上游。见 docs/design/layered-skeleton-design-20261103.md。

- **v7 → v7.1（2026-11-03）：层命名定稿 llmwikify 式四层（用户拍板）**——L2 domain→**apps** · L3 runtime→**kernel**（物理目录不动，仅逻辑层名）；Gateway 组件归类上墙：kernel×9（协议端点·mailbox·SessionController·容量组闸⏳·ModelHub·ContextHub·EventStore·Scheduler⏳·RunContext）+ apps×1（**Director**——研究语义策略件，物理 core/study/，由 runner 轮末调用，控制面经事件流消费其产出；编排位置 ≠ 依赖层）。

- **v7.1 → v7.2（2026-11-03）：kernel 三子带 + 两处归位（用户确认）**——逐包核查后修正：core/tools（data_clean/transforms 研究数据工具实现，仅被 config_runner/data_readiness/db 引用）→ **L2 apps**；core/registry.py（Generic typed Registry 基类，零语义）→ **L4 foundation**；两处经 import grep 验证为违规中性（基线 107 不变）。kernel 显式拆三子带（只改体现不改 import 规则）：控制面 ~4.1k（gateway/orchestration/events/session/scheduled_research）· 执行面 ~6.0k（runtimes/workflow/swarm）· 能力面 ~24.6k（agent 独大 + context/hooks/permission/memory/skills）。foundation 写明晋升门槛（去 run/session/研究语义可独立成库；候选：ToolContext 去 session 化、hooks 泛化）与反向红线（禁止为平衡假厚）。

- **v7.2 → v7.3（2026-11-03）：foundation 独立成带（用户拍板）**——从 ④ 平台数据中剥离「L4 存储机制」，④ 收纯为 L3 事件语义；⑥ foundation 独立底座带（llm/storage/registry/observability/utils/web/config_loader/errors/git · 晋升门槛与反向红线上墙），管线=渠道→平台→研究域→foundation；原 ⑥ 总表改编号 ⑦。成员零变化（纯图面重构）：④ 的 P-1/P-2 语义在 L3（EventStore/projector），其 sqlite 写入实现（PlatformStore/event_schema）在 L4。

- **v7.3 → v7.4（2026-11-03）：管线重画为脊-翼双平面（用户拍板方案 A）**——左脊=平台 kernel（②Gateway→③执行面→④平台数据），右翼=研究域 apps 并列（不再纵排垫底），两条缝带方向：紫虚线=端口回调（kernel 驱动 apps · G2b 后 import 向下）、蓝线=服务使用（apps 用平台 · 9 触点白名单）；⑥ foundation 全宽垫底被脊翼共同依赖；翼内加依赖阶梯（L1→L4 + 各层行数）、⑦ 加依赖 DAG 七边图。编号保留于门禁/⑦（DAG 拓扑排名），管线主体不再依赖编号叙事。结构零改动：成员表与 test_layer_matrix 棘轮原样。曾评估「真合并三层」并否决（合法化 92 条病灶 / 溶解 IP 护城河 / 推翻 10-26 研究域独立拍板）。

- **v7.4 修正（同日，用户指示）**：依赖阶梯移出翼、入右栏侧边栏（编号叙事随阶梯迁移）；翼内改放「成员与体量速览」（apps 22 包 / kernel 三子带 / interfaces·foundation 的 LOC 分布）。

- **v7.4b（同日，用户指示）**：ConfigHub 与 CapabilityRegistry 从右栏提入架构主体——落在 ②Gateway 与 ③执行面之间的「配置与供给」走廊位（CapabilityRegistry 虚线盒紧贴端口回调箭头起点：它是 G2b 的正题，切 0.6/0.7）；右栏仅余护栏与台账（InvariantLayer · 质量门禁 · 落地状态 · 决策记录 · 依赖阶梯）。

- **v7.4c（同日，用户指示）**：「成员与体量速览」是审计统计不是架构组件——移出研究域翼、入右栏（依赖阶梯下方）；翼内重新等距排布，只留业务组件（方法论/执行组件/账本/市场数据/回测/数据工具/两条缝）+ 右栏指路。
