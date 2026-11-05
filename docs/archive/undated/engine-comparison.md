# Study 引擎对比分析

> Date: 2026-08-20
> 范围: Phase 引擎 vs DAG 引擎 vs LangGraph 引擎

## 1. 总览

| 特性 | Phase 引擎 | DAG 引擎 | LangGraph 引擎 |
|------|-----------|---------|---------------|
| 执行模型 | 硬编码 3 阶段 | 拓扑分层串行 | StateGraph（串行/并行） |
| 并行 | ❌ 无 | ❌ 层内串行 | ✅ Super-step 自动并行 |
| 检查点 | ❌ 无 | ❌ 无 | ✅ SqliteSaver |
| HITL | ❌ 无 | ❌ 无 | ✅ interrupt() |
| 代码量 | ~80 行 | ~100 行 | ~600 行 |
| 依赖 | 无额外依赖 | 无额外依赖 | langgraph 包 |
| 测试兼容 | ✅ 完全兼容 | ✅ 完全兼容 | ⚠️ 需适配 |

## 2. 详细对比

### 2.1 Phase 引擎（`engine='phases'`）

**架构**：`_run_one_round_impl` 硬编码 3 个阶段调用

```
run_researcher_phase → novelty_gate → run_execution_phase → run_evaluation_phase
```

**优势**：
- 最简单，无外部依赖
- 测试完全兼容（现有 test 直接 stub phase 函数）
- 执行路径确定，易调试

**劣势**：
- 无法并行（researcher → dq + fa 必须串行）
- 无检查点（失败必须整轮重跑）
- 无 HITL（无法中途暂停审批）
- 图结构硬编码，无法自定义

**适用场景**：现有 study、测试、快速实验

### 2.2 DAG 引擎（`engine='dag'`）

**架构**：`AgentExecutor` + 拓扑分层，层内串行

```
layers = topological_layers(graph)
for layer in layers:
    for agent_id in layer:
        executor.execute(plugin, ...)
```

**优势**：
- 支持自定义图结构（graph.json）
- 复用 AgentExecutor（统一 agent 调度）
- 与 Phase 引擎输出格式兼容

**劣势**：
- 层内串行（即使无依赖也串行执行）
- 无检查点
- 无 HITL
- `_session_manager` 属性未定义（已有 bug）

**适用场景**：需要自定义 agent 依赖关系，但不需要并行/检查点

### 2.3 LangGraph 引擎（`engine='langgraph'`）

**架构**：`StudyGraph → StateGraph` 转换 + LangGraph 运行时

```
StateGraph(StudyRoundState)
  .add_node("researcher", agent_node)
  .add_node("data_quality", agent_node)
  .add_edge("researcher", "data_quality")  # 自动并行
  .compile(checkpointer=SqliteSaver)
  .invoke(initial_state)
```

**优势**：
- ✅ **真并行**：LangGraph super-step 自动识别可并行节点
- ✅ **检查点**：SqliteSaver，失败轮从断点恢复
- ✅ **HITL**：interrupt() 暂停执行，等待人工审批
- ✅ **Profile 系统**：phases/dag/langgraph 三种预设
- ✅ **状态管理**：TypedDict + reducer，类型安全
- ✅ **生态对齐**：LangGraph 是事实标准

**劣势**：
- ⚠️ 依赖 langgraph 包（需 `pip install strategy-research[langgraph]`）
- ⚠️ 代码复杂度高（600 行 vs 80/100 行）
- ⚠️ 测试需要适配（不能直接 stub phase 函数）
- ⚠️ DuckDB 并行写需要 mutex（已解决）

**适用场景**：新 study、生产环境、需要高可靠性

## 3. 性能对比

### 3.1 并行执行

**Phase 引擎**：串行执行所有 agent
- researcher → data_quality → factor_analyst → strategist → ... → END
- 总时间 = Σ(agent_i 时间)

**DAG 引擎**：拓扑分层，层内串行
- Layer 0: researcher
- Layer 1: data_quality + factor_analyst（串行！）
- Layer 2: strategist
- ...
- 总时间 = Σ(layer_j 时间) = Σ(max(agent_i 时间 in layer_j))

**LangGraph 引擎**：super-step 自动并行
- Super-step 0: researcher
- Super-step 1: data_quality ‖ factor_analyst（真并行！）
- Super-step 2: strategist
- ...
- 总时间 = Σ(max(agent_i 时间 in super-step_j))

**加速比**（以 DEFAULT_STANDARD_GRAPH 为例）：
- researcher → {dq, fa} fan-out：**~2x 加速**（dq 和 fa 并行）
- risk → {attribution, anti_overfit} fan-out：**~2x 加速**
- 总体：**~1.5-2x 加速**（取决于 agent 执行时间分布）

### 3.2 失败恢复

**Phase 引擎**：失败 → 整轮重跑
- 9 个 agent 全部重跑
- LLM 调用成本 = 9 × 单次调用

**DAG 引擎**：失败 → 整轮重跑
- 同 Phase 引擎

**LangGraph 引擎**：失败 → 从检查点恢复
- 已完成的 agent 输出从 checkpoint 加载
- 只重跑失败的 agent
- LLM 调用成本 = 失败 agent 数 × 单次调用

### 3.3 HITL 审批

**Phase/DAG 引擎**：不支持
- 只能在轮间插入指令（下轮生效）

**LangGraph 引擎**：支持
- researcher 完成后暂停，等待人工审批
- 审批后继续执行，不丢失上下文
- 超时自动继续（可配置）

## 4. 优化建议

### 4.1 高优先级

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **统一引擎入口** | 消除 phase/dag 引擎代码重复 | 中 |
| **DAG 引擎并行** | DAG 引擎层内也支持并行 | 低 |
| **Agent 输出缓存** | data_quality/fa 相同输入时复用 | 中 |
| **检查点清理** | 定期清理旧 checkpoint，避免 DB 膨胀 | 低 |

### 4.2 中优先级

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **渐进式 checkpoint** | 每个 agent 完成后保存（而非 super-step 边界） | 中 |
| **并行 DuckDB 读** | 多 agent 并发读 DuckDB（已安全） | 低 |
| **HITL 超时配置** | 支持 per-study 超时设置 | 低 |
| **SSE 状态快照** | 每个 super-step 完成后发送状态快照 | 中 |

### 4.3 低优先级

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **删除 Phase/DAG 引擎** | 代码简化，但破坏测试兼容 | 高 |
| **LangGraph Streaming** | 用 stream_events 替代 invoke | 中 |
| **多假设并行轮** | Send API 实现 population-based search | 高 |

## 5. 推荐路径

### 短期（1-2 周）

1. **统一引擎入口**：将 phase/dag 引擎的核心逻辑提取到公共函数，langgraph 引擎复用
2. **DAG 引擎并行**：给 DAG 引擎加 `parallel=True` 参数，层内 agent 用 `threading.Thread` 并行
3. **Agent 输出缓存**：在 `AgentExecutor.execute()` 层加 LRU cache，输入 hash 相同时复用

### 中期（1 个月）

4. **渐进式 checkpoint**：每个 agent 完成后保存，支持更细粒度的恢复
5. **HITL 超时配置**：在 studies 表加 `hitl_timeout_seconds` 列
6. **SSE 状态快照**：每个 super-step 完成后发送 `study_state_snapshot` 事件

### 长期（3 个月）

7. **删除旧引擎**：确认 langgraph 引擎稳定后，移除 phase/dag 引擎代码
8. **LangGraph Streaming**：用 `stream_events` 替代 `invoke`，实现实时进度
9. **多假设并行轮**：Send API 实现一轮内并行测试多个假设

## 6. 结论

**LangGraph 引擎是唯一值得长期投入的方向**。它解决了 Phase/DAG 引擎的所有痛点（并行、检查点、HITL），且 LangGraph 是事实标准，社区活跃。

**建议**：
- 新 study 默认使用 `engine='langgraph'`
- 现有 study 保持 `engine='phases'`（向后兼容）
- 逐步将关键 study 迁移到 `engine='langgraph'`
- 最终删除 Phase/DAG 引擎代码（P8 目标）
