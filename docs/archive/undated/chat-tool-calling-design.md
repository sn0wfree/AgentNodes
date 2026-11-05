# Chat 工具调用设计文档

## 概述

当前 chat 中的 AgentLoop 无法调用工具。本文档描述启用工具调用、修复 SSE 字段对齐、以及新增因子/策略研发工具的完整方案。

## 问题根因

### 阻断点 1：工具被禁用

`service.py:385` 传 `allowed_tools=[]`（空列表）。AgentLoop 的过滤逻辑：

```python
if allowed_tools is not None:      # [] 不是 None → True
    filtered = ToolRegistry()
    for name in allowed_tools:     # 空列表 → 零次迭代
        ...
    self.registry = filtered       # 空 registry
```

结果：LLM 收不到任何工具定义，无法生成 tool_calls。

### 阻断点 2：LLM 没收到工具定义

`loop.py` 的 `_stream_chat`/`_astream_chat` 调用 `self.client.stream(messages)` 时没传 `tools=` 参数。即使 registry 有工具，LLM 也不知道。

### 阻断点 3：max_iterations=1

`service.py:89` 默认 `max_iterations=1`。工具调用需要多轮：

```
轮 1: LLM → "我要调用 read_file" → 执行工具 → 返回结果
轮 2: LLM → 根据工具结果生成最终回答
```

`max_iterations=1` 只跑一轮就停了。

### 阻断点 4：SSE 字段名不匹配

后端 emit `tool_call`/`tool_result` 的字段名与前端 `useSSE.ts` 期望的不一致：

| 字段 | 后端 emit | 前端 expect |
|------|----------|------------|
| 工具调用 ID | `call_id` | `id` |
| 工具名 | `tool` | `name` |
| 结果预览 | `preview` | `result` |
| 状态 | `ok`/`error` | `done`/`error` |

## 架构

### 工具调用 SSE 事件流

```
POST /chat/send_async → service.send_message()
  → emit "message_received"
  → spawn _run_attempt() → AgentLoop.arun()
    → 轮 1:
      → LLM 返回 tool_calls
      → emit "tool_call" {id, name, arguments, call_id}
      → 执行工具
      → emit "tool_result" {id, name, status, result, elapsed_ms, call_id}
    → 轮 2:
      → LLM 看到工具结果，生成最终回答
      → emit "text_delta" / "assistant_message"
    → emit "agent_done"
```

### 工具 registry 结构

```python
ToolRegistry
  ├── ReadFileTool         (read_file, readonly)
  ├── WriteFileTool        (write_file, !readonly)
  ├── RunBacktestTool      (run_backtest, readonly)
  ├── ComputeFactorTool    (compute_factor, readonly)
  ├── FactorAnalysisTool   (factor_analysis, readonly)
  ├── GitDiffTool          (git_diff, readonly)
  ├── ListHistoryTool      (list_history, readonly)
  ├── PatternRecognitionTool (pattern_recognition, readonly)
  ├── ListSkillsTool       (list_skills, readonly)
  ├── LoadSkillTool        (load_skill, readonly)
  ├── OptionsPricingTool   (options_pricing, readonly, no workspace)
  ├── WebSearchTool        (web_search, readonly, no workspace)
  ├── ReadUrlTool          (read_url, readonly, no workspace)
  ├── ReadDocumentTool     (read_document, readonly, no workspace)
  ├── GetMarketDataTool    (get_market_data, readonly, no workspace)
  ├── ListDataSourcesTool  (list_data_sources, readonly, no workspace)
  ├── SearchSymbolTool     (search_symbol, readonly, no workspace)
  ├── FactorCrossSectionalAnalysis  (新增, Phase 3)
  ├── FactorQuintileReturns        (新增, Phase 3)
  ├── FactorICDecay                (新增, Phase 3)
  ├── FactorTurnover               (新增, Phase 3)
  ├── StrategyCompare              (新增, Phase 4)
  ├── DrawdownAnalysis             (新增, Phase 4)
  └── BenchmarkComparison          (新增, Phase 4)
```

### workspace 路径

workspace = 服务器启动目录（CWD）。AgentLoop 在执行工具时自动注入 `workspace` kwarg：

```python
# loop.py:839-842
kwargs = dict(tc.arguments)
if "workspace" not in kwargs and self.workspace is not None:
    kwargs["workspace"] = self.workspace
```

service.py 创建 AgentLoop 时传 `workspace=None`。AgentLoop 会自动将 workspace 传给工具。如果 `workspace=None`，需要 workspace 的工具会报错。

**修复**：service.py 中将 `workspace=None` 改为 `workspace=Path.cwd()`。

## 实施计划

### Phase 1：启用工具调用

| 文件 | 改动 |
|------|------|
| `api/session/service.py:89` | `max_iterations: int = 1` → `= 5` |
| `api/session/service.py:379` | `workspace=None` → `workspace=Path.cwd()` |
| `api/session/service.py:385` | `allowed_tools=[],` → `allowed_tools=None,` |
| `core/agent/loop.py:245` | `self.client.stream(messages)` → 加 `tools=tools` |
| `core/agent/loop.py:331` | `self.client.astream(messages)` → 加 `tools=tools` |
| `core/agent/loop.py:638,642` | `self.client.chat(messages)` → 加 `tools=tools` |
| `cli/tui/session.py:271` | `allowed_tools=[],` → `allowed_tools=None,` |

### Phase 2：修复 SSE 字段对齐

| 文件 | 改动 |
|------|------|
| `core/agent/loop.py:832` | `tool_call` emit 加 `id`、`name` 字段 |
| `core/agent/loop.py:859` | `tool_result` emit 加 `id`、`result`，status 改 `ok`→`done` |

### Phase 3：因子研发工具（新增 4 个）

| 工具 | 功能 | 核心实现 |
|------|------|---------|
| `factor_cross_sectional_analysis` | 截面 IC | 批量计算因子 → 按日期与 forward return 做截面相关 |
| `factor_quintile_returns` | 分层收益 | 按因子值分 N 组 → 计算各组平均收益 |
| `factor_ic_decay` | IC 衰减 | 多 horizon 循环调用截面 IC |
| `factor_turnover` | 换手率 | 计算相邻期间排名的 Spearman 相关 |

### Phase 4：策略分析工具（新增 3 个）

| 工具 | 功能 | 核心实现 |
|------|------|---------|
| `strategy_compare` | 策略对比 | 读取多个 results.tsv → 指标并排表 |
| `drawdown_analysis` | 回撤分析 | 从净值序列计算回撤期间和恢复时间 |
| `benchmark_comparison` | 基准对比 | 策略净值 vs 基准净值 → alpha/beta/IR |
