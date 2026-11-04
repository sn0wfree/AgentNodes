# 复用性与冗余评审（2026-11-04 · W11）

> 范围：全库 963 个源文件 / 138,324 行 + 700 个测试文件 / 157,105 行
> 方法：graphify 重扫 + 自建 AST 指纹分析 + 定点取证（**所有数字独立核验，不采信二手结论**）
> 目标：找出冗余与可精简处，并给出按设计模式匹配的改进方向

---

## 0. 先说方法论：三条被推翻的"看似显然"的结论

本次评审推翻了三处"看起来是重复、实际不能合并"的判断。这类误判在重构中代价极高
（合并后语义漂移，且已写文档承诺的行为变了），因此单列。

### 0.1 双份算子库：16 个同名函数，**没有一个是相同的**

`apps/alpha_zoo_ops.py`（238 行 / 22 函数）与 `apps/compute_factor.py`（1388 行 / 109 函数）
有 16 个同名函数。逐个比对函数体：

```
_apply  delta  rank  safe_div  scale  signed_power  ts_argmax  ts_argmin
ts_corr  ts_cov  ts_max  ts_mean  ts_min  ts_rank  ts_std  zscore
→ 逐字相同 = False   归一化后相同 = False   （16/16 全部不同）
```

**判定：巧合重复（coincidental duplication），不可合并。** 名字相同是因为它们都叫
"时序算子"，但实现语义不同（如 `ts_rank`：ops 版 27 行 vs cf 版 3 行，
前者带窗口参数处理，后者是简化版）。

> 这是 Fowler "Three Strikes" 判定表里最该说"别抽"的一格：
> **语义不同 → 别抽**。强行合并会引入 `mode=` 参数分支、造成布尔盲区，
> 且两边都有各自的测试做等价性保证。

### 0.2 `study/store.py` 的 4 个 interrupt 查询：语义不同

`get_pending_interrupt` vs `get_interrupt_for_round`、`get_latest_pending_interrupt`
vs `get_interrupt` —— 名字相近，AST 指纹判为重复，但逐行看：

| 方法 | SQL 差异 |
|---|---|
| `get_pending_interrupt` | `WHERE study_id=? AND round_num=? AND status='pending'` |
| `get_interrupt_for_round` | `WHERE study_id=? AND round_num=?` + `ORDER BY created_at DESC`（任意状态） |
| `get_latest_pending_interrupt` | `WHERE study_id=? AND status='pending'` |
| `get_interrupt` | `WHERE interrupt_id=?` |

**判定：不可合并。** 后两者的差异（pending vs 任意状态）正是 HITL 审批流程的语义核心
—— 合并会让"已批准的 interrupt"被当成"待处理的"返回。

### 0.3 `alpha_zoo` 的 468 个模块：是**生成数据**，不是代码冗余

精确重复 Top 榜几乎全是 `alpha_zoo`（`_ind_neutralize` ×18、`_where_ternary` ×10、
`_delay` ×17、`_rolling_sum` ×30…）。但：

- `apps/alpha_zoo_convert.py`（1011 行）是一套 **YAML ↔ Python 双向转换器**
  （`convert_py_to_yaml` / `convert_all_zoos` / AST visitor）
- 468 个 .py 是从 YAML 批量转出来的**因子公式实现**，每个是一个独立因子

**判定：这是「代码生成」的产物，重复是设计使然。** 正确处理不是抽函数，
而是让生成器承担去重（把公共 helper 放到共享模块、由生成器注入 import），
并把 468 个文件视为**数据资产**而非代码资产 —— 它占 src 的 18.6%，
却几乎不参与架构讨论。

---

## 1. 精确重复的真实规模

自建 AST 归一化指纹（标识符/字面量/属性名全部归一化后哈希）：

| 指标 | 数值 |
|---|---:|
| 函数/方法总数 | 4,655 |
| 精确重复组（≥2 份） | **107 组 / 373 个函数（8.0%）** |
| 骨架重复组（≥3 份，语句类型序列） | 25 组 / 158 个函数 |
| 理论可消除行数 | ≈ **2,319 行（src 的 1.7%）** |

组规模分布：`{2: 75, 3: 5, 4: 2, 5: 13, 10: 10, 17: 1, 18: 1}`

**去掉 alpha_zoo 后，真实可消除量不足 500 行（0.36%）。**

结论：**这个库的冗余不在"复制粘贴"上。** 精确重复检测（业界主流工具
PMD CPD / jscpd / pylint R0801 的做法）在这里给出的信号很弱 ——
因为重复主要发生在生成数据里，而真正的问题在**结构一致性**上。

---

## 2. 真正确证的结构问题（按证据强度排序）

### 2.1 【死代码】`loop.py` 里两份"无人使用的重复实现" ★★★★★

`sync_bridge.py` 是被引用的那份，`loop.py` 里的是**孤儿**：

| 符号 | 被谁用 | `loop.py` 里的那份 |
|---|---|---|
| `_run_coro_in_sync` | `loop_tools.py:22` 从 **sync_bridge** 导入 | `loop.py:81` 定义，**仅 `loop.py:755` 内部调用** |
| `_get_hook_loop` | `loop_telemetry.py:13` 从 **sync_bridge** 导入 | `loop.py:68` 定义，**无人使用** |

`loop.py` **不导入 sync_bridge**（已核实），两份实现各自独立演化：
`sync_bridge` 的 docstring 只有一行，`loop.py` 的有 5 行详细说明。

**风险**：两份 `_HOOK_LOOP` 全局变量 + 两把锁 = **两个独立的后台线程池**。
若将来有人同时调用两份，`_get_hook_loop` 会创建**两个** hook 事件循环线程，
而 `loop.py:755` 用的是它自己那份，`loop_telemetry.py:36` 用的是 sync_bridge 那份
—— **hook 回调会分散到两个不同的 event loop**，这是典型的竞态/顺序错乱来源。

**建议（Pattern：Extract Module + 单一权威）**：
- `loop.py` 的 `_run_coro_in_sync` 删除，改用 `sync_bridge` 的
- `loop.py` 的 `_get_hook_loop` + `_HOOK_LOOP` + `_HOOK_LOOP_LOCK` 整段删除
  （它无人使用）
- 保留 `loop.py` 对 sync_bridge 的 re-export（若外部有引用）

**成本**：3 处删除 + 1 处 import 调整。**风险**：极低（有测试覆盖 hook 路径）。

### 2.2 【抽象已建但没人用】`foundation/registry.py` 的通用 `Registry[T]` 零生产引用 ★★★★★

```
foundation/registry.py:23  class Registry(Generic[T])
    91 行 · 提供 register/get/remove/all_items/keys/items/filter/restricted/
           __len__/__contains__/__iter__/__repr__
生产引用者：**0 个**（仅 3 个测试文件引用，见下）
```

docstring 自己写着：
> "DSH-inspired: replaces 8+ ad-hoc `dict[str, T]` registries with a single
> generic base class"

**但它一个都没替换。** 实际存在 9 套各自实现的 registry：

| 实现 | 位置 |
|---|---|
| `BacktestEngineRegistry` | `apps/backtest_engine/factory.py:25` |
| `DataStoreRegistry` | `apps/data_store_registry.py:29` |
| `ValidatorRegistry` | `apps/goal/validator_registry.py:37` |
| `HypothesisRegistry` | `apps/hypothesis/registry.py:224` |
| `AgentPluginRegistry` | `kernel/agent/registry.py:15` |
| `ToolRegistry` | `kernel/agent/tools.py:835` |
| `SkillRegistry` | `kernel/skills/registry.py:15` |
| `AgentRegistry` | `kernel/workflow/agents.py:14` |
| `WorkflowRunRegistry` | `kernel/workflow/executor.py:361` |
| （+ `foundation/llm/provider/__init__.py` 的 `_REGISTRY` 模块级 dict） |

**能力匹配度实测**（逐方法对照 9 个实现）：

| `Registry[T]` 的能力 | 9 个实现里的对应 |
|---|---|
| `get` / `register` / `__contains__` / `__len__` | ✅ 9 个都有（可被基类覆盖） |
| `__iter__` | 1 个（`SkillRegistry`） |
| `restricted` | 1 个（`ToolRegistry`，**已独立重写**） |
| `all_items` / `filter` / `items` / `keys` / `remove` / `__repr__` | **0 个** |

**6 个独有能力零使用，唯一体现设计意图的 `restricted()` 已在 `ToolRegistry`
里独立重写**（`tools.py:930`，与基类语义一致但代码不同）。

历史意图也已失效：`docs/layered-skeleton-design-20261103.md:108` 记载
"`core/registry.py` …唯一引用方 `agent/builtin_tools/subagent_tool`"——
但 `subagent_tool.py` 现在用的是 `kernel/agent/tools.py` 的 `ToolRegistry`，
**从未引用过 `foundation.registry`**。

**判定：这是「能力与需求不匹配」的抽象 —— 该删，不该让 9 个去继承。**
理由（调研 §4.2 的「抽象比」指标）：`Registry[T]` 91 行，而 9 个实现里最小的
`SkillRegistry` 只有 15 行 —— **抽象层比一半的实现还厚**。Rule of One：
零生产引用，说明连一个潜在的第二实现都不存在。

**但这是一个需要拍板的设计决策，本轮未执行**，原因：
1. 它牵连 3 个专属测试文件（`test_registry_base.py` 114 行 +
   `test_registry_base_extended.py` 130 行 + `test_registry_base_keys.py` 104 行
   = **348 行**，全部只测 `Registry[T]` 自己，不测任何生产行为）
2. `tests/test_layer_matrix.py:99,105` 有两处 `foundation.registry` 的层归属登记
3. `docs/layered-skeleton-design-20261103.md:50,108` 把它写进了 L4 成员表
4. **替代方案 B**（让 9 个继承）需要逐个确认 key 规则差异能否用 `register()`
   覆写表达 —— `HypothesisRegistry` 的 `create/derive/link/contradicts` 与
   `ToolRegistry` 的 `execute/guard/check_guards` 语义完全不同，
   基类只能覆盖 dict 存取那部分，**收益仅是省掉几行 `self._items` 样板**

**待用户决策**：删除（连带 348 行测试 + 2 处门禁登记 + 2 处文档），
还是保留（接受"零生产引用的抽象"作为已知债），或走替代方案 B。

### 2.3 【Adapter 骨架重复】8 个 data_source loader 的 `fetch()` 雷同 ★★★★☆

8 个 loader，**8/8 都有 `fetch` 和 `is_available`**，`_fetch_one` 7/8 有。
`fetch()` 的骨架：

```python
def fetch(self, codes, start_date, end_date, *, interval="1D", fields=None):
    validate_date_range(start_date, end_date)      # ← 8/8 完全相同
    result = {}
    for code in codes:
        try:
            df = self._fetch_one(code, start_date, end_date)   # ← 唯一差异点
            if df is not None and not df.empty:
                result[code] = df
        except Exception as exc:
            logger.warning("<NAME> 获取失败 %s: %s", code, exc)   # ← 仅 logger 名不同
    return result
```

**关键取证**：`apps/data_source/base.py` 已经有 `normalize_ohlcv()`（统一后处理），
但**只有 `local_loader.py` 在用**，其余 7 个各自内联了等价的后处理逻辑
（约 8 行/loader）。

**建议（Pattern：Template Method + Protocol + Facade）**：
- `base.py` 提供 `BaseLoader`（或 `fetch_all()` 模板函数），把
  「遍历 → try → 判空 → 收集」这段固定住
- 各 loader 只实现 `_fetch_one()` + 自己的列名映射
- 后处理统一走 `normalize_ohlcv()`

**成本**：中（8 个文件，但每处改动小）。**风险：中**
—— 各 provider 的列名/字段差异需逐个验证（`akshare` 返回中文列名、
`fred` 用 `series_id` 而非 `code`、`eastmoney` 需要 `klt` 映射）。
**建议先写契约测试**（对 8 个 loader 跑同一套 Protocol 契约）再动手。

### 2.4 【横切未统一】578 处 `except Exception` / 115 处 `os.environ` ★★★★☆

| 横切项 | 处数 | 文件数 | 集中度 |
|---|---:|---:|---|
| `except Exception` | **578** | 169 | Top: `cli/tui/app.py` 36 · `apps/db.py` 28 · `factor_tools.py` 16 |
| `os.environ` 直读 | **115** | 60 | Top: `kernel/agent/cache.py` 12 · `api/app.py` 9 · `llm/config.py` 7 |
| `logging.getLogger` | 191 | 177 | 分散（正常） |
| `@dataclass` | 164 | 93 | 其中 frozen 仅 66 |
| `Protocol` / `runtime_checkable` | 91 / 34 | 27 / 11 | 仅 11 文件用 runtime_checkable |
| `singledispatch` | **0** | 0 | 完全未用 |

**环境变量重复读取**（同一变量被多处读）：

```
SR_WORKSPACE_PATH   11 处     ← 最值得收敛
SR_ENFORCE_STUDY_IDOR  4 处
SR_GOAL_DB_PATH  3 · SR_HYPOTHESES_PATH 3 · STATIC_DIR 3 · CORS_ORIGINS 3
```

`foundation/config_loader.py` 已存在但未被统一使用。

**建议（Pattern：Facade + frozen dataclass Config）**：
- 建 `foundation/settings.py`：`@dataclass(frozen=True) class Settings`，
  启动时读一次环境，之后只读
- `SR_WORKSPACE_PATH` 11 处 → 1 处读取 + N 处引用

**成本**：低（机械替换）。**风险**：低 —— 但要保留「测试中改环境变量」的
现有能力（`monkeypatch.setenv` 目前是有效测试手法），改成 frozen 一次性读取后
会破坏它。**这是本条的主要代价，必须先确认测试策略**。

### 2.5 【超大模块】73 个模块 ≥400 行，占 src 的 39.4% ★★★☆☆

```
1773  apps/goal/store.py                    1647  api/routers/study.py
1589  apps/study/store.py                   1450  api/session/service.py
1427  apps/autoresearch.py                  1388  apps/compute_factor.py
1319  kernel/agent/loop.py                  1273  apps/study/runner.py
1273  apps/db.py                            1150  builtin_tools/factor_tools.py
1142  apps/study/scheduler.py               1112  cli/__init__.py
1020  api/routers/chat.py                   1011  apps/alpha_zoo_convert.py
```

其中 `compute_factor.py` 93 个顶层函数、`cli/__init__.py` 1,112 行且 60 条再导出 import
（这个已在本轮前面的评审里记录过）。

**建议（Pattern：Facade + Extract Module）**：拆大模块时用 `__init__.py`
维持原导出路径，调用方零改动。**注意**：这条与 W10 已修的 `cli/__init__.py`
导入排序问题相关，但那是 lint 层面；这里是体积层面。

### 2.6 【测试样板】105 个同名测试函数覆盖 460 处 ★★★☆☆

```
27×  test_empty        20×  test_defaults      18×  test_basic
14×  test_empty_string  13×  test_frozen         9×  test_empty_messages
 7×  test_no_db          7×  test_to_dict        7×  test_diamond
```

**但要谨慎**：调研 §3.2 明确指出「测试里的重复往往是**故意**的」——
独立可读性 > DRY。**只有当它们能被 `@pytest.mark.parametrize` 或契约测试
替代时才该动。** `test_empty` ×27 这种命名跨 27 个不同类，
大概率是各 dataclass/store 的"空值行为"测试 —— 这些**适合参数化**
（同一个断言跑 27 个类），但需要先确认它们的断言体是否真的同构。

### 2.7 【graphify 图变化】W10 三项带来的净增 ★☆☆☆☆

```
graphify4: 45,982 节点 / 89,897 边
graphify5: 46,029 节点 / 89,986 边   （+47 / +89）
```

新增节点正是 W10 的产出（`factory_create_engine`、
`StrategyEngineAdapter.{engine,vol_targeting,trend_filter,stop_loss,risk_free_rate}`
等 5 个透出属性）。**图的净增全部可解释，无异常膨胀。**

---

## 3. 设计模式匹配表

调研产出的 25 行对照表（GoF 23 + Python 生态 + 2 个非 GoF），
按**本仓库实测症状**筛选后的适用子集：

| 模式 | 本仓库的确切症状 | 落点 | 成本 | 风险 |
|---|---|---|---|---|
| **Extract Module** | `loop.py` 双份 sync/hook 实现 | §2.1 | 极低 | 极低 |
| **（删除过度抽象）** | `Registry[T]` 91 行零引用 | §2.2 | 极低 | 极低 |
| **Template Method** | 8 loader 的 `fetch()` 骨架 | §2.3 | 中 | 中 |
| **Protocol（PEP 544）** | loader 隐式契约；`base.py` 已有 `DataLoader` | §2.3 | 低 | 低 |
| **Facade + frozen dataclass** | 115 处 `os.environ` 直读 | §2.4 | 低 | 中（测试策略） |
| **Decorator** | 578 处 `except Exception` 的边界转换 | §2.4 | 中 | 中 |
| **Extract Module + Facade** | 73 个超大模块 | §2.5 | 中 | 中 |
| **@pytest.mark.parametrize** | 105 组同名测试 | §2.6 | 低 | 低 |
| **Strategy / singledispatch** | if-else 类型分派（loader 市场判定、事件 kind） | 待查 | 中 | 中 |
| **Registry** | 9 套 registry —— **但建议反向：删而非加** | §2.2 | — | — |
| **Mixin** | `kernel/agent/loop_*` 6 个 Mixin | ⚠ **建议逐步移除** | 高 | 高 |
| **Observer / 事件总线** | `apps/goal/event_bus.py` 未统一 | 待查 | 中高 | 中高 |
| **Dependency Injection** | 模块级单例、全局变量 | ⚠ 慎用 | 高 | 高 |
| **Singleton** | `study/scheduler.py` 的 gateway 单例 | ⚠ 需审查 | 低 | 高 |

### 3.1 调研中的一条反直觉结论：Mixin 是本仓库的隐性成本

`kernel/agent/loop.py` 由 **6 个 Mixin** 组合而成
（`loop_compaction` / `loop_lifecycle` / `loop_progress` / `loop_stream` /
`loop_telemetry` / `loop_tools`）。调研 §2.1 列出的 mixin 代价，
本仓库**全部命中**：

| mixin 代价 | 本仓库实例 |
|---|---|
| 隐式耦合（依赖的方法名不在签名里） | 每个 Mixin 声明 `_event_bus: Any` / `client: Any` 等"宿主接口"，改名即静默失效 |
| 同名方法遮蔽 | 6 个 Mixin 都有 `_maybe_compact` / `async _amaybe_compact` 形态相近的方法 |
| `super()` 链依赖 | `loop.py` 里 Mixin 的 `super().__init__()` 调用顺序敏感 |
| MRO 冲突运行时才暴露 | W10-1 断环时发现 `loop_compaction` 反向 import `loop`，正是 mixin 拆分的后遗症 |
| 难静态分析 | 6 个 `loop_*.py` 共 ~1,300 行都要靠读 Mixin 才能拼出 AgentLoop 全貌 |

**建议**：**不新增 mixin**；已有 6 个的拆除是独立大工程（需先给 `AgentLoop`
写出可测的行为契约），不列在本轮。

---

## 4. 执行建议（按 ROI 排序）

| # | 事项 | 规模 | 风险 | 收益 | 模式 | 状态 |
|---|---|---|---|---|---|---|
| **1** | 删 `loop.py` 的 `_run_coro_in_sync` + `_get_hook_loop` 双份 | 3 处 | 极低 | 消除**双 event loop 线程**隐患 | Extract Module | ✅ **已做** |
| **2** | `foundation/registry.py` 定去留（建议删） | 91 行 + 348 行测试 + 4 处登记 | 极低 | 去掉零引用抽象 | —（反向） | ⏸ **待拍板**（见 §2.2） |
| **3** | `SR_WORKSPACE_PATH` 等 11 处环境变量收敛到 `Settings` | 中 | 中 | 配置可审计、启动只读一次 | Facade + frozen dataclass | 待办 |
| **4** | 8 loader 的 `fetch()` 模板化 + 后处理统一走 `normalize_ohlcv` | 中 | 中 | 消除 ~70 行重复；统一契约 | Template Method + Protocol | 待办 |
| **5** | `compute_factor.py`（93 函数）拆分 + Facade 保兼容 | 中 | 中 | 超大模块治理 | Extract Module + Facade | 待办 |
| 6 | 27 组 `test_empty` 类测试参数化 | 中 | 低 | 缩短 CI | parametrize | 待办 |
| 7 | `except Exception` 分级收窄（先补测试再收） | 大 | 中 | 错误可诊断 | Error Hierarchy + Decorator | 待办 |
| 8 | 6 个 Mixin 逐步拆除 | 很大 | 高 | 可读性 | 组合替代继承 | 待办 |
| 9 | alpha_zoo 468 文件按**数据资产**管理（生成器去重） | 大 | 低 | 减 src 体积 | 代码生成 | 待办 |

**本轮已做：建议 1**（`loop.py` 双份实现收敛到 `sync_bridge.py`）。
**建议 2 需用户拍板**：它要连带删 348 行专属测试与 4 处登记，属设计决策。

**建议 3-5 各自需要一次单独设计与验证**，不在本轮。

---

## 5. 明确不要做的

| 事项 | 理由 |
|---|---|
| 合并 `alpha_zoo_ops` 与 `compute_factor` | §0.1 已证：16 个同名函数语义全不同 |
| 合并 `study/store.py` 的 4 个 interrupt 查询 | §0.2 已证：pending vs 任意状态是语义核心 |
| 强行让 9 个 registry 继承 `Registry[T]` | 抽象比 91:15，Rule of One（零使用） |
| 给 `apps` 层新增 mixin | MRO 冲突 / 隐式耦合 / 难静态分析 |
| 引入 DI 容器 | 全局状态回归风险；本仓库已有 9 个 registry，再加会放大 service-locator 化 |
| 把 `alpha_zoo` 468 文件当"代码重复"抽函数 | 它们是 YAML 转换产物，重复是设计使然 |
| 为"消除 578 处 except"而批量收窄异常类型 | 会掩盖真实错误；必须先补测试 |

---

## 附录 A：本次用到的分析工具

| 脚本（`tmp/`） | 用途 | 关键输出 |
|---|---|---|
| `reuse_audit.py` | AST 归一化指纹 + 骨架 + 横切统计 | 107 组精确重复 / 25 组骨架 / 15 项横切计数 |
| `reuse_targeted.py` | 5 个候选的定点取证 | 双份算子 16/16 不同；loader 骨架 |
| `reuse_deep.py` | registry / normalize_ohlcv / alpha_zoo / 超大模块 | `Registry[T]` 零引用；73 个 ≥400 行 |
| `reuse_final.py` | sync_bridge 孤儿核实 + 图差分 + 测试重复度 | `_get_hook_loop` 无人使用；图 +47/+89 |

**建议固化**：`reuse_audit.py` 进 `scripts/arch/check_code_duplication.py`
（只读、输出统计、退出码按阈值），把"精确重复组数"变成每次 PR 可见的数字。
但**必须带 ignore 列表**（alpha_zoo 等生成目录），否则 107 组里 90% 是噪声。

## 附录 B：调研来源

设计模式调研由独立子代理完成，覆盖：GoF 23 模式全集（含 GoF 边界说明——
`Repository`/`Registry`/`Object Pool` **不是** GoF 模式）、Python 生态 11 类
（mixin/Protocol/PEP 562/描述符/DI/插件/singledispatch 等）、
4 类重复检测法（文本/近似/AST/语义，含 PMD CPD 与 jscpd 的具体做法与局限）、
21 项重构手法目录、Rule of Three 判定表、Python 特有的 5 类"不该用继承"信号、
以及 10 项可落地的重构指标（导航跳数、变更放大系数、抽象比、样板比等）。

调研结论中**被本报告的独立取证推翻或修正的部分**，已在 §0 逐条记录。
