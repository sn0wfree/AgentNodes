# P0-1 A3 — EventV2 增加 parent_event_id / branch_id 字段

> **Status:** Applied (branch `p0-1-event-sourcing`)
> **承接:** A1 schema 已加列（占位），A2 EventV2 已下沉。本步把字段真正接入 EventV2 数据模型。

## 目标

把 `parent_event_id`（trace 树）和 `branch_id`（fork 分支）字段从 schema 层升级到
EventV2 dataclass 层，并保证完整的 round-trip 兼容。

## 字段定义

| 字段 | 类型 | 默认 | 用途 |
|------|------|------|------|
| `parent_event_id` | `str \| None` | `None` | 父事件 ID，支持 trace 树（迭代内工具调用→父消息，subagent→父 agent_loop_start）。无父时 None。 |
| `branch_id` | `str` | `"main"` | fork 分支标识。默认 `"main"` 与 schema DEFAULT 一致。fork 由 P0-1 C 阶段使用，本步只留字段。 |

## 实现要点

### 1. EventV2 dataclass 字段

```python
@dataclass
class EventV2:
    id: str
    aggregate_id: str
    seq: int
    type: str
    data: Dict[str, Any]
    parent_event_id: Optional[str] = None      # 新增
    branch_id: str = "main"                     # 新增
    time_created: float = field(default_factory=time.time)
```

注意：`branch_id` 是 str（不是 Optional）—— schema 是 `NOT NULL DEFAULT 'main'`，
dataclass 默认值与 schema 一致，便于不传参时也能直接落库。

### 2. 序列化

- `to_row()`：`parent_event_id` 写为列，`branch_id` 写为列（默认 "main"）
- `from_row()`：从 dict 读取两字段，缺失时使用默认值（`None` / `"main"`）以兼容旧 DB
- `to_dict()` / `from_dict()` / `to_json()` / `from_json()`：同步处理

### 3. EventStore 调用方

- `emit()` 用 `EventV2.create(aggregate_id, seq, type, data, parent_event_id=?, branch_id=?)`
  v0.1 阶段不主动传，依赖默认值
- `_replay()` 内 tuple→dict 转换新增两个键
- `to_row` 的 INSERT 包含新列（dict 构造器会处理）

### 4. 反向兼容

- `from_row` 读到旧数据（无 parent_event_id / branch_id 字段）→ 用默认值填充
- 不需要回填脚本（schema 已有 DEFAULT 'main'）
- 测试断言 round-trip 后 `branch_id == "main"`

## 验证

- `test_event_v2.py` 扩展：to_row/from_row/to_dict/from_dict round-trip 包含新字段
- `test_event_store.py`：emit→replay 后 `branch_id == "main"`、`parent_event_id` 默认 None
- `test_trace_projection.py`：不受影响（投影读 type filter，不依赖新字段）
- ruff 新模块 + 改动文件无新告警

## 后续

- P0-1 B：fork/resume 时 `branch_id` 实际生效
- P0-1 C：parent_event_id 由 emit 调用方根据上下文填充（loop 迭代、子 agent）