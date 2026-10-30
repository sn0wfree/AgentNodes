# D1 设计：方法论归位 `core/study/method/`（2026-10-30）

> 前置已满足：`phase_engine ↔ runner` 循环导入已解（`c7838e78`）。
> 三道门沿用。**纯搬家，零行为变化。**

---

## 1. 勘查修正：搬家集比计划小

原计划搬 `phase_engine` + 5 个域件。AST 勘查发现 **`phase_engine` 搬不了**：

| 事实 | 证据 |
|---|---|
| `phase_engine` 仍 import 执行侧：`.graph_preset`、`.scenario_router` | AST import 扫描 |
| 且被全部执行侧引用（contracts/graph_preset/runner/scenario_router） | 文本级反查 |
| 搬它 = `method↔engine` **包级双向引用** | 上两条组合 |

**修正后的定位**：`phase_engine` 是「引擎的方法适配器」（它把方法论阶段
接到执行机制上），暂留原位；它的归位以前置「与 graph_preset/scenario_router
解耦」为条件——那是真实重构，不是搬家，另立刀号。

**本轮搬 6 个纯方法件**（AST 验证：无任何域内执行依赖）：

| 文件 | 内部依赖 | 搬后 |
|---|---|---|
| `aegis.py` | 无（typing only） | ✅ |
| `attribution.py` | 无（enum only） | ✅ |
| `claims.py` | 无 | ✅ |
| `guidance.py` | 无 | ✅ |
| `metric_targets.py` | `core/strategy_acceptance`（study 域外） | ✅ |
| `review_loop.py` | 无 | ✅ |

互相引用（aegis→attribution、claims→metric_targets）随同目录搬迁自动保持。

## 2. 搬法

1. `git mv` 6 文件 → `core/study/method/` + 新建 `__init__.py`；
2. **不留 shim**——执行侧 ~20 处 import 全量改指 `.method.*`（与
   hanging_events 迁移同纪律：旧路径立即死，门禁会抓住遗漏）；
3. 白名单门禁适配：`STUDY_PLATFORM_TOUCHPOINTS` 扫描 `study/` 顶层；
   `method/` 子包加**新门禁**——`method/` 不得 import 执行侧
   （runner/scheduler/graph_preset/scenario_router/store/phase_engine），
   **这正是归位的意义**：方法论不许依赖执行机制；
4. `metric_targets` 的 `strategy_acceptance` 触点：加入白名单允许子层。

## 3. 验收

| 门 | 判定 |
|---|---|
| 行为等价 | 纯搬 + import 改指，零运行时变化 |
| 测试不减 | inventory 0 removed |
| 失败集不扩大 | c1_gate 对基线 |
| 新门禁 | method/ 无执行侧 import；引擎→方法单向 |
