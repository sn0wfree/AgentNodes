/**
 * Chat UI Config — 从配置文件加载聊天界面视觉样式
 *
 * 配置文件: public/chat-ui-config.json
 * 修改该文件即可改变聊天界面的外观，无需改代码。
 */
import chatUiConfigRaw from '../../../public/chat-ui-config.json'

// ── 类型定义 ───────────────────────────────────────────────────

export interface AssistantConfig {
  avatar: {
    icon: string
    size: number
    gradient: string[]
  }
  labels: {
    modelPrefix: string
    modelSeparator: string
    queuedLabel: string
    toolCountFormat: string
    tokenCountFormat: string
  }
  colors: {
    streamingDot: string
    flatLabel: string
    bubbleLabel: string
    timestamp: string
  }
  visibility: {
    showVerifiabilityBadge: boolean
    showStreamingStatus: boolean
  }
}

export interface UserBubbleConfig {
  labels: {
    userLabel: string
    editLabel: string
    resendLabel: string
    cancelLabel: string
  }
  colors: {
    label: string
    timestamp: string
    gradient: string[]
  }
  sizing: {
    maxWidth: string
    borderRadius: string
    padding: { x: number; y: number }
  }
  visibility: {
    allowEdit: boolean
  }
}

export interface ToolCallConfig {
  icons: {
    default: string
    status: Record<string, string>
  }
  colors: {
    running: { border: string; bg: string }
    error: { border: string; bg: string }
    done: { border: string; bg: string }
    pending: { border: string; bg: string }
  }
  labels: {
    dangerousBadge: string
    dangerousTooltip: string
  }
  dangerousTools: string[]
  visibility: {
    showArgsPreview: boolean
    showResultSummary: boolean
    showDuration: boolean
    showCopy: boolean
    showRetry: boolean
    showDangerousBadge: boolean
  }
}

export interface ThinkingConfig {
  icons: {
    header: string
  }
  colors: {
    border: string
    background: string
    headerText: string
    contentText: string
    accent: string
  }
  labels: {
    streamingFormat: string
    doneFormat: string
    charCountSuffix: string
  }
  sizing: {
    maxHeight: number
    charThreshold: number
  }
  visibility: {
    showCopy: boolean
    showCharCount: boolean
  }
}

export interface MessageListConfig {
  labels: {
    roundSeparator: string
    emptyTitle: string
    emptyDescription: string
    loadMore: string
    errorLabel: string
    errorDetail: string
    compactionLabel: string
    scrollToBottom: string
  }
  colors: {
    separatorLine: string
    separatorPill: string
    separatorText: string
  }
  visibility: {
    showDaySeparators: boolean
    showQuickStartChips: boolean
    showContextBar: boolean
    showCompactBanner: boolean
  }
}

export interface PageShellConfig {
  layout: {
    headerHeight: number
    contentMaxWidth: number
    contentPadding: { x: number; y: number }
  }
  labels: {
    themeLightLabel: string
    themeDarkLabel: string
  }
  visibility: {
    showThemeToggle: boolean
  }
}

/** §9 复核批：上下文分段聚合行（后端细拆 → 前端配置聚合）。 */
export interface ContextUsageGroup {
  /** 聚合后的展示 key（如 messages / tool_traffic） */
  key: string
  label: string
  colorVar: string
  /** 聚合哪些后端细粒度段 */
  source: string[]
}

export interface ContextUsageConfig {
  groups: ContextUsageGroup[]
}

export interface ChatUiConfig {
  assistant: AssistantConfig
  userBubble: UserBubbleConfig
  toolCall: ToolCallConfig
  thinking: ThinkingConfig
  messageList: MessageListConfig
  pageShell: PageShellConfig
  contextUsage?: ContextUsageConfig
}

// ── 导出配置 ───────────────────────────────────────────────────

export const chatUiConfig: ChatUiConfig = chatUiConfigRaw as ChatUiConfig

// ── 公共 API ───────────────────────────────────────────────────

/**
 * 获取完整的聊天界面配置
 */
export function getChatConfig(): ChatUiConfig {
  return chatUiConfig
}

/**
 * 获取特定部分的配置
 */
export function getAssistantConfig(): AssistantConfig {
  return chatUiConfig.assistant
}

export function getToolCallConfig(): ToolCallConfig {
  return chatUiConfig.toolCall
}

export function getThinkingConfig(): ThinkingConfig {
  return chatUiConfig.thinking
}

export function getMessageListConfig(): MessageListConfig {
  return chatUiConfig.messageList
}

export function getPageShellConfig(): PageShellConfig {
  return chatUiConfig.pageShell
}

/** 上下文分段聚合配置（§9 复核批：后端细拆 10 段 → 前端配置聚合）。
 *  JSON 缺 contextUsage 段或配置非法时回退代码内默认，保证弹层永远可渲染。 */
export function getContextUsageConfig(): ContextUsageConfig {
  const groups = chatUiConfig.contextUsage?.groups
  if (Array.isArray(groups) && groups.length > 0) return { groups }
  return { groups: DEFAULT_CONTEXT_USAGE_GROUPS }
}

/** 代码内兜底分组（与 chat-ui-config.json 的 contextUsage.groups 保持一致）。 */
export const DEFAULT_CONTEXT_USAGE_GROUPS: ContextUsageGroup[] = [
  { key: 'messages', label: '历史消息', colorVar: '--viz-1', source: ['user_text', 'assistant_text'] },
  { key: 'tool_traffic', label: '工具流量', colorVar: '--viz-8', source: ['tool_calls', 'tool_results'] },
  { key: 'system_prompt', label: '系统提示', colorVar: '--viz-3', source: ['system_prompt'] },
  { key: 'meta_user_context', label: '用户上下文', colorVar: '--viz-5', source: ['meta_user_context'] },
  { key: 'skills', label: '技能', colorVar: '--viz-2', source: ['skills'] },
  { key: 'tool_prompt', label: '工具说明', colorVar: '--viz-4', source: ['tool_prompt'] },
  { key: 'system_tool_schemas', label: '工具 Schema', colorVar: '--viz-6', source: ['system_tool_schemas'] },
  { key: 'mcp_tool_schemas', label: 'MCP Schema', colorVar: '--viz-7', source: ['mcp_tool_schemas'] },
  // 「其他」兜底行：后端新增细段而前端未配置时，未被引用的段归到这里，
  // 保证展示合计 == 后端总量（占比不失真）。
  { key: 'other', label: '其他', colorVar: '--viz-8', source: [] },
]
