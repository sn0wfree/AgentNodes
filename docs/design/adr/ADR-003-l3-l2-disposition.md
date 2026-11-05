# ADR-003：L3→L2 处置路径 — 不建 CapabilityRegistry

日期：2026-11-03　状态：**已接受**　Owner：平台架构 Owner
输入：`docs/archive/2026-11/w1-3-audit-l3-l2-20261103.md`（46 条逐条分类）·
`docs/archive/2026-11/w1-1-audit-agent-classification-20261103.md`（agent 22k 分类）·
`docs/archive/2026-11/w1-2-audit-l1-l2-20261103.md`（L1→L2 三类拆分）
关联：`docs/design/layered-skeleton-design-20261103.md` · `tests/test_layer_matrix.py`

## 背景

实施计划 3.3 要求在分类结论出来前不得动手端口化。W1-3 已完成 46 条 L3→L2
上行边的逐条分类，结论与立项时的假设相反。

## 决定

### D1. 不建 CapabilityRegistry（G2b 判「不建」）

**依据（W1-3）**：

| 类别 | 条数 | 说明 |
|---|---:|---|
| C：函数体内 lazy import | **44** | Python 规避循环导入的标准写法，非架构耦合 |
| B：kernel 借用研究域工具（应端口化） | **5** | 集中在 `factor_tools`(3) 与 `data_clean_tools`(2)，都是数据形状转换 |
| A：kernel 真需研究能力（分层错） | **6** | 集中在 `scheduled_research`(3) 与 `swarm/runtime`(3) |
| 模块级静态依赖 | **2** | `backtest_tools:10`、`factor_tools:8` |

B 类仅 5 条、两个工具函数——**不足以支撑一个端口层**。为一个 5 条边建的抽象，
其维护成本（协议演进、双端同步、注册时序）远高于收益。

**重开条件（二者任一）**：
1. B 类边 > 15 条；或
2. 出现第 3 个独立消费方（当前只有 builtin_tools 一族）。

### D2. A 类 6 条单独立项，用事件/指令解耦而非 Registry

| 位置 | 问题 | 处置 |
|---|---|---|
| `scheduled_research/cli.py:228,235` | 调度 CLI 直接 import `core.study` | 走 Gateway 提交（已有能力） |
| `scheduled_research/executor.py:269` | 调度器直接调 `study.bootstrap` | 同上 |
| `swarm/runtime.py:843` | 编译器内联 `goal.expression_evaluator` | 评估器下沉或经端口 |
| `swarm/runtime.py:904,924` | 编译器内联 `backtest` / `strategy_acceptance` | 经事件 + Directive |
| `agent/exec_registry.py:69,90` | 注册表内置研究能力 | 改为注册回调（`ToolRegistry` 既有模式） |

→ 新工作项 **W3-7「A 类 6 条去研究域化」**，进 B3 执行批。

### D3. 模块级 2 条改注册回调

`backtest_tools.py:10`、`factor_tools.py:8` —— 模块级 import 研究域，改为
工具注册时注入（与 D2 的 `exec_registry` 同一模式）。并入 W3-7。

### D4. C 类 44 条 lazy import 判定为合规

不修改代码，但**门禁口径需调整**（W4-1）：

- `test_layer_matrix` 对「函数体内 import」单独标注 `lazy`，与模块级静态边分开计数
- 棘轮基线只约束**模块级静态边**；lazy 边单列观察（防从 lazy 演化为模块级）

理由：lazy import 是刻意的循环导入规避，把它计入「架构违规」会让基线永远
带着 44 条噪声，真实违规（2 条模块级）被淹没。

## 影响

- **图 v8 ⑦ 区**：L3→L2 的 46 条需重新表述为「2 模块级 + 44 lazy + 5 数据工具」，
  而非单一「92/46 待端口化」。待图 v8.4 更新。
- **接缝门禁**：不变（白名单机制继续管研究域→平台的 11 触点）。
- **门禁基线**：暂不动，等 W4-1 实现 lazy 分类后一次性重设。

## 验证

- 本 ADR 不产生代码改动，无验收命令。
- 后续 W3-7 的验收：L3→L2 模块级静态边从 2 → 0。

## 备案：为什么这条 ADR 值得单独写

「不建」是一个**需要留痕的决定**。若不写，六个月后有人看到「46 条上行违规」
会自然地提议「我们应该建一个 CapabilityRegistry」——然后重新走一遍这三份审计。
本 ADR + 重开条件的价值就是阻止这个循环。
