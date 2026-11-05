# 结构修复三项设计（2026-11-04 · W10）

> 依据：`docs/design/structure-review-graphify-20261104.md`（§2.5 修正版口径）
> 用户拍板范围：**项 1 断真环 · 项 2 契约下沉 · 项 3 backtest_engine 接线**
> 约束：git-first doc-first；三项各自独立 commit；每步前后门禁与测试必须绿。

---

## 0. 前置：本次设计建立在已修正的口径上

评审初版曾有 3 处错误结论，实现前复核已推翻（详见评审 §2.5）。本设计**只依据修正后的数据**：

| 事实 | 数值 | 取证方式 |
|---|---|---|
| eager 真环（运行时会 ImportError） | **1 个**：`kernel.agent.loop ↔ loop_compaction` | 运行时实测 `import ...loop_compaction` 先执行 → `ImportError: cannot import name 'LoopCompactionMixin' from partially initialized module` |
| `from . import X` → `from .X import ...` | **对断环无效** | CPython `_handle_fromlist` 两种写法都先执行父包 `__init__`；实测 `import apps.study.budget` 会完整加载 `apps.study` |
| `AgentResult` 的下沉价值 | `workflow → swarm` 37 条符号边中 **28 条是 `AgentResult`** | AST 逐 import 统计 |
| `backtest_engine` 性质 | P0-3 半成品：Protocol + factory 齐备，**生产路径绕过它** | `config_runner.py:91` 直接 `return StrategyEngine(...)` |

---

## 1. 项 1 —— 断 `kernel.agent.loop ↔ loop_compaction` 真环

### 1.1 现状（已实测会崩）

```
loop.py:296          from .loop_compaction import LoopCompactionMixin   ← eager
loop_compaction.py:14  from . import loop as _loop_mod                  ← eager
loop_compaction.py:231  persister = _loop_mod._compaction_persister      ← 唯一真实用途
```

`loop_compaction.py` 需要 `loop.py` 里的**一个模块级函数** `_compaction_persister`，
而 `loop.py` 需要 `loop_compaction.py` 里的**一个 Mixin 类**。
典型双向依赖。

运行时后果（已实测）：
```python
import strategy_research.kernel.agent.loop_compaction
# ImportError: cannot import name 'LoopCompactionMixin'
#   from partially initialized module 'strategy_research.kernel.agent.loop'
```
「import `loop` 先」则成功——**能否导入取决于导入顺序**，这正是最危险的一类缺陷：
测试全绿但生产启动路径一换就崩。

### 1.2 方案：把 `_compaction_persister` 下沉到 `compact.py`

`compact.py` 已被双方依赖（`loop.py:52` 与 `loop_compaction.py:13`），是天然的中立落点。
`_compaction_persister` 是压缩持久化逻辑，语义上也属于 `compact` 而非 `loop`。

步骤：
1. 读 `loop.py` 中 `_compaction_persister` 的完整定义与其依赖（确认不反向依赖 loop 内部状态）
2. 平移该函数到 `compact.py`，`loop.py` 改为 `from .compact import _compaction_persister` 再 re-export（保持向后兼容）
3. `loop_compaction.py:14` 的 `from . import loop as _loop_mod` 删除，`:231` 改 `_compact_mod._compaction_persister`

**为什么这是对的一刀**：断环后
- `loop.py` → `loop_compaction.py`（单向，保留）
- `loop_compaction.py` → `compact.py`（单向，已有）
- 环消失，且**导入顺序不再影响正确性**

### 1.3 验证口径

```bash
# 1) 环消失：任意顺序都能导入
python -c "import strategy_research.kernel.agent.loop_compaction"
python -c "import strategy_research.kernel.agent.loop"
python -c "from strategy_research.kernel.agent.loop_compaction import LoopCompactionMixin"
# 2) eager SCC 归零（用修正版分析器）
python tmp/p1_scc_v3.py    # 期望 "eager-only 环 0 个"
# 3) 行为不变
pytest tests -k "compact or loop" -q
```

### 1.4 风险

低。`_compaction_persister` 是纯模块级函数；`compact.py` 已是双方共同依赖，
不会引入新的跨层或跨包边。若该函数引用了 `loop.py` 的其它模块级状态，则改为
「先抽到一个新的 `_loop_shared.py`」——但取证阶段会先确认，避免过度设计。

---

## 2. 项 2 —— `AgentResult` / `SwarmPreset` 下沉

### 2.1 现状

三个 dataclass 定义在 `kernel/swarm/runtime.py`（945 行的引擎实现文件）：

| 类型 | 定义行 | 使用面 |
|---|---:|---|
| `SwarmPreset` | `runtime.py:33` | `preset_loader.py:8`(eager) · `apps/goal/workflow.py:96`(lazy) · `workflow/definition.py:410`(lazy) |
| `AgentResult` | `runtime.py:70` | `workflow/{executor:28, graph_compiler:37, node_types:21}`(eager) · `workflow/store:304`(lazy) · `apps/goal/workflow:613`(lazy) |
| `SwarmResult` | `runtime.py:87` | 无外部 import（仅 runtime 内部） |

`AgentResult` 是 workflow 的核心数据载体却被定义在 swarm 引擎里
——**定义面 1 处 vs 使用面 6 处**，典型的契约错位。

### 2.2 方案：下沉到 `kernel/swarm/types.py`

`kernel/swarm/types.py` 现有 79 行，已是 `AgentCall / AgentStatus / SwarmHook` 的契约模块，
且 `runtime.py:24` 已从它导入——**它就是为这件事存在的**。

步骤：
1. `SwarmPreset` / `AgentResult` / `SwarmResult` 三个 dataclass 平移至 `swarm/types.py`
   （`SwarmResult` 一并搬，保持同族完整；虽无外部依赖，但留在 runtime 会造成「一半契约一半实现」）
2. `runtime.py` 改为 `from .types import AgentResult, SwarmPreset, SwarmResult` 并 re-export
   （**保持 `from strategy_research.kernel.swarm.runtime import AgentResult` 全部旧路径可用**）
3. 契约模块需要 `dataclass/field/Any`——`types.py` 会引入 `from dataclasses import dataclass, field`

**为什么不是 `kernel/workflow/types.py`**：`workflow/types.py` 只有 12 行且是
`swarm.types` 的 back-compat re-export 层；把契约放在 `swarm/types.py` 更符合
现有结构（`AgentStatus` 已在那里），也不会让 workflow 反向成为 swarm 的契约源。

### 2.3 预期收益

- `workflow → swarm` 的 `AgentResult` 28 条符号边 → 指向 `swarm/types.py`（**仍是 swarm 包内**，
  所以**跨包边数不变**，但语义变清晰：workflow 依赖的是 swarm 的**契约**，不是 swarm 的**实现**）
- ⚠ **诚实说明**：这一项**不会减少层间/包间边数**，它改善的是「依赖指向契约还是指向实现」。
  评审 §6 表格里写的「消掉 28 条边」表述不准确，实际是「28 条边的终点从 `runtime.py` 移到 `types.py`」。
  真正的边数下降要靠项 5（api/session 下沉）和项 6（CapabilityRegistry）。

### 2.4 验证口径

```bash
python -c "from strategy_research.kernel.swarm.runtime import AgentResult, SwarmPreset, SwarmResult"
python -c "from strategy_research.kernel.swarm.types import AgentResult, SwarmPreset, SwarmResult"
pytest tests -k "swarm or workflow or goal" -q
python tmp/p1_scc_v3.py    # 期望 SCC 数量不增加
```

### 2.5 风险

低。纯 dataclass 平移 + re-export。唯一注意点：`swarm/types.py` 若被 `runtime.py`
在模块顶层导入，而 `types.py` 又（当前）不导入任何本包实现——需确认平移后不产生新的环。

---

## 3. 项 3 —— `apps/backtest_engine` 接线

### 3.1 现状

P0-3 的产物，设计目标是「把 YAML 路径 (`StrategyEngine.run`) 与 Callback 路径
(`run_backtest`) 收敛到单一 `BacktestEngine` Protocol」：

```
protocol.py              BacktestEngine / Strategy 两个 Protocol
factory.py               get_engine() 注册表，默认注册 "strategy" 与 "callback"
strategy_engine_adapter.py  包装 utils.strategy_engine.StrategyEngine
callback_engine_adapter.py  包装 utils.backtest_engine.run_backtest
```

**但生产路径没走它**：`apps/config_runner.py:91` 直接
```python
return StrategyEngine(
    vol_targeting=vt, trend_filter=tf, stop_loss=sl,
    risk_free_rate=float(bt_cfg.get("risk_free_rate", 0.0) or 0.0),
)
```

### 3.2 方案：`config_runner` 改走 `get_engine()`

用户决定：**保留并暂时接线，后续再深入**。最小接线（不改变计算行为）：

```python
# config_runner.py
from strategy_research.apps.backtest_engine import get_engine

def create_strategy_engine(cfg: dict) -> Any:
    ...
    adapter = get_engine("strategy")
    # adapter 内部持有 StrategyEngine；构造参数（vol_targeting 等）
    # 目前只能通过 config 传入，见 3.3 的已知缺口
    return adapter
```

**必须诚实指出的缺口**：`StrategyEngineAdapter.__init__` 无参数，
而当前 `config_runner` 把 `vol_targeting / trend_filter / stop_loss / risk_free_rate`
作为**构造参数**传入。`StrategyEngineAdapter` 只在 `run(config=...)` 里转发
`rebal_freq / min_history / cost` 三项。因此「直接换成 `get_engine()`」会**丢掉风险配置**，
这是行为回退，不可接受。

因此项 3 的正确做法是**两步**：

**3a（本次做）** 给 `StrategyEngineAdapter` 增加 `__init__` 参数支持，
使其能承接 `vol_targeting / trend_filter / stop_loss / risk_free_rate`；
`config_runner` 改走 `get_engine("strategy", **risk_kwargs)` 或在 config 中传递。
配套断言：`create_strategy_engine(cfg)` 的返回值在典型 cfg 下与旧实现的
`StrategyEngine(...)` **参数等价**（比较属性，不比较对象身份）。

**3b（记录待办，本次不做）** `factory.get_engine()` 目前签名是
`get_engine(name: str | None = None) -> BacktestEngine`，不接受构造 kwargs；
要让「配置 → 引擎」这条链彻底走 factory，需要给 registry 引入 factory-callable
或 kwargs 透传。这属于 registry 设计变更，记入待办。

### 3.3 验证口径

```bash
# 1) 旧行为等价的断言（新增测试）
pytest tests/test_backtest_engine.py tests/test_backtest_engine_protocol.py -q
# 2) config_runner 仍能构造引擎且风险参数不丢
pytest tests -k "config_runner or strategy_engine" -q
# 3) 接线生效：config_runner 不再直接 import StrategyEngine
grep -n "StrategyEngine" src/strategy_research/apps/config_runner.py   # 期望只剩 adapter 内
```

### 3.4 风险

**中低**。这是三项里唯一触及生产计算路径的：风险参数丢失会导致回测结果变化而
测试不一定发现（数值差异）。因此：
- 必须先写「参数等价」断言**再**改接线
- 改完必须跑回测相关测试全集，并对比一个真实 YAML 配置的引擎属性

---

## 4. 执行顺序与提交划分

| 序 | 内容 | commit |
|---|---|---|
| 0 | 本设计文档 + 评审 §2.5 修正 | `docs(W10)` |
| 1 | 断 `loop ↔ loop_compaction` 环 | `fix(W10-1)` |
| 2 | `AgentResult/SwarmPreset/SwarmResult` 下沉 `swarm/types.py` | `refactor(W10-2)` |
| 3 | `StrategyEngineAdapter` 承接风险参数 + `config_runner` 走 `get_engine()` | `feat(W10-3)` |

每步验证：`compileall` → `check_dangling_imports` → `check_path_literals` →
`test_layer_matrix` + `test_architecture_gates` → 相关模块测试 → 全量（最后一步）。

## 5. 记录待办（本次不做，用户确认后归档）

1. **`factory.get_engine()` kwargs 透传**（3b）——让「配置 → 引擎」彻底走 factory。
2. **13 处 `from . import X` 机械改写**——对断环无效（§2.5），仅提升可读性，
   其中 `apps/strategy_acceptance/cli.py:21` 的 `decide`/`load_config` 是 `__init__` 名字，不可机械改。
3. **`interfaces/api/session/` 5.9k 行下沉 `kernel/session/`**——L1 判据重划，需先解 35 处互引。
4. **CapabilityRegistry 端口化**（ADR-003）——46 条 L3→L2 清零的唯一正解。
5. **判环门禁固化**——把「eager/TYPE_CHECKING/lazy 三分 + `from . import X` 归属判定」
   做成 `scripts/arch/check_import_cycles.py`，终审以运行时导入矩阵为准。
