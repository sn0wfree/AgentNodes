# P0-2 D — ToolContext DI 注入扩展

> **Status:** Applied (branch `p0-2-capability-seams`, follow-up to A/B/C)
> **承接:** A (DataStore) + B (BacktestResult) + C (ExecutionSandbox) 完成。本步把三个 capability seam 作为可选字段注入 ToolContext，工具可通过构造器接收依赖。

## 目标

`ToolContext` 新增三个可选 capability seam 字段：
- `data_store: DataStore | None` —— 工具直接通过 Protocol 访问数据层
- `sandbox: ExecutionSandbox | None` —— 工具按 Protocol 做 AST/path 校验
- `backtest_engine` 暂不注入（与 Strategy Protocol 绑定，P0-3 范围）

v0.1 阶段字段都是 `Optional` 且默认 None；既有调用方不受影响。新工具按需使用字段。

## 设计

### ToolContext 新增字段

```python
@dataclass
class ToolContext:
    # ... 现有 13 字段 ...
    # P0-2 D: capability seams
    data_store: Optional[DataStore] = None
    sandbox: Optional[ExecutionSandbox] = None
```

### AgentLoop 注入点

`AgentLoop` 在 `_make_tool_context()` / 类似位置构造 `ToolContext` 时：
```python
ctx = ToolContext(
    workspace=self._workspace,
    session_id=self.session_id,
    # ... 现有字段 ...
    data_store=get_store(),       # P0-2 A 默认 duckdb
    sandbox=StaticSandbox(self._workspace),  # P0-2 C
)
```

v0.1 在 AgentLoop 内部构造 ToolContext 时自动注入；用户（profile 等）仍可显式覆盖。

### 工具使用示例（新建 helper）

新建 `core/agent/tools_capability.py`，提供：
- `get_data_store(ctx)` / `get_sandbox(ctx)` —— 缺失时报错
- 让工具自动 fallback 到旧的 `db.py` 函数 / `sandbox` 模块函数

## 实施步骤

| 步骤 | 内容 | 验证 |
|------|------|------|
| D1 | `ToolContext` 加 2 字段（`data_store` / `sandbox`），可选 + 默认 None | ToolContext 创建兼容 |
| D2 | `AgentLoop._build_tool_context()` 自动注入默认 store/sandbox | 单测：context 字段非 None |
| D3 | helper 模块 `tools_capability.py` 提供 `get_data_store` / `get_sandbox` | 单测 + 路径解析 |
| D4 | 测试：现有 AgentLoop 调用方仍工作；新增 helper 单独测试 | 现有 112+ 测试全绿 |

## 风险

| 风险 | 缓解 |
|------|------|
| ToolContext 变 god object | 字段 Optional；调用方按需；后续可拆 Component Container |
| 循环依赖（AgentLoop → DataStore → AgentLoop） | DataStore 不依赖 AgentLoop；get_store() 是函数调用 |
| 自动注入破坏现有测试 | 字段默认 None；只有 AgentLoop 在 _build_tool_context 时填 |

## 不在 v0.1 范围

- ToolContext → Component Container 重构（太大，独立 PR）
- BacktestEngine 注入（需要先定义 BacktestEngine Protocol，P0-3）
- 自动按工具类型注入特定 Provider（需要 TypeAdapter）