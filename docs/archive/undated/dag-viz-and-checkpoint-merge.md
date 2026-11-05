# DAG 可视化 + Checkpoints 合并方案

> Date: 2026-08-20

## 1. DAG 可视化（graph.json）

### 现状

- **已有** `GET /study/{id}/graph` 端点（study.py:1456），返回 StudyGraph 格式
- **已有** `DAGVisualization.tsx` 前端组件，渲染节点状态图
- **已有** `StudyGraph.to_dict()` / `StudyGraph.load()` 方法
- LangGraph 引擎接收 StudyGraph 作为输入，结构完全兼容

### 结论

**不需要额外开发**。现有基础设施已完整支持：

```
StudyGraph → graph.json → GET /study/{id}/graph → DAGVisualization.tsx
```

LangGraph 引擎的图结构与 graph.json 格式完全一致（同一个 StudyGraph 对象）。前端已能渲染。

## 2. Checkpoints 合并到 studies.db

### 现状

| 数据库 | 路径 | 表 | 生命周期 |
|--------|------|-----|---------|
| `studies.db` | 共享路径 | studies, study_directives, study_rounds, study_interrupts | 长期 |
| `checkpoints.db` | `study/{id}/checkpoints.db` | checkpoints, writes | 短期（可清理） |

### 目标

将 LangGraph checkpoint 表（checkpoints, writes）合并到 studies.db，减少文件数量。

### 方案

#### 2.1 表结构

在 studies.db 中添加两张表（加 `langgraph_` 前缀）：

```sql
CREATE TABLE IF NOT EXISTS langgraph_checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint BLOB,
    metadata BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS langgraph_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    value BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
```

#### 2.2 实现修改

| 文件 | 修改 |
|------|------|
| `store.py` | `_init_db()` 加 checkpoint 表；新增 `get_checkpoint_conn()` |
| `langgraph_engine.py` | `_get_checkpointer()` 接受 connection 参数 |
| `runner.py` | 传递 `study_store._conn` 给 langgraph_engine |

#### 2.3 迁移

- 旧 `checkpoints.db`：自动忽略（checkpoint 是临时状态）
- 新 study：直接用 studies.db 中的 checkpoint 表

### 线程安全

- `SqliteSaver` 使用 `check_same_thread=False`
- 复用 studies.db 的 `threading.Lock`

### 验证

- 现有 72 tests 继续通过
- LangGraph engine 功能不变（checkpoint 从 studies.db 读写）
- 无独立 `checkpoints.db` 文件创建
