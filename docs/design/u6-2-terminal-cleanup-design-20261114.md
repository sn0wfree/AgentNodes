# U6-2 终局清理：legacy 循环体退役 + 引擎切换（方案，R-014）

> 2026-11-14 · 前置已齐：全量验证网恢复（7268 passed / -n 8 / 101s，ff7ca103）
> · 上游：B2-①（T1–T8 已入库，T11 4 条 strict xfail 为验收钉子）/ 批次 12 S1（引擎默认已翻 skeleton）

## §0 结论

1. **范围修正一处**：事件桥（`_make_event_bridge`）**移出 U6-2**——真值侦察发现
   统一引擎自己在用（`loop_engine.py:147` `_attach_event_source`），它是 U3 的
   活机制不是 legacy 遗物。桥退役留在冻结的 B2-②。
2. **StopPolicy 处置判定**：实测 **legacy-only**——唯一消费链
   `legacy._run_loop_core:1225 → _check_budget:739 → StopPolicy(runtime/policy.py)`；
   统一引擎走 `BudgetDecider`（deciders.py:196）。处置 = **随 legacy 整体退役**，
   两套预算账合一（invariants.py:148 记的"两套"就此收敛）。
3. **批次切分（R-020：主体与删除分 commit，删除置末）**：
   - **U6-2-T11（先行，小）**：过渡壳 + `step_adapters.py` 整模块 + `should_run`×3
     → 4 条 strict xfail 转正。一个删除 commit。
   - **U6-2-A 主体批（非删除）**：时序钉子移植 + tool 测试 async 化迁移。
   - **U6-2-A 删除批（大）**：引擎切换 + `_run_loop_core`（258 行）+ sync 孪生
     + StopPolicy 链 + env 注册 + 三个对照网退役 + `legacy_audit` 翻转。
     一个删除 commit。
4. 每个删除 commit 后立即全量（R-020 工序）；全量网已恢复（101s/次，成本可承受）。

## §1 真值表（全部实测，2026-11-14）

| # | 事实 | 证据 |
|---|---|---|
| 1 | 引擎切换点：`arun` L910-925；skeleton 默认（批次 12 S1）；`AGENTNODES_ENGINE=legacy` / `AGENTNODES_SKELETON=0` 显式回退 | `loop.py:900-925` |
| 2 | `_run_loop_core` 唯一真调用 = `loop.py:925`（legacy 分支）；其余 38 处引用全是 docstring/注释/审计常量 | 全仓 grep + `legacy_audit.py:91`（审计器自己确认） |
| 3 | `_run_loop_core` = 258 行（L1113-1371）；loop.py 总 1581 行 | 实测 |
| 4 | 事件桥被统一引擎消费：`loop_engine.py:147` `loop._make_event_bridge()` | `loop_engine.py:134-159` |
| 5 | StopPolicy 唯一活链 = `_check_budget`（仅 `loop.py:1225` legacy 内调用）；`runtime/policy.py` 三出口（StopDecision/StopPolicy/limits_from_config）零其他生产消费者 | grep |
| 6 | sync 孪生现状：`_execute_tool_batch`（L301，**零调用者**——U5.5 已翻）；`_execute_tool_call` sync 包装 7 调用者全在 tests；core 的 `async_mode` 参数分支 L160/183 | grep |
| 7 | `test_tool_execution_step_hooks.py:175` 已有门：「同步孪生引用回流（应恒走 async 孪生）」；`test_architecture_gates.py:284` 仍断言 sync batch 存在（需翻转） | grep |
| 8 | `engine=` 实参：生产 src **零调用**；测试 17 处（parity 8 + 等价 1 + legacy 网 1 + goal continuation 1 + live 2 + audit 模板 4） | grep |
| 9 | 三个对照网：`test_engine_parity.py`（双引擎对比 ×8 场景）/ `test_loop_engine_equivalence.py`（3 测试，engines_agree 参数化场景）/ `test_legacy_engine_net.py`（legacy 专用网） | grep |
| 10 | 时序钉子：`test_dsh_deep_integration.py::TestRunLoopCoreTiming` 3 测（inbox drain 先于 injector / per-iteration / post-response）直接驱动 legacy 内部 | L237-289 |
| 11 | `step_adapters.py`（LegacyStepAdapter + FnStreamingHandler）**零生产调用者**，仅 `test_core_types.py` 使用 | grep |
| 12 | `should_run`×3（compaction/llm_call/pre_run）+ `test_compaction_step_migration.py:110` 1 测 | grep |
| 13 | `legacy_audit` 已接 CLI（`config audit-legacy-engine`，只读）；其审计对象就是 legacy 存在性本身 | `config_cmd.py:255` |
| 14 | settings.py 注册两 env（L195-198）；`test_settings.py:344,479-485` 钉在 ENV_TABLE | grep |

## §2 逐对象处置（R-015 四项 + R-016 出口网对照）

### 2.1 引擎切换 + `_run_loop_core`（A 删除批）

- **arun**：删 engine 分支与参数，方法体直达 `_run_via_skeleton`。
  `run()`（sync 经 `_run_coro_in_sync(arun)`）与 `run_stream` 不受影响。
  生产零调用方传 `engine=`（真值 #8）。
- **`_run_loop_core`（258 行）整体删除**。出口对照（R-016）：
  inbox drain（`_drain_inbox` 公共方法保留）、injector 时序、budget 补发、
  `_trace_and_emit` 各事件——逐项在 §6 验证表中对应引擎侧钉子。
- **`_get_response` 的 async_mode 分支收敛**（L1360-1417 区域）：legacy 恒传
  True，sync 分支随 legacy 死。core 去参数化，签名收敛为纯 async 形态。

### 2.2 loop_tools.py 同步孪生（A 删除批）

- 删 `_execute_tool_batch`（sync 批，零调用者——R-015 调用者项已满足）；
  `_execute_tool_call` / `_execute_tool_with_heartbeat`（sync 包装）随之删；
  `_execute_tool_call_core` / `_execute_tool_with_heartbeat_core` 去 `async_mode`
  参数化（分支 L160/183 收敛为恒 async）。
- 幸存形态：`_aexecute_tool_call` / `_aexecute_tool_with_heartbeat` / `_aexecute_tool_batch`。
- 门翻转：`test_architecture_gates.py:284` 改断言 `_aexecute_tool_batch` 存在。
- 测试迁移：`_execute_tool_call`×7、`_aexecute_tool_call`×3、core 直调
  `async_mode=False`×6（dsh_comprehensive 2 / dsh_deep 4 / capability_smoke 1 / dead_params 1）→ 改 async 形态。

### 2.3 StopPolicy 链（A 删除批）

- 删 `loop.py` `_check_budget`（L739-748）+ import；`runtime/policy.py` 整模块；
  `runtime/__init__.py` 三出口收敛。
- 幸存者：`BudgetDecider`（deciders.py:196，引擎在用）+ `loop_spec.BudgetSpec`。
- `gateway/invariants.py:148` docstring 的"两套"表述同步改写。

### 2.4 三个对照网退役（A 删除批，前置=钉子移植）

- `test_engine_parity.py`（8 场景双引擎对比）→ **拆解**：skeleton 侧断言
  （hooks 计数 / injector post_calls / 事件流形状 / 注入攻击防护）提取为
  engine-path 常驻测试（新文件 `test_engine_semantics.py` 或并入既有文件）；
  legacy 侧断言与对比结构退役。
- `test_loop_engine_equivalence.py` → 退役（等价性对象消失）。
- `test_legacy_engine_net.py` → 退役。
- `test_dsh_deep_integration.py::TestRunLoopCoreTiming` 3 测 → 重定向到
  engine 路径（inbox drain / injector 时序的行为断言保留，驱动方式从
  legacy 内部改为 `arun()` 黑盒 + 事件/状态观察）。
  `TestPrepareRunIntegration` / `TestExecuteToolCallGuardIntegration` 等其余
  类已走公共入口，不受影响。

### 2.5 T11 策略层（T11 批，先行）

- `_call_step` 过渡壳（loop.py:654-667 `execute(ctx, async_mode=True)` 分支）删除。
- `step_adapters.py` 整模块退役：`LegacyStepAdapter` 删；
  `FnStreamingHandler` 仅测试使用 → **内联进 test_core_types.py**（测试工具私有化）。
- `should_run` ×3 删（compaction.py:41 / llm_call.py:46 / pre_run.py:48）
  + `test_compaction_step_migration.py:110` 对应测试删。
- `_core_types.py:117` 对 LegacyStepAdapter 的 docstring 引用改写。
- **4 条 strict xfail 转正**：strict xfail 在目标达成后会 XPASS→fail，
  删除 marker 后即常规绿断言。

### 2.6 引擎开关 env 注册（A 删除批）

- `foundation/settings.py:195-198` 两条 Setting 删除；
  `test_settings.py:344,479-485` 更新。
- 外部兼容性：部署环境若设 `AGENTNODES_ENGINE=legacy`，删除注册后
  `get_str` 返回空 → 无害（默认 skeleton）。记入 release note（遗留账 §8）。

### 2.7 `legacy_audit` 翻转（A 删除批）

- 现职责 = 审计「legacy 引擎真调用/定义/开关读取/engine="legacy" 传参」的
  **存在性**。U6-2 后翻转为**绝迹断言**（anti-resurrection gate）：
  四类符号在 src+tests 全零，命中即 fail。
- `test_legacy_audit.py` 的注入测试改为「注入 legacy 残留 → 必须红」
  （R-019：网的价值 = 注入变红）。
- CLI `config audit-legacy-engine` 保留（语义变为"legacy 残留审计"）。

## §3 范围修正：事件桥移出

`loop_engine.py:147` 在每次 run attach 时调用 `loop._make_event_bridge()`——
桥是 U3 的事件汇聚机制，服务 **run_stream 消费**，与 legacy 循环体无关。
U6-2 删桥 = 砍掉引擎自己的事件入口。桥的替换（dispatcher 化）属于 B2-②
生产端收敛的一部分，维持冻结。

## §4 批次切分与工序（R-020）

| 批 | 性质 | 内容 | 验证 |
|---|---|---|---|
| U6-2-T11 | 删除 commit | §2.5 全部 | 全量跑① + 4 xfail 转正确认 |
| U6-2-A 主体 | 非删除 commit | §2.4 的钉子移植（parity 拆解 + timing 重定向）+ §2.2 测试 async 化 | 全量跑② |
| U6-2-A 删除 | 删除 commit | §2.1/2.2/2.3/2.6/2.7 全部 + 三个网退役 | 全量跑③ + legacy_audit 翻转确认 |

删除清单在删除前列出（本文件 §2 即清单）；主体与删除分 commit；
删除后立即重跑全量再确认（R-020）。

## §5 行为变化（显式）

1. `AGENTNODES_ENGINE` / `AGENTNODES_SKELETON` 环境变量失效（未注册，
   设了无效、无提示）——skeleton 成为唯一路径。
2. `arun(engine=...)` 参数删除（生产零调用方；测试 17 处随批迁移）。
3. 工具执行只剩 async 形态；sync 包装与 sync 批消失（U5.5 已把生产调用方
   全部翻到 async，本批只是删掉遗骸）。
4. `config audit-legacy-engine` 语义从「legacy 存在性审计」变为
   「legacy 残留审计（应零命中）」。
5. 预算机制只剩 `BudgetDecider` 一套（StopPolicy 退役）。

## §6 验证表（R-019：每个网说明它变红时抓住了什么）

| 网 | 钉住什么 |
|---|---|
| 4 条 strict xfail → 转正常绿 | 过渡壳/LegacyStepAdapter/should_run 绝迹 |
| `legacy_audit` 翻转 + 注入测试 | legacy 四类符号（真调用/定义/env 读取/engine= 传参）复活即红 |
| `test_engine_semantics.py`（parity 拆解产物） | hook 计数/injector 时序/事件形状/注入防护在唯一引擎上成立 |
| `TestRunLoopCoreTiming` 重定向版 | inbox drain 先于 injector、per-iteration/post-response 时序 |
| `test_architecture_gates.py` 翻转 | sync 孪生回流即红 |
| 全量 ×3 | 每批零回归 |

## §7 风险

| # | 风险 | 缓解 |
|---|---|---|
| 1 | legacy 内部时序（inbox/injector/budget 补发）在引擎路径缺失对应实现，删 legacy 后行为回退 | parity 网今天就在**双引擎对比**这些行为——移植前先跑 parity 确认 skeleton 侧已全绿，才允许删 legacy |
| 2 | `_get_response` 去参数化触碰流式分支（text_delta 等） | 该函数已被引擎经 `_get_llm_response`（adapters）调用，分支收敛只影响 legacy 专用路径；全量兜底 |
| 3 | 外部部署设了 `AGENTNODES_ENGINE=legacy` | 删除注册后静默回 skeleton；release note + changelog |
| 4 | 258 行删除夹带隐式副作用（如模块级状态） | 删除清单逐行过（§2.1 已列）；全量③兜底 |
| 5 | 对照网退役丢行为 | 拆解而非整删：skeleton 侧断言先落地新文件，旧文件后删（同 commit 内原子） |

## §8 回滚与遗留账

- 每批独立 commit，revert 粒度 = 批。
- 遗留：① release note 记 env 退役；② B2-②（桥/dispatcher）维持冻结；
  ③ `run_stream` docstring 的 engine="legacy" 陷阱注释（L983-984）随批删除。

## §9 讨论评估要点（供决策）

1. **批次顺序**：T11 先（小、独立、4 钉子尽早转绿）→ A 主体 → A 删除。
   备选：A 先 T11 后——无技术理由，仅偏好；推荐前者。
2. **对照网的处置深度**：拆解保留 skeleton 侧（推荐）vs 整删（丢行为 vs 省事）
   ——推荐拆解，`test_engine_semantics.py` 承接。
3. **`arun(engine=...)` 参数**：直接删（推荐，内部 API + 生产零调用）vs 保留
   参数忽略 + DeprecationWarning（过度兼容）。
4. **`legacy_audit` 翻转 vs 退役**：翻转（推荐，成为永久 anti-resurrection 门）
   vs 删除（失去注入防护）。
5. **execution 顺序**：设计确认后三批连续做（预计单批 30-60 分钟 + 101s 全量），
   还是一批一确认？推荐连续做、每批一 commit，最后汇总报告。
