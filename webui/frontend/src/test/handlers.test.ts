// hooks/sse/handlers — dispatch registry contract.
//
// Pins four things: (1) runtime EVENT_TYPES and HANDLERS keys are exact
// mirrors (no unhandled runtime event, no orphan registration);
// (2) every named handler is wired by same-source identity (a key swap
// or accidental removal turns red); (3) every value is callable (a bad
// import must not yield undefined); (4) the inline agent-loop handlers
// write the expected live events into the real study store, with
// agent_text_delta staying a deliberate noop.

import { describe, it, expect, beforeEach } from 'vitest'
import { EVENT_TYPES } from '../hooks/sse/types'
import { HANDLERS } from '../hooks/sse/handlers'
import { useStudyStore } from '../stores/study'
import {
  textStarted,
  textDelta,
  textEnded,
  thinkingStart,
  thinkingDelta,
  thinkingDone,
  thinkingEnd,
} from '../hooks/sse/textHandlers'
import {
  toolCall,
  toolResult,
  toolProgress,
  assistantMessage,
  fileEdit,
  table,
  chart,
  image,
  html,
} from '../hooks/sse/messageHandlers'
import {
  messageReceived,
  attemptStarted,
  queuePaused,
  queueState,
} from '../hooks/sse/queueHandlers'
import {
  sessionTotalTokens,
  llmUsage,
  compact,
  compactCount,
  agentDone,
  errorEvent,
} from '../hooks/sse/controlHandlers'
import {
  agentStatus,
  agentLoop,
  dagUpdate,
  progress,
} from '../hooks/sse/agentHandlers'
import { goalUpdated, sessionMetaUpdated } from '../hooks/sse/metaHandlers'
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
} from '../hooks/sse/studyHandlers'
import {
  subagentStarted,
  subagentToolCall,
  subagentToolResult,
  subagentTextDelta,
  subagentCompleted,
  subagentFailed,
} from '../hooks/sse/subagentHandlers'
import { todoUpdated } from '../hooks/sse/todoHandlers'
import {
  permissionRequest,
  permissionResult,
} from '../hooks/sse/permissionHandlers'

// Expected wiring — same-source imports as handlers.ts itself. Keeping
// this map explicit is the point: editing the registry forces editing
// this test, so the change is always conscious.
const EXPECTED: Record<string, unknown> = {
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
  study_scoreboard: studyScoreboard,
  study_goal_snapshot: studyGoalSnapshot,
  study_budget: studyBudget,
  agent_approval_requested: agentApprovalRequested,
  agent_approval_responded: agentApprovalResponded,
  study_parse_retry: studyParseRetry,
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
  study_interrupt_responded: studyInterruptResponded,
  subagent_started: subagentStarted,
  subagent_tool_call: subagentToolCall,
  subagent_tool_result: subagentToolResult,
  subagent_text_delta: subagentTextDelta,
  subagent_completed: subagentCompleted,
  subagent_failed: subagentFailed,
  todo_updated: todoUpdated,
  permission_request: permissionRequest,
  permission_result: permissionResult,
}

describe('HANDLERS — SSE dispatch registry contract', () => {
  it('runtime EVENT_TYPES and HANDLERS keys are exact mirrors', () => {
    const keys = new Set(Object.keys(HANDLERS))
    const unhandled = EVENT_TYPES.filter((t) => !keys.has(t))
    expect(unhandled, 'runtime events without a handler').toEqual([])
    const orphans = Object.keys(HANDLERS).filter((k) => !EVENT_TYPES.includes(k as never))
    expect(orphans, 'registered handlers without a runtime event').toEqual([])
  })

  it('registers every named handler by same-source identity', () => {
    for (const [event, fn] of Object.entries(EXPECTED)) {
      expect(HANDLERS[event], `wiring for ${event}`).toBe(fn)
    }
  })

  it('has no extra registrations beyond expected + documented inline handlers', () => {
    const INLINE_KEYS = [
      'agent_thinking_start',
      'agent_thinking_done',
      'agent_tool_call',
      'agent_tool_result',
      'agent_text_delta',
      'agent_assistant_message',
      'agent_loop_end',
    ]
    for (const k of INLINE_KEYS) {
      expect(typeof HANDLERS[k], `inline handler ${k} must be registered`).toBe(
        'function'
      )
    }
    expect(Object.keys(HANDLERS).length).toBe(
      Object.keys(EXPECTED).length + INLINE_KEYS.length
    )
  })

  it('every value is a function (no undefined from a bad import)', () => {
    for (const [event, fn] of Object.entries(HANDLERS)) {
      expect(typeof fn, `HANDLERS[${event}] must be callable`).toBe('function')
    }
  })

  describe('inline agent-loop handlers → study store live events', () => {
    beforeEach(() => {
      useStudyStore.getState().reset()
    })

    const cases: Array<[string, Record<string, unknown>, string]> = [
      ['agent_thinking_start', { agent: 'planner', round: 3 }, '🧠 planner 思考中...'],
      ['agent_thinking_done', { agent: 'planner', round: 3 }, '🧠 planner 思考完成'],
      ['agent_tool_call', { agent: 'planner', tool: 'bash', round: 3 }, '🔧 planner 调用 bash'],
      ['agent_tool_result', { agent: 'planner', status: 'ok', round: 3 }, '📋 planner 工具返回 ok'],
      ['agent_assistant_message', { agent: 'planner', round: 3 }, '💬 planner 输出完成'],
      ['agent_loop_end', { agent: 'planner', reason: 'done', round: 3 }, '✅ planner 完成 (done)'],
    ]

    for (const [event, payload, message] of cases) {
      it(`${event} appends a live event with agent/round/raw`, () => {
        HANDLERS[event as keyof typeof HANDLERS]!(payload, undefined as never)
        const ev = useStudyStore.getState().recentEvents[0]
        expect(ev, `live event for ${event}`).toBeDefined()
        expect(ev!.message).toBe(message)
        expect(ev!.round).toBe(3)
        expect(ev!.raw).toMatchObject({ type: event })
      })
    }

    it('falls back to generic labels when agent/tool are missing', () => {
      HANDLERS['agent_tool_call']!({}, undefined as never)
      expect(useStudyStore.getState().recentEvents[0]!.message).toBe(
        '🔧 agent 调用 工具'
      )
    })

    it('agent_text_delta is a deliberate noop (high-frequency noise guard)', () => {
      HANDLERS['agent_text_delta']!({ text: 'x' }, undefined as never)
      expect(useStudyStore.getState().recentEvents).toHaveLength(0)
    })
  })
})
