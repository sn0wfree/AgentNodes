# Phase Engine 重构计划 — 步骤再评估（勘误版）

> 日期：2026-10-08 · 基线：commit `3eea546`
> 性质：对 `docs/archive/2026-10/architecture-harness-recommendations-20261008.md` §1.3 的**勘误 + 步骤重评**。
> 方法：读真实代码后修正口头方案（原方案有三处与代码不符）。

## 1. 勘误：三处与代码不符的判断

原文档 §1.3 与前次口头讨论中的 `EngineProtocol` 方案基于印象，读码后修正如下。

### 勘误 1：分派点不在 runner，在 phase_engine

| 原判断 | 事实 |
|---|---|
| runner 同时认识两个引擎，抽 Protocol 让 runner 只依赖接口 | **runner 不认识引擎**——`runner._run_one_round_impl` 只调 `run_round_phases`（`runner.py:735`，局部 import）；分派在 **`phase_engine.py:128-141`** |

### 勘误 2：双引擎不是并列可替换，是 if/else + 局部变量 shim 约定

分派逻辑（`phase_engine.py:128-141`）：

```python
engine = getattr(study, "engine", None) or "phases"      # 默认 phases
if os.environ.get("SR_STUDY_DAG_ENGINE") == "1" and engine == "phases":
    engine = "langgraph"                                  # env 遗留开关
if engine == "dag":
    engine = "langgraph"                                  # 'dag' 是假选项，静默映射
if engine == "langgraph":
    lg_result = runner._run_round_via_langgraph(...)      # 分支 A
    # line 159-205: 手工 shim 出 phases 分支的变量名
else:
    # 分支 B: run_researcher_phase → execution → evaluation
```

关键事实：
- 两分支靠**同名局部变量**（`researcher_output` / `metrics` / `eval_result` / `exec_result` / `captured_predictions`）衔接 line 207 之后的**共享 finalization**
- langgraph 分支需要 ~20 行 shim 才能让 finalization 不 AttributeError（`phase_engine.py:159-205`）
- `engine='dag'`：store/API 仍接受，但**无 executor 存在**，静默映射为 langgraph（`phase_engine.py:137-141` 注释自认）
- `SR_STUDY_DAG_ENGINE` 环境变量是另一个遗留开关

### 勘误 3：`_run_loop` 复杂度 32 的来源是生命周期控制流，不是引擎分派

`runner.py:362-570` 的 32 分支来自：
- 主循环内的 cancel 分支（含 ARCHIVED 状态子判断）
- pause 分支（含「暂停中被取消」子判断）
- HITL resume 巨块（`runner.py:490-570`：run_dir 重建、state 重建、`_resume_round_langgraph` 调用、三种失败路径）
- budget / 终止判断

**引擎分派完全不在 `_run_loop` 里。**

## 2. 新发现：langgraph 分支 claims 被清空（疑似真 bug）

`phase_engine.py` claims 链路：

| 行 | 分支 | 代码 | 问题 |
|---|---|---|---|
| 190 | langgraph | `captured_predictions = _capture_round_claims(...)` | 捕获真值 ✓ |
| **205** | langgraph | `captured_predictions = {}` | **立即清空** ✗（相邻注释只解释 `eval_result`/`exec_result` shim，未解释此行） |
| 238 | phases | `captured_predictions = _capture_round_claims(...)` | 真值，**不清空** ✓ |
| 317-320 | 共享 | `_record_journal_and_regression(..., predictions=captured_predictions)` | langgraph 传 `{}`，phases 传真值 |

**影响假说**：langgraph 引擎下 journal phase-B（backfill）拿到空 predictions，与 phase-A（`_capture_round_claims` 内部 append）写入的内容不一致；phases 引擎正常。

**为什么现有测试没抓到**：`tests/test_langgraph_parity.py`（17 个测试）只验证 **output schema** 等价（docstring 自述），journal 是 side-effect 不在 schema 内。

**Step 0 将用新增测试验证此假说**（只新增，不改生产代码，先出证据）。

## 3. 修正后的步骤（替代原「EngineProtocol」）

| Step | 动作 | 风险 | 护栏 | 工期 |
|---|---|---|---|---|
| **0** | 新增 langgraph 分支 journal/predictions 断言测试 → 确认 line 205 是否真 bug → 若确认，**仅删/改该行**（最小改动） | 零（先测试后一行改动） | 测试本身 | 半天 |
| **1** | 定义 `RoundOutcome` dataclass：两分支各自构造返回值，finalization 只消费 outcome——把局部变量约定变**显式类型契约**。**对外签名 `run_round_phases(...)` 不变** | 中低（动 806 行函数内部） | `test_langgraph_parity.py` 17 测直接适用 | 1-2 天 |
| **2** | 分派查表化 `{"phases": ..., "langgraph": ...}`；同步收口 `engine='dag'` 假选项与 `SR_STUDY_DAG_ENGINE` env 遗留 | 低（纯移动，Step 1 契约已立） | Step 1 契约 | 半天-1 天 |
| **3** | `_run_loop` 生命周期状态机化：`cancel/pause/resume/terminate` 抽 `StudyLifecycle`，主循环变 `while not terminal: transition()`。**方向已修正：不是 engine step 化** | 中高（核心循环） | 12,366 全量 + eval 子集 | 2-3 天 |

### 逻辑链条

```
Step 0  用活证据（line 205）证明「shim 约定会藏 bug」
   ↓    同时堵住已知漏洞
Step 1  RoundOutcome = 把 shim 约定显式化为类型契约
   ↓    分支体缩短（C901 24 → 目标 ≤15）
   ↓    parity 测试从「碰巧过 schema」变「类型上难偏」
Step 2  契约已立 → 分派只认「谁产出 RoundOutcome」→ 查表自然成立
   ↓    此时删 'dag'/env 假选项是安全的
Step 3  分派与契约稳定后 → 才动 _run_loop 生命周期
```

**支点是 Step 1**：没有 `RoundOutcome`，Step 2 只是变量约定的搬家，Step 3 建立在流沙上。

### 护栏缺口（Step 1 验收时补）

parity 测试覆盖 schema、不覆盖 side-effect——Step 0 的 bug 正从这个缺口溜过。Step 1 验收应追加：**两分支跑完后 journal / state_store 落盘 diff 一致**。

## 4. 原文勘误指针

`docs/archive/2026-10/architecture-harness-recommendations-20261008.md` §1.3 的「`EngineProtocol`（`start_round`/`resume`/`snapshot`）」接口草案与「runner 认识双引擎」的前提**均已作废**，以本文档 §1-§3 为准；该条的优先级判断（study 心脏、先低风险后高风险、当前有护栏）仍然成立。

## 5. Step 0 验收记录（2026-10-08，commit `261a88f`）

### 5.1 执行

1. 新增 `tests/test_phase_engine_langgraph_claims.py`（仅新增，stub pattern 镜像自现有测试，不 import 测试模块）
2. 首跑 **红**（证据）：`phase A produced {'calmar': 0.6, 'sharpe': 0.35}, phase B received {}`
3. 修复：删 `phase_engine.py:205` 的 `captured_predictions = {}` **一行**；相邻 shim 注释扩两句说明为何 captured_predictions 不得 shim
4. 复跑 **绿**；ruff 干净（仅剩 pre-existing C901=24）

### 5.2 回归测试面

| 批次 | 范围 | 结果 |
|---|---|---|
| 核心 | `test_phase_engine_*` + `test_langgraph*` + `test_study_engine_dispatch` | 35 passed + 2 已知失败 |
| 扩面 | study / phase / langgraph / claims / attribution / goal / autoresearch / hitl / resume 共 **69 文件** | **1258 passed · 15 failed · 1 skipped**（118.5s） |

排除项：`test_engine_*`（26 文件）为回测引擎测试，与 study 引擎无关。

### 5.3 失败归因（before/after 精确 diff）

方法：`git checkout 261a88f~1 -- phase_engine.py` 还原修复前，跑同一 15 个失败测试，与修复后对比 FAILED 集合。

**结果：before 14 failed = after 14 failed，集合完全一致 → 修复零回归。**

14 个 pre-existing 失败（按约束未动，仅记录）：

| 类别 | 测试 | 现象 |
|---|---|---|
| 研究指标 | `test_study_real_world` ×3 | MetricEvaluation 断言失败 |
| 调度器 | `test_study_scheduler` ×3 | recover/duplicate 场景 |
| 存储 | `test_study_store::test_list_newest_first` | 排序 |
| 存储 | `test_study_store_finalization_ops::heartbeat` | 浮点时间戳 `>=` 精度 |
| 类型防御 | `test_phase_engine_researcher_defense::type_guarded` | `test_get_calls_are_type_guarded` |
| 图路由 | `test_langgraph_parity::test_no_duplicate_incoming_edges_under_hitl` | 期望 `novelty_gate→strategist`，实际 `novelty_gate→__end__` |
| goal | `test_goal_events::test_flush_persists_metadata` | 元数据 flush |
| HITL 重建 | `test_hitl_resume_rebuild` ×3 | `AutoresearchRunner has no attribute 'control'`（`runner_langgraph.py:106`，stub 不完整） |

另有 **1 个 flaky**：`test_study_dump::test_dump_with_studies` — 69 文件大跑 fail、单跑 pass（测试间污染，`goal_test.db` WinError 32 权限残留）。

### 5.4 遗留观察（不属于 Step 0 范围）

- `test_langgraph_parity` 的 HITL 边路由失败可能反映**图构建真问题**（novelty_gate 直连 `__end__` 而非 strategist）——值得单独立项，与 Step 1 的契约工作无关但同属 study 引擎
- `runner_langgraph.py:106` 的 `control` 属性缺失——测试 stub 与实现漂移

## 6. Pre-existing 失败修复记录（2026-10-08，Step 0 后授权批次）

§5.3 列出的 14 个 pre-existing 失败 + 1 flaky + 2 遗留观察，经授权后全部修复。

### 6.1 根因与修法

| # | 测试 | 根因 | 修法 | 类别 |
|---|---|---|---|---|
| 1-3 | `hitl_resume_rebuild` ×3 | `_bare_runner` 用 `__new__` 跳过 `__init__`，未提供 `control` 属性（`runner.py:148` 正式属性） | `_bare_runner` 补 `runner.control = ControlToken()` | 测试 |
| 4 | `researcher_defense::type_guarded` | `PHASE_ENGINE.read_text()` 无 encoding，Windows 默认 GBK 读 UTF-8 炸 | `read_text(encoding="utf-8")` ×2 | 测试 |
| 5 | `goal_events::flush_persists_metadata` | `Projector` 的 sqlite 连接**无关闭入口**，tearDown 删临时目录时 WinError 32 | **产品**：`Projector.close()`（幂等，持 flush_lock）；测试 tearDown 调用 | 产品+测试 |
| 6 | `study_dump` flaky | 同 #5 类 db 锁（大跑 fail / 单跑 pass） | #5 的 `Projector.close()` 间接覆盖；69 文件大跑验证通过 | 由 #5 覆盖 |
| 7 | `study_store::test_list_newest_first` | `list_studies` 的 `ORDER BY created_at DESC` **无 tiebreak**，同微秒内创建顺序不定 | **产品**：加 `, rowid DESC`（与 line 934 同模式） | 产品 |
| 8 | `store_finalization_ops::heartbeat` | `time.time()` 浮点精度 > datetime 微秒截断，同微秒内 `hb < before` | 断言加 `1e-6` 容差 + 注释 | 测试 |
| 9-11 | `study_real_world` ×3 | `_stub_researcher` 已迁至 `core/study/stub_agents.py`，测试仍从 `core.autoresearch` import | 改 import 路径 ×3 | 测试 |
| 12-14 | `study_scheduler` ×3 | ① `_setup` 硬编码 `workspace_path="/tmp/ws"`（POSIX，Windows 不存在 → recover 标 error）② duplicate 测试等 RUNNING 后立即 shutdown，round 未跑 | ① 改 `str(Path.cwd())` ② 等 `StudyStatus.ERROR` 终态再 shutdown | 测试 |
| 15 | `langgraph_parity::HITL 边` | **测试方法错**：`get_graph().edges` 静态视图不渲染条件边 targets（`ends=None` 无 path_map），且裁剪条件分支下游静态边；产品逻辑（`_gate_route` 条件路由）本身正确 | 重写断言：静态边读 `builder._all_edges`，条件路由**直接调用** `branches['novelty_gate']['_gate_route'].path.func` 验证运行时行为（normal→`[risk, strategist]`，aborted→`[END]`） | 测试（增强） |

### 6.2 产品改动汇总（2 处）

1. `api/session/projector.py` — 新增 `close()`（连接生命周期此前无公共出口）
2. `core/study/store.py` — `list_studies` 两处 `ORDER BY created_at DESC` 加 `rowid DESC` tiebreak

### 6.3 验证

| 批次 | 结果 |
|---|---|
| 修复目标测试（10 文件） | **67 passed, 1 skipped, 0 failed** |
| 69 文件全量回归（同 §5.2 口径） | **1273 passed, 1 skipped, 0 failed**（此前 1258 passed + 15 failed；+15 正好对应修复数） |

### 6.4 遗留（未处理，规模外）

- `ruff check tests/` 存在 34 个 pre-existing lint 问题（F401/F811/I001，集中在 `test_study_real_world.py`）——此前 lint job 只扫 `src`，未暴露；autofix 有语义风险（F811 redefinition），另立任务处理
- HITL 边测试依赖 langgraph 私有 `builder._all_edges` / `branches`——升级 langgraph 版本时需复查

## 7. 执行约束

- **他人代码最小触碰**：Step 0 只新增测试 + 最多一行生产代码改动；Step 1-3 每步单独评审后再动
- 每步独立 commit，可单独回滚
- 每步验收必跑：ruff C901（`phase_engine` 目标值）+ `test_langgraph_parity.py` + 受影响模块测试
