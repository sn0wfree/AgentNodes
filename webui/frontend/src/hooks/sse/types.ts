import type { Agent } from '../../stores/agents'
import type { Message } from '../../stores/chat'
import type { Goal } from '../../stores/goal'

/**
 * DAG/workflow node execution status. Matches `DAGNodeData.status`
 * in `components/workflow/DAGNode.tsx`.
 */
export type NodeStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'skipped'

export type SSEEventType =
  | 'text.started'
  | 'text_delta'
  | 'text.ended'
  | 'tool_call'
  | 'tool_result'
  | 'tool_progress'
  | 'thinking_delta'
  | 'thinking_done'
  | 'thinking_start'
  | 'thinking_end'
  | 'file_edit'
  | 'table'
  | 'chart'
  | 'image'
  | 'html'
  | 'agent_status'
  | 'agent_loop'
  | 'agent_done'
  | 'assistant_message'
  | 'dag_update'
  | 'progress'
  | 'message_received'
  | 'error'
  | 'session_meta_updated'
  | 'goal_updated'
  | 'attempt.started'
  | 'queue_paused'
  | 'queue_state'
  | 'llm_usage'
  | 'session_total_tokens'
  | 'context.breakdown' // R-014 H1：7 段上下文构成
  | 'compact'
  | 'compact_count'
  | 'study_queued'
  | 'study_started'
  | 'study_round'
  | 'study_completed'
  | 'study_failed'
  | 'study_budget_limited'
  | 'study_paused'
  | 'study_resumed'
  | 'study_round_rejected'
  | 'study_interrupted'
  | 'study_cancelled'
  | 'study_progress'
  | 'study_retry_queued'
  | 'study_monitor_check_failed'
  | 'study_monitoring_started'
  | 'study_monitor_check'
  | 'study_drift_detected'
  | 'study_scoreboard'
  | 'study_goal_snapshot'
  | 'study_budget'
  | 'agent_approval_requested'
  | 'agent_approval_responded'
  | 'study_parse_retry'
  // Phase D: live activity events (emitted by runner.py)
  | 'study_phase'
  | 'study_agent_complete'
  | 'study_graph_node'
  | 'study_knowledge_check'
  | 'study_knowledge_update'
  | 'study_review'
  | 'study_evidence'
  | 'study_directive_added'
  | 'study_objective_applied'
  | 'study_early_stopped'
  | 'study_todos_updated'
  | 'study_interrupt_responded'
  // Agent loop events (from langgraph engine on_event adapter)
  | 'agent_thinking_start'
  | 'agent_thinking_done'
  | 'agent_tool_call'
  | 'agent_tool_result'
  | 'agent_text_delta'
  | 'agent_text_started'
  | 'agent_text_ended'
  | 'agent_assistant_message'
  | 'agent_loop_end'
  | 'agent_llm_usage'
  | 'agent_llm_response'
  | 'subagent_started'
  | 'subagent_tool_call'
  | 'subagent_tool_result'
  | 'subagent_text_delta'
  | 'subagent_completed'
  | 'subagent_failed'
  | 'todo_updated'
  // Tier 1 A1: permission gate handshake
  | 'permission_request'
  | 'permission_result'

export const EVENT_TYPES: SSEEventType[] = [
  'text.started', 'text_delta', 'text.ended',
  'tool_call', 'tool_result', 'tool_progress',
  'thinking_start', 'thinking_delta', 'thinking_done', 'thinking_end',
  // Block-part events (file_edit / table / chart / image) — handlers
  // are registered (Tier B P6). The backend AgentLoop does not
  // currently emit these at SSE time; when emission lands the
  // parts will land in the assistant message automatically. The
  // matching block components (FileEditBlock etc.) are reachable
  // today via DB-loaded parts.
  'file_edit', 'table', 'chart', 'image', 'html',
  'agent_status', 'agent_loop', 'agent_done', 'assistant_message',
  'dag_update', 'progress', 'message_received', 'error',
  'session_meta_updated',
  'goal_updated',
  'compact',
  'compact_count',
  'llm_usage', 'session_total_tokens',
  'context.breakdown', // R-014 H1
  'attempt.started', 'queue_paused', 'queue_state',
  // Study task system (StudyScheduler → EventStore → SSE)
  'study_queued', 'study_started', 'study_round',
  'study_completed', 'study_failed', 'study_budget_limited',
  'study_paused', 'study_resumed', 'study_round_rejected',
  'study_interrupted', 'study_cancelled',
  'study_progress', 'study_retry_queued', 'study_monitor_check_failed',
  'study_monitoring_started', 'study_monitor_check',
  'study_drift_detected',
  'study_scoreboard', 'study_goal_snapshot', 'study_budget',
  // Agent loop approval gate (Step C) + parse retry (Step B)
  'agent_approval_requested', 'agent_approval_responded',
  'study_parse_retry',
  // Phase D: live activity events
  'study_phase', 'study_agent_complete', 'study_graph_node',
  'study_knowledge_check', 'study_knowledge_update', 'study_review',
  'study_evidence', 'study_directive_added', 'study_objective_applied',
  'study_early_stopped', 'study_todos_updated',
  // P4: HITL interrupt events
  'study_interrupt_responded',
  // Agent-level execution events (langgraph engine live streaming)
  'agent_thinking_start', 'agent_thinking_done',
  'agent_tool_call', 'agent_tool_result',
  'agent_text_delta', 'agent_assistant_message', 'agent_loop_end',
  // Subagent events
  'subagent_started', 'subagent_tool_call', 'subagent_tool_result',
  'subagent_text_delta', 'subagent_completed', 'subagent_failed',
  // Todo / task tracking
  'todo_updated',
  // Tier 1 A1: permission gate (opencode-style)
  'permission_request', 'permission_result',
]

/**
 * SSE handler context — the bundled set of store mutators + session
 * scope the event handlers need.
 *
 * Keeping this in one object (instead of passing ~13 args) makes the
 * handler signature stable and the deps array in useSSE trivially
 * correct (one object identity).
 *
 * The `state.*` accessors are read-through helpers for the lazy-direct
 * lookups the original inline switch performed (e.g. reading the current
 * `messages` Map to check for an existing error bubble). Isolating them
 * here keeps the handler modules free of cross-store imports.
 */
export interface SSEContext {
  sessionId: string | null
  // Chat store mutators
  addMessage: (m: Message) => void
  updateMessage: (id: string, updater: (m: Message) => void) => void
  /** P0-1: single atomic write for text_delta (part text + preview buffer + legacy stream). */
  applyTextDelta: (messageId: string, textId: string, text: string) => void
  setStreamingMessage: (id: string | null) => void
  setStreamingText: (s: string) => void
  appendStreamingText: (s: string) => void
  setQueuePaused: (sessionId: string, paused: boolean) => void
  setQueueLength: (sessionId: string, length: number) => void
  setTokensUsed: (sessionId: string, tokens: number) => void
  setContextBreakdown: (sessionId: string, segments: Record<string, number>) => void
  markTotalTokensSeen: (sessionId: string) => void
  setActiveAttempt: (id: string | null) => void
  setAskedUser: (sessionId: string, asked: boolean) => void
  setLastCompaction: (c: { layer: string; timestamp: number } | null) => void
  // Per-part streaming preview buffer (opencode-style). text_delta
  // and thinking_delta write into this so the first character of a
  // part is visible the instant it arrives. Cleared on terminal
  // events (text.ended / thinking_done / tool_result) by the same
  // handler that closes the part.
  accumulatePartText: (partId: string, delta: string) => void
  clearPartAccum: (partId: string) => void
  // Read-through state helpers (snapshot reads of the chat store)
  state: {
    getMessage: (id: string) => Message | undefined
    getMessages: () => IterableIterator<Message> | Message[]
    isQueuePaused: (sessionId: string) => boolean
    hasSeenTotalTokens: (sessionId: string) => boolean
    getTokensUsed: (sessionId: string) => number
  }
  // Agent store
  updateAgent: (id: string, updater: (a: Agent) => void) => void
  // Workflow store
  updateNodeStatus: (id: string, status: NodeStatus) => void
  setExecutionProgress: (p: number) => void
  // Goal store
  setGoal: (g: Goal | null) => void
  // Toast store
  addToast: (kind: 'error' | 'info' | 'success', msg: string) => void
  /** §9 复核：同一错误（按 message_id/attempt_id 去重）不重复弹 toast。
   *  SSE 断线重连会回放历史事件；历史 error 不应在每次切回时重新弹出。
   *  返回 true 表示「本次确实新增提示」，false 表示已提示过。 */
  addErrorToastOnce: (key: string, msg: string) => boolean
  // Session store meta update (auth-side session list patching)
  patchSessionMeta: (session_id: string, patch: Record<string, unknown>) => void
  // Tier 1 A1: permission handshake. The store holds the most recent
  // pending permission_request so the dialog can render. Frontend
  // posts the verdict to /api/chat/permission/respond.
  setPendingPermission: (req: import('./permissionHandlers').PermissionRequest) => void
  clearPendingPermission: (tool_call_id: string) => void
}

export type SSEHandler = (data: Record<string, unknown>, ctx: SSEContext) => void