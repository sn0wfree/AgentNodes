# Chat UI 改进计划

## 日期: 2026-07-30

## 背景

调研了 ChatGPT、Claude、Cursor、Windsurf、LibreChat 等主流 AI 聊天界面的工具调用和消息展示方式，
对比我们当前实现，制定了以下改进计划。

## 改动清单

### P1 - 高优先级

| # | 改动 | 文件 | 描述 |
|---|------|------|------|
| 1 | 工具特定图标 | `ToolCallBlock.tsx` | 根据工具名映射不同图标 (Code/Search/Play/Database/File/Edit/Download/BarChart) |
| 2 | 工具结果摘要 | `ToolCallBlock.tsx` | 折叠时显示一行结果摘要（如 "返回 252 条数据"） |
| 3 | 错误重试按钮 | `ToolCallBlock.tsx` | error 状态时显示 Retry 按钮，点击重新发送该工具调用 |
| 4 | Thinking markdown | `ThinkingBlock.tsx` | 展开时用 MarkdownRenderer 渲染，支持代码高亮和格式化 |

### P2 - 中优先级

| # | 改动 | 文件 | 描述 |
|---|------|------|------|
| 5 | 流式文本发光 | `StreamingText.tsx` | streaming 时容器加 subtle glow/shadow 效果 |
| 6 | 工具状态切换动画 | `ToolCallBlock.tsx` | 状态变化时 border-color 带 transition |
| 7 | 结果智能摘要 | `ToolCallBlock.tsx` | 根据工具类型显示不同摘要格式 |

### P3 - 低优先级

| # | 改动 | 文件 | 描述 |
|---|------|------|------|
| 8 | tool_progress 支持 | 多文件 | 后端发送 tool_progress 事件，前端显示工具内部进度步骤 |

## 实现细节

### 1. 工具特定图标映射

```typescript
const TOOL_ICONS: Record<string, typeof Code> = {
  write_file: Code,
  read_file: FileText,
  list_files: FolderOpen,
  edit: Pencil,
  web_search: Search,
  web_fetch: Globe,
  run_backtest: Play,
  get_market_data: BarChart3,
  import_data: Database,
  factor_cross_sectional_analysis: BarChart3,
  factor_quintile_returns: BarChart3,
  factor_ic_decay: BarChart3,
  factor_turnover: BarChart3,
  strategy_compare: GitCompare,
  drawdown_analysis: TrendingDown,
  benchmark_comparison: GitCompare,
  // default: Wrench
}
```

### 2. 工具结果摘要

在 ToolCallBlock 折叠状态下，如果 status=done，显示结果摘要行：

```typescript
function summarizeResult(result: unknown): string {
  if (!result) return ''
  if (typeof result === 'string') {
    // 如果是 JSON 字符串，解析后尝试摘要
    try {
      const parsed = JSON.parse(result)
      return summarizeResult(parsed)
    } catch {
      return result.length > 40 ? result.slice(0, 40) + '…' : result
    }
  }
  if (Array.isArray(result)) return `${result.length} 条数据`
  if (typeof result === 'object') {
    const keys = Object.keys(result)
    if (result.data && Array.isArray(result.data)) return `${result.data.length} 条数据`
    if (result.rows) return `${result.rows.length} 行`
  }
  return ''
}
```

### 3. 错误重试按钮

在 error 状态的 ToolCallBlock header 末尾添加 Retry 按钮：

```tsx
{toolCall.status === 'error' && onRetry && (
  <button onClick={(e) => { e.stopPropagation(); onRetry(toolCall) }}
    className="text-red-400 hover:text-red-300">
    <RefreshCw className="h-3 w-3" />
  </button>
)}
```

需要在 AssistantMessage 中传递 onRetry 回调。

### 4. Thinking markdown 渲染

将 ThinkingBlock 展开内容从 `whitespace-pre-wrap` 改为 `<MarkdownRenderer>`。

### 5. 流式文本发光

StreamingText 容器添加 subtle box-shadow：

```css
.streaming-glow {
  box-shadow: 0 0 20px rgba(var(--primary-400-rgb), 0.08);
}
```

### 6. 工具状态切换动画

ToolCallBlock 的 border-l 添加 `transition-colors duration-300`。

### 7. 结果智能摘要

根据工具名称返回不同格式的摘要：

```typescript
function smartSummary(toolName: string, result: unknown): string {
  switch (toolName) {
    case 'get_market_data': return `${result.data?.length || 0} 条行情数据`
    case 'import_data': return `已导入 ${result.rows_inserted || 0} 行`
    case 'run_backtest': return `Sharpe: ${result.sharpe}, Calmar: ${result.calmar}`
    case 'factor_cross_sectional_analysis': return `IC Mean: ${result.ic_mean}`
    case 'list_files': return `${result.length || 0} 个文件`
    default: return summarizeResult(result)
  }
}
```
