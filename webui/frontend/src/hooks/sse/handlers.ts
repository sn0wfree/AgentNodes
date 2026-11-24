import type { SSEHandler, SSEEventType } from './types'
import {
  textStarted,
  textDelta,
  textEnded,
  thinkingStart,
  thinkingDelta,
  thinkingDone,
  thinkingEnd,
} from './textHandlers'
import { toolCall, toolResult, toolProgress, assistantMessage, fileEdit, table, chart, image, html } from './messageHandlers'
import { messageReceived, attemptStarted, queuePaused, queueState } from './queueHandlers'
import {
  sessionTotalTokens,
  llmUsage,
  contextBreakdown,
  compact,
  compactCount,
  agentDone,
  errorEvent,
} from './controlHandlers'
import { agentStatus, agentLoop, dagUpdate, progress } from './agentHandlers'
import {
  goalUpdated,
  sessionMetaUpdated,
} from './metaHandlers'
import { useStudyStore } from '../../stores/study'
import {
  studyRound,
  studyCompleted,
  studyFailed,
  studyBudgetLimited,
  studyPaused,
  studyResumed,
  studyRoundRejected,
  studyInterrupted,
  studyCancelled,
  studyProgress,
  studyRetryQueued,
  studyMonitorCheckFailed,
  studyStarted,
  studyQueued,
  studyMonitoringStarted,
  studyDriftDetected,
  studyMonitorCheck,
  agentApprovalRequested,
  agentApprovalResponded,
  studyParseRetry,
  studyScoreboard,
  studyGoalSnapshot,
  studyBudget,
  // Phase D: live activity handlers
  studyPhase,
  studyAgentComplete,
  studyGraphNode,
  studyKnowledgeCheck,
  studyKnowledgeUpdate,
  studyReview,
  studyEvidence,
  studyDirectiveAdded,
  studyObjectiveApplied,
  studyEarlyStopped,
  studyTodosUpdated,
  studyInterruptResponded,
} from './studyHandlers'
import {
  subagentStarted,
  subagentToolCall,
  subagentToolResult,
  subagentTextDelta,
  subagentCompleted,
  subagentFailed,
} from './subagentHandlers'
import { todoUpdated } from './todoHandlers'
import { permissionRequest, permissionResult } from './permissionHandlers'

/**
 * Dispatch table mapping every registered SSE event type to its handler.
 *
 * Contract (pinned by src/test/handlers.test.ts): the keys here are
 * exactly the runtime EVENT_TYPES list — every declared runtime event
 * has a handler, no orphan registrations. file_edit / table / chart /
 * image are registered (Tier B P6) ahead of backend emission; when the
 * AgentLoop starts emitting them the parts land in the assistant
 * message automatically. Four types exist only in the SSEEventType
 * union (agent_text_started / agent_text_ended / agent_llm_usage /
 * agent_llm_response), not in the runtime EVENT_TYPES list — they are
 * dropped before dispatch ever sees them.
 */
export const HANDLERS: Partial<Record<SSEEventType, SSEHandler>> = {
  'text.started': textStarted,
  text_delta: textDelta,
  'text.ended': textEnded,
  thinking_start: thinkingStart,
  thinking_delta: thinkingDelta,
  thinking_done: thinkingDone,
  thinking_end: thinkingEnd,
  tool_call: toolCall,
  tool_result: toolResult,
  tool_progress: toolProgress,
  assistant_message: assistantMessage,
  // Block-part handlers (P6) — register so the dispatcher doesn't
  // drop them silently. Defense-in-depth: the backend does not
  // currently emit these at SSE time (see projector.py:985-994);
  // when emission lands, the assistant message will pick up the
  // part automatically.
  file_edit: fileEdit,
  table,
  chart,
  image,
  html,
  message_received: messageReceived,
  'attempt.started': attemptStarted,
  queue_paused: queuePaused,
  queue_state: queueState,
  session_total_tokens: sessionTotalTokens,
  llm_usage: llmUsage,
  'context.breakdown': contextBreakdown,
  compact,
  compact_count: compactCount,
  agent_done: agentDone,
  error: errorEvent,
  agent_status: agentStatus,
  agent_loop: agentLoop,
  dag_update: dagUpdate,
  progress,
  goal_updated: goalUpdated,
  session_meta_updated: sessionMetaUpdated,
  // Study task system (Phase 5 — StudyScheduler emits study_* via EventStore)
  study_queued: studyQueued,
  study_started: studyStarted,
  study_round: studyRound,
  study_completed: studyCompleted,
  study_failed: studyFailed,
  study_budget_limited: studyBudgetLimited,
  study_paused: studyPaused,
  study_resumed: studyResumed,
  study_round_rejected: studyRoundRejected,
  study_interrupted: studyInterrupted,
  study_cancelled: studyCancelled,
  study_progress: studyProgress,
  study_retry_queued: studyRetryQueued,
  study_monitor_check_failed: studyMonitorCheckFailed,
  study_monitoring_started: studyMonitoringStarted,
  study_monitor_check: studyMonitorCheck,
  study_drift_detected: studyDriftDetected,
  // Real-time scoreboard / goal_snapshot / budget (reduces /summary polling)
  study_scoreboard: studyScoreboard,
  study_goal_snapshot: studyGoalSnapshot,
  study_budget: studyBudget,
  // Agent loop approval gate + parse retry (Step B/C)
  agent_approval_requested: agentApprovalRequested,
  agent_approval_responded: agentApprovalResponded,
  study_parse_retry: studyParseRetry,
  // Phase D: live activity events (runner.py → SSE)
  study_phase: studyPhase,
  study_agent_complete: studyAgentComplete,
  study_graph_node: studyGraphNode,
  study_knowledge_check: studyKnowledgeCheck,
  study_knowledge_update: studyKnowledgeUpdate,
  study_review: studyReview,
  study_evidence: studyEvidence,
  study_directive_added: studyDirectiveAdded,
  study_objective_applied: studyObjectiveApplied,
  study_early_stopped: studyEarlyStopped,
  study_todos_updated: studyTodosUpdated,
  // P4: HITL interrupt handlers
  study_interrupt_responded: studyInterruptResponded,
  // Agent loop events (from langgraph engine on_event adapter).
  // `raw` carries the original SSE payload so StudyChat can render
  // structured agent cards; message stays the one-line timeline text.
  agent_thinking_start: (data: Record<string, unknown>) => {
    useStudyStore.getState().addLiveEvent({
      type: 'other', message: `🧠 ${data.agent || 'agent'} 思考中...`,
      round: data.round as number | undefined,
      raw: { type: 'agent_thinking_start', data },
    })
  },
  agent_thinking_done: (data: Record<string, unknown>) => {
    useStudyStore.getState().addLiveEvent({
      type: 'other', message: `🧠 ${data.agent || 'agent'} 思考完成`,
      round: data.round as number | undefined,
      raw: { type: 'agent_thinking_done', data },
    })
  },
  agent_tool_call: (data: Record<string, unknown>) => {
    useStudyStore.getState().addLiveEvent({
      type: 'other', message: `🔧 ${data.agent || 'agent'} 调用 ${data.tool || data.name || '工具'}`,
      round: data.round as number | undefined,
      raw: { type: 'agent_tool_call', data },
    })
  },
  agent_tool_result: (data: Record<string, unknown>) => {
    useStudyStore.getState().addLiveEvent({
      type: 'other', message: `📋 ${data.agent || 'agent'} 工具返回 ${data.status || 'ok'}`,
      round: data.round as number | undefined,
      raw: { type: 'agent_tool_result', data },
    })
  },
  agent_text_delta: (_data: Record<string, unknown>) => {
    // Text deltas are high-frequency; skip to avoid noise
  },
  agent_assistant_message: (data: Record<string, unknown>) => {
    useStudyStore.getState().addLiveEvent({
      type: 'other', message: `💬 ${data.agent || 'agent'} 输出完成`,
      round: data.round as number | undefined,
      raw: { type: 'agent_assistant_message', data },
    })
  },
  agent_loop_end: (data: Record<string, unknown>) => {
    useStudyStore.getState().addLiveEvent({
      type: 'other', message: `✅ ${data.agent || 'agent'} 完成 (${data.reason || data.finished_reason || ''})`,
      round: data.round as number | undefined,
      raw: { type: 'agent_loop_end', data },
    })
  },
  // Subagent lifecycle
  subagent_started: subagentStarted,
  subagent_tool_call: subagentToolCall,
  subagent_tool_result: subagentToolResult,
  subagent_text_delta: subagentTextDelta,
  subagent_completed: subagentCompleted,
  subagent_failed: subagentFailed,
  // Todo / task tracking
  todo_updated: todoUpdated,
  // Tier 1 A1: permission gate handshake (backend -> frontend).
  permission_request: permissionRequest,
  permission_result: permissionResult,
}