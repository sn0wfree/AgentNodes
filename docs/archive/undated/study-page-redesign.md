# Study 页面重构设计文档

> 日期: 2026-08-18
> 状态: 实施中
> 关联: `study-retry-round-agent-loop-fix.md`、`study-archive-objective-flow-redesign.md`

## 1. 背景与目标

Study 详情页（`/study/:studyId`）当前存在四个问题：

1. **Overview 冗余** —— journal.md 直接渲染在概览底部，重复 RoundHistory 信息
2. **DAG 单线条** —— 硬编码线性链（`researcher → data_quality → ...`），无法表达多入口/多出口
3. **节点悬浮简陋** —— AgentNodeDetail 仅渲染纯文本 `<pre>`，未复用 chat 的 part-aware 渲染
4. **聊天记录单 round 截断** —— AgentChatLog 只看当前 round，看不到完整对话

本文档给出三组重构方案：

| 编号 | 需求 | 设计 |
|------|------|------|
| A | Overview 移除 journal | 直接删除（journal 仍保留在 `journal.md` 文件中供 RoundHistory/Drawer 使用）|
| B | DAG 多入口多出口 | 后端定义 `graph.json`（nodes + edges），前端用 dagre 自动布局 |
| C | 节点悬浮 + 群聊复用 | 节点悬浮窗复用 `AssistantMessage`；群聊复用 `MessageList + Composer` |

---

## 2. 状态机扩展（A: 删除 journal）

### 2.1 改动范围

`StudyDetailPage.tsx`：
- 删除 `StudyJournalResponse` 导入
- 删除 `journal` state + `loadJournal` callback
- 删除 Overview 末尾的 journal 渲染块
- 删除 Logs 末尾的 journal 渲染块
- 删除 `api.study.journal` 调用

### 2.2 后端不变

- `/api/study/{id}/journal` 端点保留（RoundDetailDrawer 仍可读）
- `study/{id}/journal.md` 文件保留
- `agent_outputs/*.json` 已包含完整 round 数据，可生成 journal

---

## 3. DAG 多入口多出口（B: 核心改造）

### 3.1 设计决策

| 决策点 | 方案 |
|--------|------|
| 节点定义存储 | 复用 `workflow/definition.py` 模式：内置模板 + 用户可覆盖 + `study/{id}/graph.json` |
| Edges 语义 | DAG + 顺序执行：多入口 = 多个 upstream 都完成才触发下游；同一层多节点并行 |
| 旧 study 迁移 | 一次性脚本扫描所有 study，为缺失 `graph.json` 的补齐硬编码默认图 |

### 3.2 数据模型

```python
@dataclass(frozen=True)
class GraphNode:
    id: str           # "researcher" | "data_quality" | ...
    type: str         # "llm_agent" | "evaluator" | "planner"
    label: str = ""
    config: dict = {}
    enabled: bool = True   # False 时跳过但保留节点

@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    condition: str | None = None  # "skip_if_source_failed" 等

@dataclass(frozen=True)
class StudyGraph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
```

### 3.3 内置模板

```python
DEFAULT_STANDARD_GRAPH = StudyGraph(
    nodes=(
        GraphNode("researcher", "llm_agent", "Researcher"),
        GraphNode("data_quality", "evaluator", "Data Quality"),
        GraphNode("factor_analyst", "llm_agent", "Factor Analyst"),
        GraphNode("strategist", "planner", "Strategist"),
        GraphNode("portfolio_construction", "llm_agent", "Portfolio"),
        GraphNode("risk_controller", "evaluator", "Risk Control"),
        GraphNode("attribution_analyst", "evaluator", "Attribution"),
        GraphNode("anti_overfit_analyst", "evaluator", "Anti-Overfit"),
    ),
    edges=(
        # 多入口：researcher 同时触发 data_quality 和 factor_analyst
        GraphEdge("researcher", "data_quality"),
        GraphEdge("researcher", "factor_analyst"),
        # 多入口：strategist 等两个 upstream
        GraphEdge("data_quality", "strategist"),
        GraphEdge("factor_analyst", "strategist"),
        # 标准串联
        GraphEdge("strategist", "portfolio_construction"),
        GraphEdge("portfolio_construction", "risk_controller"),
        # 多出口：risk_controller 触发 attribution + anti_overfit
        GraphEdge("risk_controller", "attribution_analyst"),
        GraphEdge("risk_controller", "anti_overfit_analyst"),
    ),
)
```

### 3.4 拓扑调度

```python
def topological_layers(graph: StudyGraph) -> list[list[str]]:
    """BFS 分层：返回 [[layer1], [layer2], ...]
    多入口节点分到同一层（其所有 upstream 都在前面的层）。
    """
```

每一层内部节点并行执行（v1: 串行；v2: `asyncio.gather`）。

### 3.5 API

```python
GET /api/study/{id}/graph         → 返回 graph.json 内容（旧 study fallback 默认模板）
PUT /api/study/{id}/graph         → 更新 graph（仅 paused/interrupted 允许）
GET /api/study/{id}/rounds        → 全轮次索引（已有，前端未用）
```

### 3.6 前端

`AgentFlowCanvas.tsx`：
- 删 `AGENT_SEQUENCE` 常量 + `layoutWithWrapping`
- 使用 `layoutWithDagre`（从 `components/workflow/layout.ts` 复用）
- 支持 `selectedRound` prop + 轮次选择器
- 节点按层着色（入口蓝色 / 中间灰色 / 出口绿色）
- 当前执行节点脉冲动画

---

## 4. 节点悬浮 + 群聊复用（C）

### 4.1 数据模型扩展

`agent_outputs/{agent}.json` schema 扩展（`runner.py:929-939`）：

```json
{
  "agent": "strategist",
  "timestamp": "2026-08-18T...",
  "duration_ms": 12345,
  "input": "...",
  "output": "...",
  "parts": [  // 新增：与 chat Message.parts 同构
    {"type": "text", "text": "..."},
    {"type": "thinking", "text": "..."},
    {"type": "tool_call", "name": "read", "args": {...}, "result": "..."},
    {"type": "file_edit", "path": "...", "diff": "..."}
  ]
}
```

向后兼容：旧 `output` 仍可读；前端 fallback 到 `[{type: "text", text: output}]`。

### 4.2 AgentNodeDetail 复用 AssistantMessage

```tsx
import { AssistantMessage } from '../chat/AssistantMessage'

export function AgentNodeDetail({ agentOutput }) {
  const message = agentOutputToMessage(agentOutput)
  return (
    <Modal>
      <AssistantMessage message={message} />
    </Modal>
  )
}

function agentOutputToMessage(output): Message {
  return {
    id: `agent-${output.agent_id}`,
    session_id: `study:{studyId}:r:{selectedRound}`,
    role: 'assistant',
    parts: output.parts ?? [{type: 'text', text: output.output ?? ''}],
    created_at: output.timestamp ? new Date(output.timestamp).getTime() : Date.now(),
    metadata: {agent_id: output.agent_id, duration_ms: output.duration_ms}
  }
}
```

### 4.3 AgentChatLog 群聊模式

```tsx
import { MessageList } from '../chat/MessageList'
import { Composer } from '../chat/Composer'

export function AgentChatLog({studyId, selectedRound}) {
  // 拉取所有 agent outputs → 转 Message[]
  const messages = useMemo(() => {
    const out = await api.study.roundAgentOutputs(studyId, selectedRound)
    const msgs = []
    for (const [agentId, o] of Object.entries(out.agent_outputs)) {
      msgs.push(agentOutputToMessage({...o, agent_id: agentId}))
    }
    return msgs.sort((a, b) => a.created_at - b.created_at)
  }, [studyId, selectedRound])

  // 注入 useChatStore（临时 sessionId = `study:{id}:r:{round}`）
  useEffect(() => {
    messages.forEach(m => useChatStore.getState().addMessage(m))
    return () => messages.forEach(m => useChatStore.getState().removeMessage(m.id))
  }, [messages])

  return (
    <div>
      <RoundPicker ... />
      <MessageList sessionId={`study:${studyId}:r:${selectedRound}`} />
      <Composer sessionId={...} onSend={(text) => api.study.directive(studyId, text, 'webui')} />
    </div>
  )
}
```

### 4.4 多 round 浏览

新增 `selectedRound` prop：
- 默认 = `clampRound(summary.current_round)`（当前 round）
- 用户可选择其他 round
- 选层渲染时调用 `roundAgentOutputs(studyId, selectedRound)`

---

## 5. 实施计划

| 步骤 | 文件 | 内容 |
|------|------|------|
| A1 | `StudyDetailPage.tsx` | 删除 journal 相关 |
| B1 | `core/study/graph.py`（新） | `StudyGraph` / `GraphNode` / `GraphEdge` + 拓扑分层 |
| B2 | `core/study/graph_templates.py`（新） | `DEFAULT_STANDARD_GRAPH` 等模板 |
| B3 | `core/study/bootstrap.py` | `init_study_dir(graph=...)` 写 `graph.json` |
| B4 | `api/routers/study.py` | `GET /graph`, `PUT /graph` |
| B5 | `api/schemas/study.py` | `StudyGraphResponse` |
| B6 | `core/study/runner.py` | 拓扑调度 + node dispatcher |
| B7 | `scripts/migrate_study_graph.py` | 旧 study 一次性补齐 |
| C1 | `client.ts` | `StudyGraphResponse` 类型 + `api.study.graph/updateGraph` |
| C2 | `AgentFlowCanvas.tsx` | dagre 布局 + 轮次选择 |
| C3 | `AgentNodeDetail.tsx` | 复用 AssistantMessage |
| C4 | `AgentChatLog.tsx` | 群聊 + Composer |
| T1 | `test_study_graph.py`（新） | 拓扑 + 验证测试 |
| T2 | `test_runner_topology.py`（新） | 拓扑调度测试 |

**预计时间**：5-6 天

---

## 6. 风险点与缓解

| 风险 | 缓解措施 |
|------|----------|
| Runner 重构是核心改动 | 保留原 `run_researcher_phase` 等函数；新调度作为 layer 包装；分阶段启用 |
| 向后兼容性 | 旧 study 由 `StudyGraph.load` fallback；前端 graph 缺失时用硬编码默认 |
| 多入口并发 | v1 串行；v2 改 `asyncio.gather`（保留灵活性） |
| Graph 修改权限 | 仅 `paused`/`interrupted` 状态可改 |
| Chat store 注入污染 | 临时 sessionId `study:{id}:r:{round}`；卸载时 removeMessage |

---

## 7. 关联文档

- `docs/archive/undated/study-retry-round-agent-loop-fix.md` —— 上轮的 round 编号 + approval gate 修复
- `docs/archive/undated/study-archive-objective-flow-redesign.md` —— Archive / Replace objective
- `docs/guides/agent-tools-reference.md` —— Agent 工具白名单
- `docs/archive/undated/study-ui-improvement.md` —— Study UI 早期设计
- `src/strategy_research/core/workflow/definition.py` —— WorkflowDefinition 模式参考