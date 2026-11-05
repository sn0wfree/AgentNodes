# P0-2 C — ExecutionSandbox Protocol 实施

> **Status:** Applied (branch `p0-2-capability-seams`, follow-up to A+B)
> **承接:** A (DataStore) + B (BacktestResult) 完成。本步把 `core/agent/sandbox.py` 的静态校验抽象成 Protocol，为未来 RestrictedPython / 容器化沙箱留锚点。

## 目标

`ExecutionSandbox` Protocol 提供：
- `validate_source(source) -> (bool, str)` — 现有 AST 校验
- `resolve_write(rel_path) -> Path` / `resolve_read(rel_path) -> Path` — 现有 PathWhitelist
- `execute_strategy(...)` / `allow_network(...)` / `get_resource_usage()` — 接口预留，v0.1 由 `StaticSandbox` 实现为 no-op

实现：
- `StaticSandbox`（`core/agent/sandbox/static_sandbox.py`）—— 包装 `validate_python_source` + `PathWhitelist`；`execute_strategy` 暂走 `backtest.py` 现有 subprocess 路径（v0.2 改造）

向后兼容：保留 `core/agent/sandbox.py` 模块顶层函数 `validate_python_source` / `DEFAULT_*` 常量 / `PathWhitelist` 类，所有现有调用方继续工作。

## 实施步骤

| 步骤 | 内容 | 验证 |
|------|------|------|
| C1 | `core/agent/sandbox/protocol.py` 定义 `ExecutionSandbox` Protocol | Protocol runtime_checkable 检查 |
| C2 | `core/agent/sandbox/static_sandbox.py` 实现 `StaticSandbox`（包装现有函数） | 所有方法调用与 legacy 函数等价 |
| C3 | `tests/test_execution_sandbox.py` 测试 Protocol + StaticSandbox | 6+ 测试通过 |
| C4 | （可选）`__init__.py` re-export | import 路径干净 |

## 文件结构

```
core/agent/sandbox/
    __init__.py            # 新建
    protocol.py            # 新建 — ExecutionSandbox Protocol
    static_sandbox.py      # 新建 — StaticSandbox（包装 validate_python_source + PathWhitelist）
    (sandbox.py 保留为旧 API 兼容层)
```

## 风险

| 风险 | 缓解 |
|------|------|
| 双重实现（StaticSandbox 包装 vs legacy 函数）可能漂移 | StaticSandbox 方法直接调用 legacy 函数（无重写逻辑）；现有测试即等价 oracle |
| `execute_strategy` v0.1 no-op 引发调用方困惑 | 显式 `NotImplementedError`，文档说明 |
| Protocol runtime_checkable 性能 | 仅在 boundary 处用；StaticSandbox 是普通类不是 Protocol |