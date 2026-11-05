# Phase 10: CircuitBreaker + RetryPolicy

## 1. 目标

将 `_check_no_progress` 升级为 `ToolLoopCircuitBreaker` 三状态机，新增 `RetryPolicy` 指数退避，整合进 AgentLoop。

## 2. 设计

### 2.1 ToolLoopCircuitBreaker 三状态机

```
CLOSED → (failure ≥ threshold) → OPEN → (cooldown expired) → HALF_OPEN → (success) → CLOSED
                                                                               → (failure) → OPEN
```

**触发条件**（任一满足即触发 Open）：
1. 同 `tool_name` 连续失败 ≥ `failure_threshold`（默认 3）
2. 同 `tool_call_hash` 重复 ≥ `no_progress_window`（默认 3）
3. 总失败数 ≥ `max_total_failures`（默认 10）

**冷却机制**：OPEN 后 `cooldown_seconds`（默认 60s）进入 HALF_OPEN，放一次尝试决定是否恢复 CLOSED。

**行为**：OPEN 时转 tool message 回 history，让 LLM 自己纠正（**不抛异常**）。

### 2.2 RetryPolicy 指数退避

```python
@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    retryable_exceptions: tuple[type[Exception], ...] = (LLMError,)
```

- `get_delay(attempt: int) → float`：计算指数退避延迟
- `should_retry(exception, attempt: int) → bool`

### 2.3 集成点

| 位置 | 集成方式 |
|---|---|
| `AgentLoop.__init__` | 新增 `circuit_breaker` / `retry_policy` 参数 |
| `_execute_tool_call` | 成功后 `record_success`，失败后 `record_failure` |
| `_check_no_progress` | 前置检查 `circuit_breaker.is_open()` |
| `run/arun` 主循环 | OPEN 时转 tool message 回 history |
| LLM 调用 | 失败时走 RetryPolicy 重试 |

## 3. 接口

```python
class BreakerState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 3
    no_progress_window: int = 3
    max_total_failures: int = 10
    cooldown_seconds: float = 60.0

class ToolLoopCircuitBreaker:
    def record_success(self, tool_name: str) -> None
    def record_failure(self, tool_name: str) -> None
    def record_no_progress(self) -> None
    def is_open(self) -> bool
    def try_half_open(self) -> bool
    def state(self) -> BreakerState
    def reset(self) -> None

@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    def get_delay(self, attempt: int) -> float
    def should_retry(self, exception: Exception, attempt: int) -> bool
```