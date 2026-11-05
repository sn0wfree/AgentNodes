# Study Chat 三视图设计文档

## 背景

Study Chat 显示多 Agent 执行轨迹。当前问题：

1. **流式回放不适合已完成轮次**：300-500 条 text_delta 碎片拼接成巨大文本墙
2. **`<think>` 标签泄漏**：LLM 输出的 `<think>` 标签未从 text_delta 中剥离
3. **多次迭代产生断裂文本块**：每个 iter 的 text.started/text.ended 创建独立 text part
4. **内容四重复制**：text_delta → text.ended → llm_response → assistant_message
5. **内部事件未过滤**：loop_start, iter_start, llm_request 等元数据事件未跳过
6. **最终输出无意义**：所有 Agent 都是 "Reached max_iterations=X without a final answer."

## 业界调研

| 产品 | 工具调用 | 思考过程 | 多步执行 |
|------|---------|---------|---------|
| Claude | 可折叠卡片 | 可折叠 thinking 块 | 线性对话流 |
| ChatGPT | 状态横幅+可折叠 | 隐藏 | Jupyter 式单元格 |
| Cursor | 可折叠工具块 | 隐藏 | 可折叠分组+Diff |
| Devin | 时间线节点 | 隐藏 | 完整时间线 |
| LangFuse | 树形层级节点 | 嵌套观察节点 | 树状 DAG + 聚合/展开 |
| Cascade | 内联工具调用 | 计划优先 | Todo 清单+检查点 |

**参考模式**：
- LangFuse Agent Graph（聚合/展开双视图）
- Claude thinking blocks（可折叠，默认收起）
- Cursor tool grouping（连续工具调用合并）
- Devin timeline（Agent 节点时间线）

## 方案设计

### 三种视图模式

| 模式 | 图标 | 说明 | 适用场景 |
|------|------|------|---------|
| **card** | `LayoutGrid` | Agent 卡片式，可折叠详情 | 默认，详细审查 |
| **compact** | `AlignLeft` | 精简消息流，一键展开 | 快速浏览 |
| **timeline** | `GitBranch` | 左侧列表+右侧面板 | 多轮对比 |

### 架构

```
API response (agent_outputs + history)
       │
       ▼
buildAgentTraces()  ← 过滤+聚合+结构化
       │
       ▼
  AgentTrace[]  ← 统一数据模型
       │
  ┌────┼────┐
  ▼    ▼    ▼
Card Compact Timeline  ← 三种渲染器
```

### 数据模型

```typescript
interface AgentTrace {
  agentId: string
  agentName: string
  icon: string
  color: string
  category: string
  status: 'completed' | 'max_iterations' | 'error'
  iterations: number
  maxIterations: number
  toolCalls: ToolCallInfo[]
  thinkingBlocks: ThinkingBlock[]
  finalOutputs: string[]
  errorOutput?: string
  elapsedSeconds?: number
  timestamp: number
}

interface ToolCallInfo {
  id: string
  tool: string
  arguments: Record<string, any>
  result?: any
  status: 'ok' | 'error'
  iteration?: number
}

interface ThinkingBlock {
  text: string
  iteration: number
  collapsed: boolean
}
```

### 事件过滤规则

| 事件类型 | 处理方式 |
|---------|---------|
| `loop_start` | 提取 max_iterations，不渲染 |
| `iter_start` | 记录当前迭代号，不渲染 |
| `iter_end` | 记录 finish_reason，不渲染 |
| `llm_request` | 跳过（元数据） |
| `llm_response` | 跳过（与 text.ended 重复） |
| `loop_end` | 跳过 |
| `loop_final` | 提取 iterations/elapsed_s，不渲染 |
| `thinking_start` | 开始收集 thinking 文本 |
| `thinking_delta` | 累积到当前 thinking 块 |
| `thinking_done` | 结束当前 thinking 块 |
| `thinking_end` | 结束当前 thinking 块 |
| `text.started` | 开始新的文本块 |
| `text_delta` | 剥离 `<think>` 标签后累积到文本块 |
| `text.ended` | 保存为 finalOutput |
| `tool_call` | 创建 ToolCallInfo |
| `tool_result` | 匹配 tool_call，更新状态和结果 |
| `assistant_message` | 作为 errorOutput（max_iterations 消息） |

### `<think>` 标签处理

text_delta 中可能包含 `<think>...</think>` 标签：

```
text_delta: "<think>Let me analyze"
text_delta: " the situation.</think>"
text_delta: "Based on the data..."
```

处理逻辑：
1. 检测 `<think>` 开始 → 创建新的 ThinkingBlock
2. `<think>` 之前的内容 → 累积到文本块
3. `<think>` 和 `</think>` 之间的内容 → 累积到 ThinkingBlock
4. `</think>` 之后的内容 → 累积到文本块
5. 如果 `<think>` 未闭合 → 该 thinking 块标记为未完成

### 文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `components/study/agentTraceTypes.ts` | 类型定义 |
| 新建 | `components/study/buildAgentTraces.ts` | 数据处理函数 |
| 新建 | `components/study/AgentCardView.tsx` | 视图 A：卡片式 |
| 新建 | `components/study/TimelineView.tsx` | 视图 C：时间线 |
| 修改 | `dashboard/widgets/StudyChat.tsx` | 视图切换+集成 |
| 修改 | `public/chat-ui-config.json` | studyChat 配置 |
| 修改 | `components/chat/chatUiConfig.ts` | 配置读取 |

### 视图切换 UI

StudyChat header 右侧添加分段控制器：

```
[Round N]  [回到最新]                    [⊞|☰|⊢]
```

视图模式保存到 `localStorage`，key: `study-chat-view-mode`。

### SSE 流式支持

运行中的 Study 仍使用 SSE 事件实时更新：
- `card` 模式：SSE 事件累积到 AgentTrace 状态，卡片实时更新
- `compact` 模式：SSE 事件直接走现有 Message 流
- `timeline` 模式：同 card 模式，时间线节点实时刷新

### 配置文件扩展

`public/chat-ui-config.json` 添加：

```json
{
  "studyChat": {
    "defaultViewMode": "card",
    "card": {
      "thinkingCollapsed": true,
      "toolCallsCollapsed": true,
      "maxToolResultChars": 500
    },
    "compact": {
      "showExpandButton": true
    },
    "timeline": {
      "defaultSelectedAgent": "first"
    }
  }
}
```
