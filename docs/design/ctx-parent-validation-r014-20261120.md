# 父侧 ctx 校验收口 R-014 方案（第三轮复审候选 I3）

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **来源**：第三轮架构复审候选 I3（`C:\Users\1\AppData\Local\Temp\architecture-review-20261120-163911.html`，OS temp 不入库）。
> **前置**：B2.3 codec（ctx_v + 9 字段白名单 + to_wire/from_wire + probe_ctx_echo）已实施并验收。
> **范围**：让「坏 ctx 不出父进程」的宣称在生产唯一公开 seam 上真正成立；不动 worker 侧拒收契约。

## 0. 目标与非目标

**目标**：
- codec 提供公开纯校验函数 `validate_wire(obj)`；
- 生产唯一公开入口 `isolate_tool_call` 对 versioned-dict 形态做父侧校验，使一条畸形 wire dict 在父进程抛 `IsolationContextError`；
- `ToolContext` / `None` 形态行为逐字不变（仍经 `to_wire` 编码，本就父侧校验）；
- 合法 wire dict 仍原样透传（identity 保持），零性能语义变化。

**非目标**：
- 不改 `worker.call` / `_normalize_ctx_wire`（内部适配面）—— worker 侧 3 个结构化拒收测试**零改动**，继续验证纵深防御；
- 不改 from_wire 的校验规则（版本 / 白名单 / 路径 / 类型逐字不变），只把校验逻辑抽成可复用纯函数；
- 不改毒化语义（毒化只属 deadline/EOF）、不改 E5 终态、不改 ctx schema（CTX_VERSION 不 +1）；
- 不碰冻结删除批。

## 1. 现状证据（已回读源码核对）

### 1.1 宣称与现实的偏差
- `isolation.py:481-483`（`isolate_tool_call` docstring）：「发送前编码失败在父进程抛 `IsolationContextError`（坏 ctx 不出父进程）」。
- `_normalize_ctx_wire`（`isolation.py:111-140`）四种形态：`None` → `to_wire(ToolContext())`；`ToolContext` → `to_wire`；带 `ctx_v` 的 dict → **原样透传（:137，唯一检查是 `ctx_v` 存在）**；裸 dict → 拒。
- 因此畸形 wire dict（多余键 / 非法 `ctx_v` / 错类型 / 非绝对或含 `..` 的路径）在父侧畅通，到 worker 才由 `_decode_ctx`（`_isolation_worker.py:84-101`）经 `from_wire` 拒收为结构化 error 行（`_emit({"result": {"status": "error", ...}})`，不毒化）。
- 宣称对 `ToolContext` / `None` / 裸 dict 成立，对 versioned-dict 形态不成立。

### 1.2 生产调用面（已核实）
- `isolate_tool_call` 生产调用点唯一：`_isolated_exec.py:246`（run_isolated）；它内部经 `worker.call`（`isolation.py:492` 或 `:503`）→ `_normalize_ctx_wire`（`:296`）。
- `worker.call` 在生产中**只由 isolate_tool_call 调用**；其余调用点全在 tests/。
- 故在 `isolate_tool_call` 入口校验 = 覆盖全部生产路径。

### 1.3 codec 现状
- `_isolation_context.py:23-30` `__all__ = [CTX_VERSION, IsolationContextError, IsolationCtxWire, WIRE_FIELDS, from_wire, to_wire]` —— **无独立纯校验函数**。
- `from_wire`（`:114-171`）内联全部校验：`_check_version`（`:71-83`）+ 白名单外键拒（`:132-137`）+ 类型/路径校验 + 构造 `ToolContext`。
- 校验逻辑可抽成 `validate_wire(obj) -> None`（只校验、不构造），`from_wire` 改为先 `validate_wire(obj)` 再构造，规则逐字不变。

### 1.4 既有测试锚
- `test_isolation_platform_matrix.py:104-106` `test_versioned_dict_passes_through`：`_normalize_ctx_wire(wire) is wire`（identity 断言）—— 不动。
- `test_isolation_platform_matrix.py:90-93` `test_bare_ctx_dict_rejected`：唯一父侧拒收测试，覆盖裸 dict —— 不动。
- `test_isolation_channel.py:129-153,187-190` 三个畸形 wire dict 经 `worker.call` → 结构化 error 行 + worker 存活 —— **全部不动**（Q18）。
- WP-4 验收（`architecture-refactor-execution-roadmap-20261119.md:141,180-187,306-313`）要求「缺失/无效/不兼容版本显式报错 + 对象/句柄不跨进程泄漏」，closing evidence 为 codec 测试 + worker 错误行；本方案补上父侧显式报错这一环，不删任何既有覆盖。

## 2. 设计（grilling 19 题全 A + I3 落点二次确认：方案一）

| # | 决策 | 落位 |
|---|---|---|
| Q17′ | codec 新增公开纯 `validate_wire(obj)`（抽自 from_wire，返回 None / 抛 IsolationContextError），from_wire 改为「先 validate_wire 再构造」 | `_isolation_context.py` |
| Q17″ | `isolate_tool_call` 入口处对 dict 形态调 `validate_wire` 后再交给 `worker.call`；`ToolContext` / None 形态不变 | `isolation.py:491` 之前 |
| Q18 | `test_versioned_dict_passes_through` identity 断言保留；新增父侧拒收测试（多余键 / 非法 ctx_v）；worker 侧三测试零改动 | `test_isolation_platform_matrix.py` + 新测试 |

**实现要点**：
- `validate_wire(obj)` 的校验顺序与 from_wire 现有一致：① dict 类型；② `ctx_v` 合法/兼容；③ 白名单外键；④ 路径绝对 + 无 `..`；⑤ 类型（str/list/Path/tuple）。返回 None 表示合法。
- `from_wire` 保留现有返回 `ToolContext` 的语义，内部改为 `validate_wire(obj)` + 值强转构造；对外行为逐字等价（同一异常类型与消息）。
- `isolate_tool_call` 入口校验只针对 dict 形态（与 `_normalize_ctx_wire` 接受的双形态对齐）：`if isinstance(ctx, dict): validate_wire(ctx)`；裸 dict（`ctx_v` 缺失）由 `validate_wire` 的 `_check_version` 拒（原 `_normalize_ctx_wire` 也会拒，消息不同但都在父侧抛同类异常——检查现有裸 dict 测试断言 `match="bare ctx dict"`，需保持该消息，见风险节）。
- `__all__` 加 `validate_wire`。

## 3. 分批与验收（单提交）

| 验收项 | 落点 |
|---|---|
| validate_wire 公开且纯（不改 obj、不构造 ToolContext） | codec + `__all__` |
| from_wire 复用 validate_wire，行为逐字等价 | codec 既有契约测试全绿 |
| 生产入口父侧校验 | isolate_tool_call：畸形 dict → IsolationContextError |
| 合法 wire identity 透传保持 | test_versioned_dict_passes_through 不动且绿 |
| worker 侧三测试零改动且绿 | test_isolation_channel 三例不动 |
| 数字同步 | R-007：sync + check_framework_numbers |

**回滚**：单提交回退即恢复；零残留。

## 4. 风险与回滚

- **裸 dict 消息兼容**：`test_isolation_platform_matrix.py:90-93` 断言 `match="bare ctx dict"`。方案：入口校验前先复现 `_normalize_ctx_wire` 的形态判定——`if isinstance(ctx, dict) and "ctx_v" not in ctx` 保留原 `IsolationContextError("bare ctx dict is not allowed ...")`，有 `ctx_v` 才调 `validate_wire`。这样裸 dict 消息逐字不变。
- **重复校验**：合法 wire 在入口校验一次、worker from_wire 再校验一次（纵深防御），两次都是纯函数、开销可忽略；语义零变化。
- **ToolContext 形态**：不经入口校验（to_wire 产出必合法），行为逐字不变。
- **异常时序**：父侧异常在 spawn 之前抛出——这正是宣称要求的时序，无 worker 泄漏。

## 5. 明确不做

① 改 `worker.call` / `_normalize_ctx_wire`；② 改 worker 侧三个结构化拒收测试；③ 改 ctx schema / CTX_VERSION；④ 改毒化 / E5 / deadline 语义；⑤ 任何删除批动作。

## 6. 实施前确认

本方案来自 grilling 全 A（Q17/Q18）+ I3 落点二次确认（方案一：isolate_tool_call 入口）。**未获批不得实施任何代码**；获批后按 R-007 单提交实施，doc first 纪律已满足（本文件）。

## 7. 执行记录（2026-11-20 · 已实施 · 用户"go"批准）

- `_isolation_context.py`：新增公开纯 `validate_wire(obj)`（入 `__all__`），从 `from_wire` 抽出完整校验（dict 类型 / 版本 / 白名单外键 / 路径 / 类型，异常消息逐字不变）；`from_wire` 改为「先 validate_wire 再值强转构造」，对外行为逐字等价。
- `isolation.py`：`isolate_tool_call` 入口对 dict 形态把关——裸 dict 保留原 `bare ctx dict` 消息、带 `ctx_v` 的调 `validate_wire`；spawn 之前抛 `IsolationContextError`。"坏 ctx 不出父进程"在公开 interface 上真正成立。
- `worker.call` / `_normalize_ctx_wire`：**零改动**；worker 侧三个结构化拒收测试零改动且全绿（纵深防御仍验）。
- `test_isolation_platform_matrix.py`：新增 3 例（父侧多余键拒 / 非法 ctx_v 拒 / validate_wire 纯函数）；identity 透传与裸 dict 拒收锚不动。
- 验证：I3 面 48 passed；全量 `-n 6` **8058 passed** / 0 failed（+3 新例）；batch_gate ALL GREEN；数字 464/96,959（L3 51,262）。
