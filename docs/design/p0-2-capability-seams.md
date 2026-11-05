# P0-2 Capability Seam 接口分离（2026-08-16）

> **Status:** Completed (branch `p0-2-capability-seams`, merged to `main` as `a4c2897`)
> **承接:** P0-1 完成事件源架构（fork/resume/replay）。P0-2 解决"换 Provider = 整套能力迁移"的接口定义问题。
> **用户决策:** ① P0-1 先于 P0-2 ✅ 完成 ② git first doc first ③ 预算 2 周

## 完成状态

| 阶段 | 标题 | 状态 | 提交 |
|------|------|------|------|
| 母文档 | 设计（本文档） | ✅ | `d250788` |
| **Phase A** | DataStore Protocol | ✅ | `d474afb` |
| **Phase B** | 统一 BacktestResult | ✅ | `0b4a186` |
| **Phase C** | ExecutionSandbox Protocol | ✅ | `af98489` |
| **Phase D** | ToolContext DI 注入 | ✅ | `6a9f7b0` |
| 合并 | merge commit | ✅ | `a4c2897` |

**测试**：38 个 P0-2 新增测试 + 350+ 回归测试全绿（合计 365+）。
**ruff**：4 存量错误（C901 + 2×I001 + W292），P0-2 未引入新告警。

## 用户价值兑现

✅ DataStore Protocol —— 45 个 DuckDB 耦合点有了抽象边界；测试可用 in-memory provider
✅ BacktestResult 统一 —— 双 dataclass 收敛到一个 5 字段权威定义，向后兼容 shim
✅ ExecutionSandbox Protocol —— 为 RestrictedPython / Docker 沙箱留好锚点
✅ ToolContext DI —— `data_store` / `sandbox` 自动注入，工具通过 helper 消费

## 后续（P0-3 / P1 候选）

- BacktestEngine Protocol（合并 `BacktestCallbacks` 与 `BaseStrategy`）
- 三条回测路径完全收敛（YAML / Callback / Bar-by-bar）
- SubprocessSandbox 实现 v0.1（带 timeout + env whitelist）
- DuckDB → SQLite/PostgreSQL Provider 实现
- ToolContext → Component Container 重构

---

## 0. 目标

数据源、回测引擎、沙箱三层明确区分 **"接口定义 / Provider 实现 / Consumer 使用方"**。当前每层都有硬编码耦合：

| 层 | 现状 | 问题 |
|----|------|------|
| **DataStore** | `core/db.py` 1165 行单体，30+ 调用点直接 `from .db import get_connection` | 切 SQLite/in-memory 测试 DB 必须 monkey-patch |
| **BacktestEngine** | 两个 `BacktestResult` dataclass + 两个 strategy 接口 + 三条执行路径 | 结果类型不兼容、FactorStrategy 鸭子类型未被正式认可 |
| **ExecutionSandbox** | 仅静态 AST 分析（`core/agent/sandbox.py`）；运行时隔离靠 subprocess + env whitelist | 没有正式 Protocol，未来 RestrictedPython/容器化难插入 |

LLM Provider 层（`core/llm/provider/`）是项目的**最佳实践模板**（Registry + Protocol + Factory + Zero core modification to add a provider）。P0-2 复制此模式到上述三层。

---

## 1. 决策摘要

### 1.1 DataStore Protocol

```python
# core/storage/data_store.py
class DataStore(Protocol, runtime_checkable):
    """Duck-typed persistence facade. Replaces hardcoded DuckDB calls."""

    # Lifecycle
    def init(self, workspace: Path) -> None: ...
    def get_connection(self, *, read_only: bool = False) -> Any: ...

    # Price data
    def save_ohlcv(self, workspace, data_map, *, strategy_name="default") -> int: ...
    def load_price_data(self, workspace, strategy_name, *, start=None, end=None) -> pd.DataFrame: ...
    def load_ohlcv_data(self, workspace, strategy_name, *, codes=None, ...) -> dict[str, pd.DataFrame]: ...

    # Factor data + registry
    def save_factor_data(self, workspace, strategy_name, factor_name, values) -> bool: ...
    def load_factor_data(self, ...) -> pd.DataFrame: ...
    def register_factor(self, ...) -> bool: ...
    def list_factors(self, ...) -> list[dict]: ...

    # Backtest results / weight / nav
    def save_backtest_result(self, workspace, result) -> bool: ...
    def list_backtest_results(self, ...) -> list[dict]: ...
    def save_weight_history(self, ...) -> bool: ...
    def load_weight_history(self, ...) -> pd.DataFrame: ...
    def save_nav_history(self, ...) -> bool: ...
    def load_nav_history(self, ...) -> pd.Series: ...

    # Validation cache
    def cache_validation(self, ...) -> bool: ...
    def list_validation_cache(self, ...) -> list[dict]: ...

    # Import metadata + fingerprint
    def get_last_import_date(self, workspace, *, codes=None) -> Optional[float]: ...
    def update_import_meta(self, ...) -> bool: ...
    def update_data_fingerprint(self, ...) -> bool: ...
    def get_data_fingerprint(self, ...) -> Optional[dict]: ...
```

**实现：**
- `DuckDBDataStore`（`core/storage/duckdb_store.py`）—— 包装现有 `db.py` 函数
- 保留 `db.py` 模块函数作为过渡期 shim（不删，避免一次性大爆炸）；新代码统一通过 `DataStore` 接口

**Registry（仿 LLM provider）：**
```python
# core/storage/data_store_registry.py
_REGISTRY: dict[str, type[DataStore]] = {"duckdb": DuckDBDataStore}

def register_store(name: str, cls: type[DataStore]) -> None: ...
def get_store(name: str | None = None) -> DataStore: ...
```

### 1.2 BacktestEngine Protocol

```python
# core/backtest_engine/protocol.py
class BacktestEngine(Protocol):
    """Unified backtest runner. Both callback-based and strategy-based
    engines conform to this contract via thin adapters."""

    def run(
        self,
        strategy: Strategy,
        price_panel: pd.DataFrame,
        *,
        config: BacktestConfig | None = None,
    ) -> BacktestResult: ...

class Strategy(Protocol):
    def compute_weights(
        self,
        date: pd.Timestamp,
        price_panel: pd.DataFrame,
        nav_history: pd.Series,
    ) -> dict[str, float]: ...
```

**关键决策：合并两个 `BacktestResult`**
- 在 `core/backtest_models.py` 新建权威 `BacktestResult`，统一字段（包含 `factor_failures: list[dict]`，默认空列表）
- 旧 `backtest_engine.BacktestResult` 和 `strategy_engine.BacktestResult` 改为 type alias 或保留向后兼容
- `FactorStrategy`（`config_runner.py` 内联 300+ 行）显式实现 `Strategy` Protocol

**三执行路径收敛：**
- YAML 路径（`run_from_yaml`）→ `StrategyEngine`（已是 Protocol 实现）
- Script 路径（`run_backtest`）→ 加 `CallbackEngineAdapter` 把 `BacktestCallbacks` 适配成 `Strategy`
- Engine runner 路径（`engine/runner.py` 的 `BaseEngine.run_backtest`）→ 返回 `Dict[str, Any]`（metrics only）保留为 v0.1（不在 P0-2 范围内收敛）

### 1.3 ExecutionSandbox Protocol

```python
# core/agent/sandbox/protocol.py
class ExecutionSandbox(Protocol):
    """Runtime code execution + resource limits."""

    # Static analysis (现有能力)
    def validate_source(self, source: str) -> tuple[bool, str]: ...

    # Path safety (现有能力)
    def resolve_write(self, rel_path: str) -> Path: ...
    def resolve_read(self, rel_path: str) -> Path: ...

    # Runtime execution (新接口)
    def execute_strategy(
        self, source: str, *, timeout: float = 30.0,
        memory_limit_mb: int = 512,
    ) -> ExecutionResult: ...

    # Network + resource control
    def allow_network(self, hosts: list[str] | None = None) -> None: ...
    def get_resource_usage(self) -> ResourceUsage: ...
```

**实现：**
- `StaticSandbox`（`core/agent/sandbox/static_sandbox.py`）—— 包装现有 `validate_python_source` + `PathWhitelist`；`execute_strategy` 走 subprocess（替代 `backtest.py` 的硬编码 subprocess 调用）
- 后续 `RestrictedPythonSandbox` / `DockerSandbox` 可作为 v0.2 接入

---

## 2. 实施步骤

### Phase E：DataStore Protocol（Week 1）

| 步骤 | 内容 | 验证 |
|------|------|------|
| E1 | `core/storage/data_store.py` Protocol + `DuckDBDataStore` 实现 | DataStoreProtocol 接口检查 + DuckDBStore 单测 |
| E2 | `core/storage/data_store_registry.py` 工厂 + 注册 `DuckDBDataStore` | registry.add("duckdb", DuckDBDataStore) 工作 |
| E3 | 迁移 5 个高频调用点（`config_runner.py`、`data_import.py`、`data_readiness.py`、`factor_tools.py`、`data_tools.py`）用 DataStore | 单测通过 + 接口调用而非 `from .db import` |
| E4 | `db.py` 标记 deprecated，新增 dataclass 装饰提示用 DataStore | 跑 ruff + 测试 |

**E3 优先级：** 选择调用频率高 + 业务核心的 5 个点。剩余调用点（CLI、研究 runner、validation/cli 等）保留旧路径——本步不强制全局切换，避免大爆炸。

### Phase F：BacktestEngine Protocol（Week 1-2）

| 步骤 | 内容 | 验证 |
|------|------|------|
| F1 | `core/backtest_engine/protocol.py` 定义 `BacktestEngine` + `Strategy` + 统一 `BacktestResult` | Protocol 检查 |
| F2 | `core/backtest_engine/strategy_engine_adapter.py` —— `StrategyEngine` 适配 | run() 返回统一 BacktestResult |
| F3 | `core/backtest_engine/callback_engine_adapter.py` —— `BacktestCallbacks` → `Strategy` 适配 | 端到端回测等价 |
| F4 | `FactorStrategy`（config_runner.py）显式实现 Strategy Protocol + 提取到独立模块 `core/backtest_engine/factor_strategy.py` | 单测 + import 改写 |
| F5 | YAML 路径与 Script 路径走统一 BacktestEngine 工厂 | 现有回测测试全绿 |

### Phase G：ExecutionSandbox Protocol（Week 2）

| 步骤 | 内容 | 验证 |
|------|------|------|
| G1 | `core/agent/sandbox/protocol.py` Protocol | Protocol 检查 |
| G2 | `core/agent/sandbox/static_sandbox.py` —— 包装 validate_python_source + PathWhitelist；execute_strategy 走 subprocess | subprocess timeout + env whitelist 测试 |
| G3 | `backtest.py` 的 subprocess 调用改为 `StaticSandbox.execute_strategy` | 回测测试通过 |
| G4 | `ToolContext` 增加 `sandbox: ExecutionSandbox` 字段 + 工具构造器注入 | 不引入循环依赖 |

---

## 3. 文件结构

```
core/storage/
    sqlite.py              # 已有
    event_schema.py        # 已有
    blob_schema.py         # 已有
    data_store.py          # 新增 — DataStore Protocol
    duckdb_store.py        # 新增 — DuckDBDataStore 实现
    data_store_registry.py # 新增 — 工厂

core/backtest_engine/
    __init__.py
    protocol.py            # 新增 — BacktestEngine + Strategy + BacktestResult
    strategy_engine_adapter.py   # 新增
    callback_engine_adapter.py   # 新增
    factor_strategy.py     # 新增 — 提取自 config_runner.py

core/agent/sandbox/
    __init__.py
    protocol.py            # 新增 — ExecutionSandbox
    static_sandbox.py      # 新增 — 包装现有 sandbox.py
    (sandbox.py 保留为旧 API 兼容层)
```

---

## 4. 测试

- `tests/test_data_store_protocol.py` —— Protocol 接口契约 + DuckDBDataStore 单测
- `tests/test_data_store_registry.py` —— 注册/获取
- `tests/test_backtest_engine_protocol.py` —— Strategy + Engine 契约 + 双引擎等价
- `tests/test_factor_strategy_extracted.py` —— FactorStrategy 独立模块测试
- `tests/test_static_sandbox.py` —— validate + execute_strategy + timeout
- `tests/test_sandbox_in_backtest.py` —— backtest.py 走新 Sandbox 后等价

**性能/等价性：** 把现有 195+ 测试当作等价 oracle——任何改写必须保持现有测试全绿。

---

## 5. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 双 BacktestResult 合并破坏现存 API | 高 | 旧类保留为 type alias（`BacktestResult = UnifiedBacktestResult`），新代码用统一类 |
| DataStore 迁移范围过广 | 中 | Phase E 只迁 5 个高频点；db.py 标 deprecated 保留 shim；下次大迭代继续迁 |
| FactorStrategy 提取触发大量 import 重写 | 中 | 提取后保持原签名/行为；只是物理位置移动 |
| Sandbox subprocess 引入新执行风险 | 中 | timeout + env whitelist 与现有 backtest.py 等价；先复用 `_build_restricted_env` |
| Protocol 抽象阻碍 DuckDB 特性使用 | 低 | Protocol 是最少公共子集；DataStore 仍可暴露 DuckDB 专属方法（独立 protocol `DuckDBCapable`） |
| 循环依赖（sandbox ↔ backtest） | 中 | Protocol 接口在 core 层；具体实现注册到工厂；ToolContext 注入避免硬引用 |

---

## 6. 成功标准

1. **Protocol 已定义**：`DataStore`、`BacktestEngine` + `Strategy`、`ExecutionSandbox` 三个 Protocol 在各自模块可被 `runtime_checkable` 检查
2. **至少一个实现**：每个 Protocol 都有至少一个具体实现（`DuckDBDataStore`、`StrategyEngineAdapter`、`StaticSandbox`）
3. **至少 5 个调用方迁移到 DataStore**：`config_runner.py`、`data_import.py`、`data_readiness.py`、`factor_tools.py`、`data_tools.py`
4. **BacktestResult 统一**：旧两处引用全部走新统一类（保持 type alias 向后兼容）
5. **195+ 现有测试全绿**（性能与功能等价 oracle）
6. **ruff check 干净**：新模块 0 错误；整体 ≤ 3 存量错误
7. **新增 ≥ 20 测试**：覆盖 Protocol 接口、Registry、Adapter、Sandbox

---

## 7. 与 P0-1 的衔接

P0-2 不动 `event_log` schema、不动 EventStore API。但部分数据存储路径（如 `save_backtest_result`）会从 `db.py` 函数迁移到 `DataStore` 方法——已有 schema/接口不变。

EventStore 与 DataStore 解耦：EventStore 只管 `event_log`（append-only），DataStore 管价格/因子/回测结果。两层可以独立替换。

---

## 8. 不在 P0-2 范围内

- 三条回测路径完全收敛（`engine/runner.py` 的 `BaseEngine` 路径 v0.1 不动）
- 完整 runtime sandbox 替换（RestrictedPython / Docker）—— 只到 subprocess timeout + env whitelist
- DuckDB → SQLite/PostgreSQL 切换 —— Protocol 已就位但实施后续
- WebUI 端到端使用 DataStore 接口 —— 后端为主

这些都在 P0-3+ 候选范围。