# LangGraph 双引擎方案 + 三引擎融合设计

> Date: 2026-08-20
> Scope: 后端引擎层 + per-study 配置 + 前端 + 测试
> Status: Approved

## 1. 背景

当前 study 执行有两套引擎（Phase 引擎 + DAG 引擎），通过 `SR_STUDY_DAG_ENGINE` 环境变量切换。两套引擎各自维护，代码重复，且 DAG 引擎层内串行。

引入 LangGraph 作为第三引擎（最终融合为唯一引擎），获得：
- **真并行**：LangGraph super-step 模型天然支持 fan-out 并行
- **检查点**：SqliteSaver 实现轮内 agent 级断点续跑
- **HITL**：`interrupt()` + `Command(resume=...)` 实现轮次中途审批
- **生态对齐**：LangGraph 是事实标准，社区活跃

## 2. 当前架构

```
_run_one_round_impl (runner.py:825)
  ├─ SR_STUDY_DAG_ENGINE != "1"  → Phase 1/2/3 硬编码
  │    researcher → novelty gate → execution → evaluation → decide
  └─ SR_STUDY_DAG_ENGINE == "1"  → _run_round_via_dag (runner.py:1475)
       AgentExecutor + graph.json topological layers（层内串行）
```

两个引擎返回相同的 `exec_result + eval_result` schema，下游（manifest、budget、review、state.json）无感。

## 3. LangGraph 引擎设计

### 3.1 核心原则

- **直接用 langgraph 包**：`StateGraph`、`SqliteSaver`、`interrupt()`、`Command` — 不造轮子
- **节点函数复用 AgentExecutor**：LangGraph 节点就是普通 Python 函数，内部调用 `AgentExecutor.execute()`，保留自有 LLM client（httpx）
- **引擎边界不变**：返回 legacy schema，下游无感

### 3.2 State 定义

```python
from typing import TypedDict, Annotated

class StudyRoundState(TypedDict):
    # ── 输入 ──
    study_id: str
    round_num: int
    strategy_name: str
    workspace_path: str
    directive_text: str | None
    metric_targets: list[dict]
    # ── 中间状态 ──
    agent_outputs: Annotated[dict[str, Any], merge_agent_outputs]  # 自定义 reducer
    hypothesis: dict | None
    verdict_decision: str | None
    verdict_reason: str | None
    # ── 输出 ──
    exec_result: dict | None
    eval_result: dict | None
    aborted: bool
    abort_reason: str | None
```

`merge_agent_outputs` reducer：并行节点各自写入自己的 agent_id key，合并为完整 dict。

### 3.3 图结构转换

```python
def study_graph_to_langgraph(study_graph: StudyGraph, executor, task_text, context):
    """StudyGraph → LangGraph StateGraph"""
    g = StateGraph(StudyRoundState)

    for node in study_graph.nodes:
        g.add_node(node.id, make_agent_node(executor, node, task_text, context))

    for edge in study_graph.edges:
        g.add_edge(edge.source, edge.target)

    # 多入口：START → 所有无入边的节点
    entry_nodes = find_entry_nodes(study_graph)
    for n in entry_nodes:
        g.add_edge(START, n.id)

    # 多出口：所有无出边的节点 → END
    exit_nodes = find_exit_nodes(study_graph)
    for n in exit_nodes:
        g.add_edge(n.id, END)

    return g
```

### 3.4 节点函数

```python
def make_agent_node(executor, node, task_text, context):
    def agent_node(state: StudyRoundState):
        result = executor.execute(
            plugin, task_text, state["workspace_path"],
            context=context,
            upstream_outputs=get_upstream_outputs(state, node.id),
            node=node,
        )
        # SSE 转发
        emit(state["study_id"], "study_agent_complete", {
            "agent": node.id, "status": result.status, ...
        })
        return {
            "agent_outputs": {node.id: try_parse_json(result.output)},
        }
    return agent_node
```

### 3.5 引擎调用

```python
def _run_round_via_langgraph(self, ...):
    from langgraph.checkpoint.sqlite import SqliteSaver

    graph = study_graph_to_langgraph(study_graph, executor, task_text, context)
    checkpointer = SqliteSaver.sqlite3_checkpoint(
        f"{study_root}/checkpoints.db"
    )
    compiled = graph.compile(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": f"{sid}:r{round_num}"}}
    result = compiled.invoke(initial_state, config=config)

    return rebuild_legacy_schema(result)
```

## 4. 并行冲突分析

### 4.1 LangGraph 层面

| 机制 | 说明 |
|------|------|
| Super-step | 同一 super-step 内节点并行执行 |
| Reducer | 每个 state channel 有独立 reducer，处理并行更新合并 |
| Checkpoint | super-step 边界保存，全部并行节点完成后才保存 |

### 4.2 Agent 文件层面

| fan-out 场景 | 读 | 写 | 冲突？ |
|-------------|---|---|--------|
| researcher → {data_quality, factor_analyst} | researcher output（只读） | 各自 `agents/{id}.json` | **安全** |
| risk_controller → {attribution, anti_overfit} | risk output（只读） | 各自 `agents/{id}.json` | **安全** |

| fan-in 场景 | 读 | 写 | 冲突？ |
|-------------|---|---|--------|
| {dq, fa} → strategist | 两个上游（只读） | `strategist.json` + `results.tsv` + `strategy.py` | **安全**（唯一写者） |

### 4.3 已知风险

- 用户自定义 graph 中两个 strategist 并行改 strategy.py → 需文件锁（MVP 不支持）
- 审计报告标注为已知限制，后续通过 graph 校验禁止冲突写入

## 5. 检查点设计

- **位置**：`study/{id}/checkpoints.db`（SqliteSaver）
- **thread_id**：`f"{study_id}:r{round_num}"`（每轮独立）
- **保存点**：super-step 边界（每个 agent 完成后）
- **恢复**：失败轮从最后一个成功 agent 恢复，跳过已完成的
- **与 state.json 职责分离**：
  - state.json：研究层状态（best_metrics, deviation, budget）
  - checkpoint DB：agent 执行层恢复（哪个 agent 完成了，输出是什么）

## 6. HITL 设计

### 6.1 流程

```
_run_loop (async)
  ├─ await asyncio.to_thread(_run_one_round)
  │    └─ LangGraph engine
  │         └─ interrupt("novelty gate approval")
  │              → 返回 {"paused_for_approval": True, ...}
  ├─ 看到 paused_for_approval
  │    → 标记 study 状态为 AWAITING_APPROVAL
  │    → await _wait_for_approval(round_num)  # 复用 _wait_until_resumed 骨架
  ├─ 收到 approval
  │    → 重新 invoke graph with Command(resume=True)
  │    → 继续后续 phase
  └─ 超时自动 approve（可配置）
```

### 6.2 数据模型

新表 `study_interrupts`：

```sql
CREATE TABLE study_interrupts (
    interrupt_id TEXT PRIMARY KEY,
    study_id     TEXT NOT NULL REFERENCES studies(study_id),
    round_num    INTEGER NOT NULL,
    interrupt_type TEXT NOT NULL,  -- 'novelty_gate', 'custom'
    payload      TEXT,             -- JSON: 问题/详情
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/rejected/expired
    response     TEXT,             -- JSON: 用户响应
    created_at   TEXT NOT NULL,
    responded_at TEXT
);
```

### 6.3 API

```
POST /study/{id}/interrupts/{iid}/respond
  body: {"decision": "approve" | "reject", "payload": {...}}
```

### 6.4 前端

StudyChat 消息流内嵌审批卡片：
- `study_interrupt` SSE 事件 → 追加到消息流
- 卡片显示：问题 + 详情 + [批准] [拒绝] 按钮
- 点击 → 调用 respond API → 更新卡片状态

## 7. Per-study 引擎配置

### 7.1 DB

```sql
ALTER TABLE studies ADD COLUMN engine TEXT NOT NULL DEFAULT 'phases';
-- 值：'phases' | 'dag' | 'langgraph'
```

### 7.2 API

StudyCreate schema 加 `engine` 字段（默认 `phases`）。StudyResponse 包含 `engine`。

### 7.3 前端

创建 study 表单加引擎下拉选择器（高级选项折叠）。

### 7.4 兼容

- `SR_STUDY_DAG_ENGINE=1` 全局变量映射为 `engine='langgraph'`（2026-08 修正：
  原实现映射到 `'dag'`，但 dispatch 只认 `'langgraph'`，该开关曾静默无效。
  注意：生效后需要安装 langgraph extra —— `pip install 'quantnodes-strategy-research[langgraph]'`）
- 新 study 默认 `phases`
- `engine` 字段优先级高于环境变量

## 8. 三引擎融合路线

### P5–P8

| 阶段 | 内容 |
|------|------|
| P5 parity 验证 | 用 langgraph engine 以 `phases` 预设（线性+串行+无 checkpoint）跑全部 study_round tests |
| P6 预设化 | langgraph engine 内部支持 profile 参数；`phases`/`dag` 成为 langgraph 的配置预设，不再走不同代码路径 |
| P7 存量迁移 | 存量 study 底层统一走 langgraph；旧列 `executor_type` 标记废弃 |
| P8 完全融合 | 删除 Phase engine、DAG engine 残留代码；`engine` 仅保留三个预设值 |

### 融合后架构

```
_run_one_round_impl
  └─ LangGraphEngine.run_round(graph, profile)
       profile: {serial, checkpoint, interrupts}
       ├─ phases 预设：线性链 + 串行 + 无 checkpoint
       ├─ dag 预设：StudyGraph + 串行 + 无 checkpoint
       └─ langgraph 预设：StudyGraph + 并行 + checkpoint + HITL
```

## 9. 测试策略

| 测试类型 | 方法 |
|---------|------|
| Parity 测试 | stubbed-LLM 下三引擎输出一致（golden file） |
| Checkpoint 测试 | 注入 agent 失败 → 恢复 → 验证跳过已完成 agent |
| HITL 测试 | interrupt → approve → resume → 验证后续 phase 执行 |
| 并行安全测试 | fan-out agent 并发执行 → 验证文件无损坏 |
| 迁移测试 | 旧 study（phases）→ 升级 → langgraph 引擎跑通 |

## 10. 实施阶段

```
P0  管道（dependency + DB/API/前端 + runner 分支）
P1  LangGraph MVP（串行，parity 测试）
P2  审计 → 并行 fan-out
P3  检查点（SqliteSaver）
P4  HITL（interrupt + approve API + StudyChat 卡片）
P5  parity 验证
P6  预设化 + 删旧引擎
P7  存量迁移
P8  完全融合
```

## 11. 文件变更清单

| 文件 | 操作 | 阶段 |
|------|------|------|
| `pyproject.toml` | 加 langgraph extra | P0 |
| `core/study/store.py` | studies 表加 engine 列 | P0 |
| `api/schemas/study.py` | StudyCreate/Response 加 engine | P0 |
| `api/routers/study.py` | create endpoint 传 engine | P0 |
| `api/routers/study.py` | 新增 interrupt respond endpoint | P4 |
| `core/study/runner.py` | engine 分支 + `_run_round_via_langgraph` | P0–P4 |
| `core/study/langgraph_engine.py` | **新文件**：图转换 + 节点 + 编译 | P1–P4 |
| `stores/study.ts` | 前端 study 类型加 engine | P0 |
| `components/study/` | 创建表单 engine 下拉 | P0 |
| `components/study/dashboard/widgets/StudyChat.tsx` | 审批卡片 | P4 |
| `hooks/useSSE.ts` | study_interrupt 事件处理 | P4 |
