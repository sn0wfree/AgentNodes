// T1.3 — studyHandlers contract sweep: the 24 handlers not covered by
// studySseHandlers.test.ts. Every handler is exercised for real against
// the actual useStudyStore (payload → store transition + live-event
// message contract). Guards and truncation branches included.

import { describe, it, expect, beforeEach } from 'vitest'
import { useStudyStore } from '../stores/study'
import {
  studyMonitorCheck,
  studyProgress,
  studyRetryQueued,
  studyMonitorCheckFailed,
  studyRoundRejected,
  agentApprovalRequested,
  agentApprovalResponded,
  studyScoreboard,
  studyGoalSnapshot,
  studyBudget,
  studyParseRetry,
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
  studyAwaitingApproval,
  studyInterruptResponded,
} from '../hooks/sse/studyHandlers'

function ctx() {
  return {} as never
}

const store = () => useStudyStore.getState()
const last = () => store().recentEvents[0]
const seedCurrent = (extra: Record<string, unknown> = {}) =>
  store().setCurrent({ status: 'ok', study_id: 's1', ...extra } as never)

beforeEach(() => {
  useStudyStore.getState().reset()
})

describe('study handlers — control/status family', () => {
  it('studyMonitorCheck passes metrics through (patch passthrough)', () => {
    studyMonitorCheck(
      { study_id: 's1', metrics: { calmar: 0.4 }, meets_targets: false },
      ctx(),
    )
    expect(store().current?.last_metrics).toEqual({ calmar: 0.4 })
  })

  it('studyProgress emits coverage live event + patches', () => {
    studyProgress({ study_id: 's1', covered: 2, total: 5, percent: 40 }, ctx())
    expect(last()!.type).toBe('evidence')
    expect(last()!.message).toBe('目标进度: 2/5 (40%)')
    expect((store().current as unknown as Record<string, unknown>).percent).toBe(40)
  })

  it('studyProgress tolerates missing counters', () => {
    studyProgress({ study_id: 's1' }, ctx())
    expect(last()!.message).toBe('目标进度: ?/? (0%)')
  })

  it('studyRetryQueued queues + notes the timeline', () => {
    studyRetryQueued({ study_id: 's1' }, ctx())
    expect(last()!.message).toBe('已重新排队，等待调度执行')
    expect(store().current?.execution_status).toBe('queued')
  })

  it('studyMonitorCheckFailed reports the error, with fallback text', () => {
    studyMonitorCheckFailed({ study_id: 's1', error: 'boom' }, ctx())
    expect(last()!.message).toBe('监控检查失败: boom')
    studyMonitorCheckFailed({ study_id: 's1' }, ctx())
    expect(last()!.message).toBe('监控检查失败: 未知错误')
  })

  it('studyRoundRejected clears the HITL card and notes the reason', () => {
    store().setHitlInterrupt({
      study_id: 's1',
      interrupt_id: 'int-1',
      round: 4,
      hypothesis: 'h',
      message: 'm',
    })
    studyRoundRejected({ study_id: 's1', reason: 'hitl_timeout' }, ctx())
    expect(store().hitlInterrupt).toBeNull()
    expect(last()!.message).toBe('轮次被拒绝/超时: hitl_timeout')
  })

  it('studyRoundRejected falls back to 未知原因', () => {
    studyRoundRejected({ study_id: 's1' }, ctx())
    expect(last()!.message).toBe('轮次被拒绝/超时: 未知原因')
  })

  it('studyEarlyStopped patches early_stopped status + timeline note', () => {
    studyEarlyStopped({ study_id: 's1', reason: 'no_progress', round: 7 }, ctx())
    expect(store().current?.execution_status).toBe('early_stopped')
    expect(last()!.message).toBe('提前停止: no_progress')
    expect(last()!.round).toBe(7)
  })
})

describe('study handlers — agent approval queue (Step C)', () => {
  it('agentApprovalRequested enqueues with documented defaults', () => {
    agentApprovalRequested({ study_id: 's1', role: 'researcher' }, ctx())
    const q = Object.values(store().agentApprovals)
    expect(q).toHaveLength(1)
    const a = q[0]!
    expect(a.study_id).toBe('s1')
    expect(a.role).toBe('researcher')
    expect(a.tool_hash).toBe('')
    expect(a.window).toBe(3)
    expect(a.iteration).toBe(0)
    expect(a.timeout_s).toBe(1800)
    expect(a.on_timeout).toBe('continue')
    expect(a.requested_at).toBeGreaterThan(0)
  })

  it('agentApprovalRequested without study_id is a no-op', () => {
    agentApprovalRequested({ role: 'researcher' }, ctx())
    expect(Object.keys(store().agentApprovals)).toHaveLength(0)
  })

  it('agentApprovalResponded resolves the matching slot', () => {
    agentApprovalRequested({ study_id: 's1', role: 'researcher' }, ctx())
    agentApprovalResponded({ study_id: 's1', role: 'researcher' }, ctx())
    expect(Object.keys(store().agentApprovals)).toHaveLength(0)
  })

  it('agentApprovalResponded without study_id leaves the queue intact', () => {
    agentApprovalRequested({ study_id: 's1' }, ctx())
    agentApprovalResponded({}, ctx())
    expect(Object.keys(store().agentApprovals)).toHaveLength(1)
  })
})

describe('study handlers — real-time projection family (guard: needs current)', () => {
  it.each([
    ['studyScoreboard', studyScoreboard, 'scoreboard', { leverage: 0.9 }],
    ['studyGoalSnapshot', studyGoalSnapshot, 'goal_snapshot', { progress: 40 }],
    ['studyBudget', studyBudget, 'budget', { turns_used: 12 }],
  ] as const)('%s merges into current when present', (_n, h, field, value) => {
    seedCurrent()
    h({ study_id: 's1', [field]: value }, ctx())
    expect((store().current as unknown as Record<string, unknown>)[field]).toEqual(value)
  })

  it.each([
    ['studyScoreboard', studyScoreboard],
    ['studyGoalSnapshot', studyGoalSnapshot],
    ['studyBudget', studyBudget],
  ] as const)('%s is a no-op when current is null', (_n, h) => {
    h({ study_id: 's1', scoreboard: {} }, ctx())
    expect(store().current).toBeNull()
  })
})

describe('study handlers — Phase D live activity', () => {
  it('studyPhase started sets currentPhase with Chinese label', () => {
    studyPhase({ study_id: 's1', round: 2, phase: 'researcher', status: 'started' }, ctx())
    expect(store().currentPhase).toBe('researcher')
    expect(last()!.type).toBe('phase')
    expect(last()!.message).toBe('研究 开始')
    expect(last()!.round).toBe(2)
  })

  it('studyPhase done clears currentPhase', () => {
    studyPhase({ study_id: 's1', phase: 'researcher', status: 'started' }, ctx())
    studyPhase({ study_id: 's1', round: 2, phase: 'researcher', status: 'done' }, ctx())
    expect(store().currentPhase).toBeNull()
    expect(last()!.message).toBe('研究 完成')
  })

  it('studyPhase awaiting_approval clears phase and notes the gate', () => {
    studyPhase({ study_id: 's1', phase: 'novelty_gate', status: 'awaiting_approval' }, ctx())
    expect(store().currentPhase).toBeNull()
    expect(last()!.message).toBe('等待审批: novelty_gate')
  })

  it('studyPhase unknown phase falls back to raw name', () => {
    studyPhase({ study_id: 's1', phase: 'custom_phase', status: 'started' }, ctx())
    expect(last()!.message).toBe('custom_phase 开始')
  })

  it('studyPhase with no status only patches', () => {
    studyPhase({ study_id: 's1', round: 5, phase: 'researcher' }, ctx())
    expect(store().currentPhase).toBeNull()
    expect(store().recentEvents).toHaveLength(0)
    expect(store().current?.current_round).toBe(5)
  })

  it('studyAgentComplete updates node status, label + elapsed, clears agent', () => {
    store().setAgent('researcher')
    studyAgentComplete(
      { study_id: 's1', agent: 'researcher', status: 'completed', elapsed_s: 1.234 },
      ctx(),
    )
    expect(store().nodeStatuses['researcher']).toBe('completed')
    expect(last()!.type).toBe('agent')
    expect(last()!.message).toBe('Researcher 完成 (1.2s)')
    expect(store().currentAgent).toBeNull()
  })

  it('studyAgentComplete failed status renders 失败', () => {
    studyAgentComplete({ study_id: 's1', agent: 'risk_controller', status: 'failed' }, ctx())
    expect(last()!.message).toBe('RiskCtrl 失败')
    expect(store().nodeStatuses['risk_controller']).toBe('failed')
  })

  it('studyAgentComplete without agent is a no-op', () => {
    studyAgentComplete({ study_id: 's1', status: 'completed' }, ctx())
    expect(store().recentEvents).toHaveLength(0)
    expect(store().nodeStatuses).toEqual({})
  })

  it('studyGraphNode updates status; running claims the agent slot', () => {
    studyGraphNode({ study_id: 's1', node_id: 'n-1', status: 'running' }, ctx())
    expect(store().nodeStatuses['n-1']).toBe('running')
    expect(store().currentAgent).toBe('n-1')
    studyGraphNode({ study_id: 's1', node_id: 'n-1', status: 'completed' }, ctx())
    expect(store().nodeStatuses['n-1']).toBe('completed')
  })

  it('studyGraphNode without node_id/status is a no-op', () => {
    studyGraphNode({ study_id: 's1', node_id: 'n-1' }, ctx())
    expect(store().nodeStatuses).toEqual({})
  })

  it('studyKnowledgeCheck emits gap topics; empty list stays silent', () => {
    studyKnowledgeCheck({ study_id: 's1', gap_topics: ['量价', '情绪'], collected: true }, ctx())
    expect(last()!.type).toBe('knowledge')
    expect(last()!.message).toBe('知识检查: 缺口 [量价, 情绪] (已收集)')
    studyKnowledgeCheck({ study_id: 's1', gap_topics: [], collected: false }, ctx())
    expect(store().recentEvents).toHaveLength(1)
  })

  it('studyKnowledgeUpdate emits only for positive counts', () => {
    studyKnowledgeUpdate({ study_id: 's1', entries_added: 3 }, ctx())
    expect(last()!.message).toBe('知识更新: +3 条')
    studyKnowledgeUpdate({ study_id: 's1', entries_added: 0 }, ctx())
    expect(store().recentEvents).toHaveLength(1)
  })

  it('studyReview emits deviation/info-gap + patches round', () => {
    studyReview({ study_id: 's1', round: 4, deviation: 'low', info_gap: true }, ctx())
    expect(last()!.type).toBe('review')
    expect(last()!.message).toBe('审核: 偏差=low, 信息缺口=是')
    expect(store().current?.current_round).toBe(4)
  })

  it('studyEvidence records the evidence id, with fallback', () => {
    studyEvidence({ study_id: 's1', evidence_id: 'ev-1' }, ctx())
    expect(last()!.type).toBe('evidence')
    expect(last()!.message).toBe('证据记录: ev-1')
    studyEvidence({ study_id: 's1' }, ctx())
    expect(last()!.message).toBe('证据记录: 新证据')
  })

  it('studyDirectiveAdded truncates content over 50 chars', () => {
    const long = 'x'.repeat(60)
    studyDirectiveAdded({ study_id: 's1', content: long }, ctx())
    expect(last()!.type).toBe('directive')
    expect(last()!.message).toBe(`新指令: "${'x'.repeat(50)}..."`)
    studyDirectiveAdded({ study_id: 's1', content: '短指令' }, ctx())
    expect(last()!.message).toBe('新指令: "短指令"')
  })

  it('studyObjectiveApplied reports the applied count', () => {
    studyObjectiveApplied({ study_id: 's1', count: 2, round: 3 }, ctx())
    expect(last()!.type).toBe('directive')
    expect(last()!.message).toBe('目标替换已应用 (2 项)')
    expect(last()!.round).toBe(3)
  })

  it('studyTodosUpdated reports update count, defaulting to 0', () => {
    studyTodosUpdated({ study_id: 's1', updates: [{ text: 'a' }, { text: 'b' }] }, ctx())
    expect(last()!.message).toBe('待办更新: 2 项')
    studyTodosUpdated({ study_id: 's1' }, ctx())
    expect(last()!.message).toBe('待办更新: 0 项')
  })
})

describe('study handlers — HITL + parse retry', () => {
  it('studyParseRetry emits a retry event only when agents failed', () => {
    studyParseRetry(
      { study_id: 's1', failed_agents: ['strategist', 'researcher'], attempt: 2, delay_s: 8 },
      ctx(),
    )
    expect(last()!.type).toBe('retry')
    expect(last()!.message).toBe('解析重试: strategist, researcher (第2次, 8s后)')
    studyParseRetry({ study_id: 's1', failed_agents: [] }, ctx())
    expect(store().recentEvents).toHaveLength(1)
  })

  it('studyAwaitingApproval truncates hypothesis to 60 chars + sets status', () => {
    studyAwaitingApproval(
      { study_id: 's1', round: 6, hypothesis: 'h'.repeat(80) },
      ctx(),
    )
    expect(last()!.type).toBe('other')
    expect(last()!.message).toBe(`等待审批: Round 6 — ${'h'.repeat(60)}`)
    expect(store().current?.execution_status).toBe('awaiting_approval')
  })

  it('studyAwaitingApproval falls back to 假设审批 when hypothesis missing', () => {
    studyAwaitingApproval({ study_id: 's1' }, ctx())
    expect(last()!.message).toBe('等待审批: Round ? — 假设审批')
  })

  it('studyInterruptResponded renders approve/reject verdicts', () => {
    studyInterruptResponded({ study_id: 's1', decision: 'approve', interrupt_id: 'int-1' }, ctx())
    expect(last()!.message).toBe('审批通过: int-1')
    studyInterruptResponded({ study_id: 's1', decision: 'reject', interrupt_id: 'int-2' }, ctx())
    expect(last()!.message).toBe('审批拒绝: int-2')
  })
})
