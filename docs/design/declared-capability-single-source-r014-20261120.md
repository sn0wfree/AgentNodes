# E5 declared capability 单点解析 R-014 方案（第三轮复审候选 I1）

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **来源**：第三轮架构复审候选 I1（`C:\Users\1\AppData\Local\Temp\architecture-review-20261120-163911.html`）。
> **前置**：B2.4 生产接线 + B2.5 准入 + 第二轮候选 1+2（IsolationAdmission 深模块）已实施。
> **范围**：两条 tool 路径的 declared 计算收进 IsolationAdmission 单点，修掉裸 getattr 偏差。

## 0. 目标与非目标

**目标**：
- tool 的 declared（有效超时能力）只有一个计算点：`isolation_admission.declared_capability(registry, name, tool)`；
- 两条 tool 路径（in-process / 隔离执行器）在 `registry=None` 时都走 `effective_capability(tool)`，非法声明回落 unstoppable；
- 执行器侧裸字面量 `"unstoppable"` 换成 `TIMEOUT_CAPABILITY_UNSTOPPABLE` 常量；
- 补两条此前零覆盖的 `registry=None` 分支测试。

**非目标**：
- 不改 E5 终态语义（state 仍按实际观测、不得由声明推断）；
- 不改 sink 本身：`core.py::_record_timeout_outcome` 是**四类调用共享**（tool × 2 + llm × 2，`core.py:873/905` 直传常量），方案**不**把解析挪进 sink——那会改写 LLM 路径的 declared；
- 不改 LLM 路径的 declared 常量；不改准入规则（C1/C2/C3）、不改四级能力语义；
- 不碰冻结删除批。

## 1. 现状证据（已回读源码核对）

### 1.1 两条 tool 路径的 declared 计算
- in-process（`tools.py:257-268`）：`self.registry.timeout_capability(tc.name) if registry is not None else getattr(tool, "timeout_capability", TIMEOUT_CAPABILITY_UNSTOPPABLE)` —— 裸 getattr **绕过准入过滤与非法值回落**（声明 `"bogus"` 原样透传）。
- 执行器（`_isolated_exec.py:294-302`）：`self._host.registry.timeout_capability(name) if registry is not None else "unstoppable"` —— 裸字面量。
- 二者在 `registry is not None` 时都调 `registry.timeout_capability(name)`（`tools/core.py:870-876` → `effective_capability(self._tools.get(name))`）—— 正确分支已同源。分歧只在 `registry=None` 回落。

### 1.2 共享 sink（不改动）
`core.py:970-986` `_record_timeout_outcome(kind, name, declared, state)` 原样 append，不做任何推断/校验。四类调用者（`scan_e5_sink_callers.py` 已核实）：
- `_isolated_exec.py:302` tool（本方案作用域）
- `tools.py:266` tool（本方案作用域）
- `core.py:873` llm（`TIMEOUT_CAPABILITY_ABANDONABLE` / `CANCELLABLE`，**不动**）
- `core.py:905` llm（`TIMEOUT_CAPABILITY_UNSTOPPABLE`，**不动**）
故"收敛到 sink"会波及 LLM 路径——本方案改为**收敛到 helper**，由两条 tool 调用点各自调用同一个纯函数。

### 1.3 常量与 helper
- `isolation_admission.py:22-31`：`TIMEOUT_CAPABILITY_UNSTOPPABLE = "unstoppable"` 等四级常量 + `TIMEOUT_CAPABILITIES` frozenset。
- `effective_capability(tool)`（`:138-155`）：None / 非法值 / 准入失败 → unstoppable；否则原声明。
- `tools.py:19-23` 已从 `tools.core` 导入 `TIMEOUT_CAPABILITY_UNSTOPPABLE`（core 再导出 admission 常量）；`_isolated_exec.py` 未导入，用裸字面量。

### 1.4 设计约束
- `b24-production-wiring-r014-20261120.md:244-245`：`declared` 字段必须取准入过滤后的 registry 值，**修正 E2 指出的裸 getattr 偏差**；任何情况下不得由 timeout_capability 推断实际状态。
- `b24:45,281`、`isolated-executor-deepening-r014-20261120.md:18`、`executor-host-seam-r014-20261120.md:15`：不改 E5 终态语义。
- `admission-module-extraction-r014-20261120.md:15-16`：不改准入判定行为、不改四级能力语义。

### 1.5 测试覆盖缺口
- `test_isolated_exec_wiring.py:189-193` 覆盖执行器 registry-present 分支（`declared=="isolatable"`）；fixture `HostStub.registry` 恒非 None。
- `test_gateway_timeout_and_strict_history.py:1013-1055` 覆盖 in-process registry-present 分支（注册工具默认 unstoppable）。
- **两条 `registry=None` 分支均零测试覆盖**（已核实：无任何测试把 loop.registry / host.registry 置 None 走超时记录路径）。

## 2. 设计（grilling 全 A：Q10-Q12）

| # | 决策 | 落位 |
|---|---|---|
| Q10 | `isolation_admission.declared_capability(registry, name, tool)`：registry 非 None → `registry.timeout_capability(name)`；registry None → `effective_capability(tool)`（含 None → unstoppable 常量）；入 `__all__` 与 core re-export | `isolation_admission.py` + `core.py` |
| Q10′ | 两 tool 调用点改调该 helper，只传 `(registry, name, tool)`；**不动共享 sink**（LLM 路径不受影响） | `tools.py:257-268`、`_isolated_exec.py:294-302` |
| Q10″ | 执行器侧裸字面量 `"unstoppable"` 消除（由 helper 统一返回常量） | `_isolated_exec.py` |
| Q11 | 行为确认：in-process `registry=None` 时非法声明 `"bogus"` 从「原样透传」改为「回落 unstoppable」（对齐 b24:244-245 的设计裁决） | helper 语义 |
| Q12 | 先补两条 registry=None 分支测试（执行器 + in-process），再改实现 | `test_isolated_exec_wiring.py` + 新测试 |

**实现要点**：
- helper 签名 `declared_capability(registry: Any, name: str, tool: Any) -> str`，纯函数不抛异常：
  ```
  if registry is not None:
      return registry.timeout_capability(name)
  return effective_capability(tool)
  ```
- `tools.py` 在超时分支：`_declared = declared_capability(self.registry, tc.name, tool)`（tool 变量已在该作用域可用——实施时核实），删原 getter 表达式。
- `_isolated_exec.py::_record_outcome`：`_settle → _record_outcome(tc, outcome)` 作用域**无 tool 对象**（已核实 run/arun 的 tool 未传入），故 `declared = declared_capability(self._host.registry, name, None)`——registry=None 时 `effective_capability(None)` → unstoppable，与现字面量 `"unstoppable"` 逐字等价，且天然消除裸字面量。
- core re-export 加 `declared_capability`（与其余 admission 符号同款，保持 loop import 面稳定）。

## 3. 分批与验收（单提交）

| 验收项 | 落点 |
|---|---|
| helper 公开且纯 | isolation_admission + __all__ + core re-export |
| 两 tool 调用点各一行 | tools.py / _isolated_exec.py |
| registry=None 两分支测试 | 新增 2 例（非法声明回落 unstoppable） |
| LLM 路径 declared 不变 | core.py:873/905 零改动；既有 LLM 超时测试全绿 |
| E5 终态语义不变 | state 仍实际观测；既有 E5 测试全绿 |
| 数字同步 | R-007 |

**回滚**：单提交回退恢复；零残留。

## 4. 风险与回滚

- **`_isolated_exec` 作用域 tool 对象**：若 `_record_outcome` 拿不到 tool，helper 用 `tool=None` 回落 unstoppable——与现语义逐字等价（该路径 registry=None 本就恒 unstoppable），不引入新行为。
- **Q11 行为变更**：仅 in-process registry=None + 非法声明这一种组合变化（"bogus" → unstoppable）；这是 b24 已裁决的偏差修正，须在测试里钉住。
- **裸 getattr 残余**：tools.py 的 `TIMEOUT_CAPABILITY_UNSTOPPABLE` import 若因此不再使用需清理（检查其它用途）。
- **数字漂移**：每批源码行数变化后重跑 sync + check_framework_numbers。

## 5. 明确不做

① 改共享 sink / LLM 路径 declared；② 改 E5 终态语义；③ 改准入规则 / 四级能力；④ 改 registry-present 分支行为（已正确）；⑤ 任何删除批动作。

## 6. 实施前确认

本方案来自 grilling 全 A（Q10-Q12）。**未获批不得实施任何代码**；获批后按 R-007 单提交实施，doc first 纪律已满足（本文件）。
