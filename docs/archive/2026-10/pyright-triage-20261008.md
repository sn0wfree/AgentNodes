# Pyright 958 Errors 修复可行性梳理

> 来源：`docs/archive/undated/pyright-gradual-design.md` 首跑基线（958 errors / 457 files）· 本次梳理决定「修哪些、何时修」
> 原则：**先真 bug 类、后机械修、mixin 类渐进**——不追求一次清零，追求「可修的快修、难修的渐进、误报的豁免」

## 1. 分类总表

| 类别 | 数量 | 修复难度 | 风险 | 建议 |
|---|---|---|---|---|
| **A. 真 bug 类**（必修） | **~16** | 低-中 | 低（改的是错） | **立刻修** |
| **B. 机械注解对齐** | 107 | 低 | 低 | 快修（半天） |
| **C. Optional/None 判** | 258 | 中 | 中（行为面） | 渐进（按文件） |
| **D. 参数类型对齐** | 217 | 中 | 中（调用方契约） | 渐进 |
| **E. mixin 隐式耦合** | 306 | **高** | 高（重构类） | Phase 4 触达时修 |
| **F. 豁免/误报** | 18 | 极低 | 零 | **配置关** |

## 2. A. 真 bug 类（必修清单）

| 位置 | 问题 | 修法 | 估工 |
|---|---|---|---|
| `backtest_analysis_tools.py:186` / `factor_tools.py:976` | `list[str]` 赋值给 `str` 类型变量 | 类型注解对齐实际（`list[str]`） | 5min |
| `hanging_events.py:104/274` | `with None`（None 未判就作 context manager） | 加 `if x is not None:` | 10min |
| `__init__.py:64/70` 等 4 处 | `__all__` 引用 `CompletionConfig`/`GoalAgentConfig` 不存在 | 删 `__all__` 条目 或 补 re-export（P1 修复后遗症？） | 10min |
| `validation.py:50-51`（+ 同类 `reportCallIssue`） | 调用少传 `config`/`equity_curve`、多传 `run_dir` | 核对签名调用方 | 20min |
| `reportCallIssue` ×57 内的真 bug 项 | 类似 validation.py | 逐条核 | 1h |
| `reportGeneralTypeIssues` TypedDict 键（`system.py:81-83`） | `LLMConfigStatus` TypedDict 缺 `model_context_tokens` 等键 | 补 TypedDict 键 或 改用 `dict` | 10min |

**估工**：半天（含验证）——真 bug 类优先，这些是运行时风险/契约错误。

## 3. B. 机械注解对齐（107，快修）

- **`reportReturnType` ×92**：返回类型不符注解（如 `service.py:196` `dict[str, str|int]`  vs  `dict[str, str]`）——修法：**放宽注解**（`dict[str, str|int]`）或改返回值——机械
- **`reportOperatorIssue` ×7 + `reportOptionalSubscript`/`Iterable`/`ContextManager` ×10**：`int | None` 参与运算/下标——加 `if x is not None` 或 `or 0`——机械
- **估工**：半天（按文件扫）

## 4. C/D. 渐进修（475，机械但面广）

- **`reportOptionalMemberAccess` ×243**：`None.get()`/`None.attr`——加 None 判——**风险**：改的是行为路径（None 时可能 fallback）；逐文件修，每次小 PR
- **`reportArgumentType` ×217**：参数类型不匹配——**调用方 vs 签名契约**，需读代码对齐（不是纯注解改）
- **估工**：3-5 天渐进（每次 PR 触达时顺手修）

## 5. E. mixin 隐式耦合（306，重构类）

`reportAttributeAccessIssue`（`self.*` 未在类/签名声明）集中于：
- `loop_tools.py` 40 · `loop_progress.py` 36 · `loop_stream.py` 26 · `runner_knowledge.py` 23 · `factor_validate.py` 19 · `runner_aegis.py` 19

**这是 12 F821 NameError 的根源**（W5 mixin 拆分遗留）——修法是 mixin → composition（Phase 4 长期规则）或每 mixin 类补 `__slots__`/显式字段声明。**触发驱动**：触到该 mixin 时顺手修。

**估工**：随 Phase 4，单 mixin ~半天。

## 6. F. 豁免/误报（18，配置关）

| 类别 | 数量 | 处理 |
|---|---|---|
| `reportMissingModuleSource`（`import yaml` 等无 stub） | 14 | `pyrightconfig.json` 关 `reportMissingModuleSource`，或 `pip install types-PyYAML` |
| `reportUnsupportedDunderAll` 其余 | ~4 | 同 A |

**估工**：5 分钟（配置）。

## 7. 建议执行顺序

| 批次 | 内容 | 估工 | 效果 |
|---|---|---|---|
| **批 1（P0）** | A 真 bug 类 + F 配置关 | **半天** | 958 → ~938；真 bug 清零 |
| 批 2（P1） | B 机械注解对齐 | 半天 | 938 → ~831 |
| 批 3（渐进） | C/D Optional/参数 | 3-5 天（PR 触达时） | → ~400 |
| 批 4（Phase 4） | E mixin 解耦 | 触动即修 | 与 mixin→composition 并行 |

**本次梳理产出**：分类清楚、真 bug 清单（A/F 可立刻做）、渐进路线（B/C/D/E 各自机制）。
