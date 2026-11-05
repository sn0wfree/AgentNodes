# B1-c mid-loop 压缩移植 — 设计 — 2026-10-28

> 目标：react 节点在长会话中与 AgentLoop 一样做上下文压缩，且用**同一
> 实现**（不复制逻辑）。

## 1. 调查：压缩核心已经是宿主解耦的

`loop_compaction.py` 的 `_maybe_compact_impl(messages, *, run_compact)`
只依赖四个宿主属性：

| 依赖 | 用途 |
|---|---|
| `self.cc` | `CompactConfig`（阈值/比例/layer 开关） |
| `self.config` | `model_context_tokens` / `model_max_output_tokens` |
| `self.client` | LLM 摘要调用 |
| `self._persist_compaction_event` | 落 CompactionMessage 事件 |

且 `run_compact` 是**参数注入**的——这就是解耦点。`_run_compact_messages`
负责把宿主配置喂给 `compact_messages()`。

**调用时机**（AgentLoop）：`strategy/steps/compaction.py:43-45` —— 每次
**迭代开始前**执行，是循环的第一步。

**react 节点**：`react.py:81` `for iteration in range(...)` 循环体开头，
与 AgentLoop 的插入位置语义等价。

## 2. 设计：注入 compactor，不搬代码

```python
@dataclass
class ReactConfig:
    ...
    compactor: Callable[[list[dict]], tuple[list[dict], list[str]]] | None = None
    # 签名: messages -> (messages, applied_layers)
    # None = 不压缩（旧行为）
```

节点内：

```python
for iteration in range(1, cfg.max_iterations + 1):
    if cfg.compactor is not None:
        try:
            messages, applied = cfg.compactor(messages)
            if applied:
                emit("context.compacted", {"layers": applied, "iteration": iteration})
        except Exception as exc:
            emit("context.compact_failed", {"error": str(exc)})
    ...
```

**为什么注入而非 import**：react 节点在 `core/runtimes/`（平台层），
`loop_compaction` 在 `core/agent/`（域）。平台 → 域是反向依赖，架构门禁
会拦住。注入是唯一合规的接法（与 `ctx_factory` 同理）。

### 2.1 生产侧：新建一个可复用的 compactor 工厂

现有 `_maybe_compact_impl` 绑在 mixin 的 `self` 上。抽出独立函数：

```python
# core/agent/compaction_runtime.py（新）
def make_compactor(
    *, compact_config, model_context_tokens, model_max_output_tokens,
    llm_client, persist=None, state: dict | None = None,
) -> Callable[[list[dict]], tuple[list[dict], list[str]]]
```

内部复用 `_maybe_compact_impl` 的**同一套**逻辑（把它的主体搬进这个函数，
mixin 方法改为调用它）——仍然单一事实源。

## 3. 验收

| 测试 | 断言 |
|---|---|
| react + compactor：未超阈值 | 消息不变，无 compacted 事件 |
| react + compactor：超阈值 | 消息被压缩，发 `context.compacted` |
| compactor 抛异常 | 原样继续，发 `context.compact_failed`，**不崩** |
| 无 compactor | 行为与今天一致（回归） |
| 压缩发生在 LLM 调用**之前** | 断言 client 收到的消息已是压缩后的 |
| mixin 与新工厂行为一致 | 同一输入两路径产出相同 |

## 4. 风险

| 风险 | 缓解 |
|---|---|
| 压缩位置错（压缩后才调 LLM 或反之） | 显式测试调用序 |
| 压缩失败拖垮整轮 | try/except + 事件，绝不外抛 |
| 与 AgentLoop 结果不一致 | 抽共享函数而非复制 |
| chat 单轮（max_iterations=1）明确无需压缩 | 可选：仅 max_iterations>1 时启用，或由调用方决定是否注入 |

## 5. 本次范围

只做「react 能压缩且不崩」。不做：
- 压缩策略本身（复用现有 `compact_messages`，不动）
- SSE 层的压缩进度事件（已有 `context.compacted` 足够）
