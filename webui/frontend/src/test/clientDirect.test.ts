// api/client — direct contract test (NO vi.mock of the client itself).
//
// The existing apiClient.test.ts covers request-core semantics (auth
// header, 401 flow, ApiError detail, sse params) but mocks the client
// everywhere else, so none of the ~85 resource endpoints ever executed
// (funcs coverage sat at 11%). This file stubs `fetch` and calls every
// endpoint for real, pinning the exact wire contract: URL (including
// query encoding), HTTP method, and JSON body.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useAuthStore } from '../stores/auth'
import { api } from '../api/client'

interface FakeRes {
  ok?: boolean
  status?: number
  statusText?: string
  json?: () => Promise<unknown>
  text?: () => Promise<string>
  headers?: { get: (k: string) => string | null }
}

const fetchCalls: Array<{ input: string; init: RequestInit }> = []
let queue: FakeRes[] = []

const okRes = (body: unknown): FakeRes => ({
  ok: true,
  status: 200,
  text: async () => JSON.stringify(body),
  json: async () => body,
  headers: { get: () => null },
})

;(globalThis as unknown as { fetch: unknown }).fetch = async (
  input: string,
  init: RequestInit = {}
) => {
  fetchCalls.push({ input, init })
  return queue.shift() ?? okRes({})
}

beforeEach(() => {
  fetchCalls.length = 0
  queue = []
  useAuthStore.setState({ token: null, user: null })
  delete (window as unknown as { location?: unknown }).location
  ;(window as unknown as { location: unknown }).location = { href: '' }
})

// [label, invoke, method, exact url, body expectation]
// body: undefined = don't assert; null = assert request body is absent;
// object = assert JSON.parse(init.body) deep-equals it.
type Row = [
  string,
  () => Promise<unknown>,
  string,
  string,
  Record<string, unknown> | null | undefined,
]

const ROWS: Row[] = [
  // ── goal ──
  ['goal.getStatus', () => api.goal.getStatus('s1'), 'GET', '/api/goal/status?session_id=s1', null],
  ['goal.list', () => api.goal.list({ session_id: 's1', status: 'active', limit: 5 }), 'GET', '/api/goal/list?session_id=s1&status=active&limit=5', null],
  ['goal.list empty', () => api.goal.list(), 'GET', '/api/goal/list', null],
  ['goal.start', () => api.goal.start('s1', 'obj', ['c1']), 'POST', '/api/goal/start', { session_id: 's1', objective: 'obj', criteria: ['c1'] }],
  ['goal.evidence', () => api.goal.evidence('s1', 'ev text', 'c-1'), 'POST', '/api/goal/evidence', { session_id: 's1', evidence: 'ev text', criterion_id: 'c-1' }],
  ['goal.complete', () => api.goal.complete('s1', 'recap'), 'POST', '/api/goal/complete', { session_id: 's1', outcome: 'complete', summary: 'recap' }],
  ['goal.cancel', () => api.goal.cancel('s1'), 'POST', '/api/goal/complete', { session_id: 's1', outcome: 'cancelled' }],

  // ── study ──
  ['study.start', () => api.study.start({ session_id: 's1', objective: 'o', workspace_path: 'w', strategy_name: 'st' } as never), 'POST', '/api/study/start', { session_id: 's1', objective: 'o', workspace_path: 'w', strategy_name: 'st' }],
  ['study.status', () => api.study.status('s1'), 'GET', '/api/study/status?session_id=s1', null],
  ['study.status +studyId', () => api.study.status('s1', 'st-9'), 'GET', '/api/study/status?session_id=s1&study_id=st-9', null],
  ['study.list', () => api.study.list({ session_id: 's1' }), 'GET', '/api/study/list?session_id=s1', null],
  ['study.pause', () => api.study.pause('st-9'), 'POST', '/api/study/st-9/pause', null],
  ['study.resume', () => api.study.resume('st-9'), 'POST', '/api/study/st-9/resume', null],
  ['study.cancel', () => api.study.cancel('st-9'), 'POST', '/api/study/st-9/cancel', null],
  ['study.continue bare', () => api.study.continue('st-9'), 'POST', '/api/study/st-9/continue', {}],
  ['study.continue args', () => api.study.continue('st-9', 'fast', 2), 'POST', '/api/study/st-9/continue', { mode: 'fast', from_round: 2 }],
  ['study.directive', () => api.study.directive('st-9', 'do x', 'planner'), 'POST', '/api/study/st-9/directive', { content: 'do x', issued_by: 'planner' }],
  ['study.summary', () => api.study.summary('st-9'), 'GET', '/api/study/st-9/summary', null],
  ['study.directives', () => api.study.directives('st-9'), 'GET', '/api/study/st-9/directives', null],
  ['study.journal', () => api.study.journal('st-9'), 'GET', '/api/study/st-9/journal', null],
  ['study.todos', () => api.study.todos('st-9'), 'GET', '/api/study/st-9/todos', null],
  ['study.knowledge', () => api.study.knowledge('st-9'), 'GET', '/api/study/st-9/knowledge', null],
  ['study.roundArtifacts', () => api.study.roundArtifacts('st-9', 3), 'GET', '/api/study/st-9/rounds/3/artifacts', null],
  ['study.roundManifest', () => api.study.roundManifest('st-9', 3), 'GET', '/api/study/st-9/rounds/3/manifest', null],
  ['study.roundAgentOutputs bare', () => api.study.roundAgentOutputs('st-9', 3), 'GET', '/api/study/st-9/rounds/3/agent_outputs', null],
  ['study.roundAgentOutputs opts', () => api.study.roundAgentOutputs('st-9', 3, { include_history: true, history_limit: 10 }), 'GET', '/api/study/st-9/rounds/3/agent_outputs?include_history=true&history_limit=10', null],
  ['study.roundDiff', () => api.study.roundDiff('st-9', 3, 2), 'GET', '/api/study/st-9/rounds/3/diff?against=2', null],
  ['study.roundSummaryMd', () => api.study.roundSummaryMd('st-9', 3), 'GET', '/api/study/st-9/rounds/3/summary_md', null],
  ['study.adoptRound', () => api.study.adoptRound('st-9', 3), 'POST', '/api/study/st-9/rounds/3/adopt', null],
  ['study.pendingInterrupt', () => api.study.pendingInterrupt('st-9'), 'GET', '/api/study/st-9/interrupts/pending', null],
  ['study.hangingEvents defaults', () => api.study.hangingEvents('st-9'), 'GET', '/api/study/st-9/hanging_events?hours=24&limit=20', null],
  ['study.hangingEvents args', () => api.study.hangingEvents('st-9', 5, 7), 'GET', '/api/study/st-9/hanging_events?hours=5&limit=7', null],
  ['study.availableActions', () => api.study.availableActions('st-9'), 'GET', '/api/study/st-9/available_actions', null],
  ['study.dispatchAction', () => api.study.dispatchAction('st-9', 'archive', { reason: 'x' }), 'POST', '/api/study/st-9/actions/archive', { reason: 'x' }],
  ['study.dispatchAction bare', () => api.study.dispatchAction('st-9', 'pause'), 'POST', '/api/study/st-9/actions/pause', {}],
  ['study.redoRound', () => api.study.redoRound('st-9', 3), 'POST', '/api/study/st-9/rounds/3/redo', null],
  ['study.replaceObjective', () => api.study.replaceObjective('st-9', 'new obj', 'g-7', 'why'), 'POST', '/api/study/st-9/actions/replace_objective', { new_objective: 'new obj', expected_goal_id: 'g-7', reason: 'why' }],
  ['study.objectiveHistory', () => api.study.objectiveHistory('st-9'), 'GET', '/api/study/st-9/objective_history', null],
  ['study.retry bare', () => api.study.retry('st-9'), 'POST', '/api/study/st-9/actions/retry', {}],
  ['study.retry args', () => api.study.retry('st-9', 2, 'restart'), 'POST', '/api/study/st-9/actions/retry', { from_round: 2, mode: 'restart' }],
  ['study.graph', () => api.study.graph('st-9'), 'GET', '/api/study/st-9/graph', null],
  ['study.updateGraph', () => api.study.updateGraph('st-9', { nodes: [], edges: [] } as never), 'PUT', '/api/study/st-9/graph', { nodes: [], edges: [] }],
  ['study.approveAgentLoop', () => api.study.approveAgentLoop('st-9', 'approved'), 'POST', '/api/study/st-9/agents/approve', { decision: 'approved' }],
  ['study.agents', () => api.study.agents(), 'GET', '/api/study/agents', null],
  ['study.presets', () => api.study.presets(), 'GET', '/api/study/presets', null],
  ['study.planDag', () => api.study.planDag({ session_id: 's1', objective: 'o' } as never), 'POST', '/api/study/plan-dag', { session_id: 's1', objective: 'o' }],

  // ── search ──
  ['search.minimax', () => api.search.minimax('q w', 3), 'GET', '/api/search/minimax?q=q%20w&count=3', null],
  ['search.minimaxHealth', () => api.search.minimaxHealth(), 'GET', '/api/search/minimax/health', null],

  // ── run ──
  ['run.status', () => api.run.status('w dir', 'st/name', 'run 1'), 'GET', '/api/run/status?workspace_path=w%20dir&strategy_name=st%2Fname&run_name=run%201', null],
  ['run.equity default points', () => api.run.equity('w', 'st', 'r'), 'GET', '/api/run/equity?workspace_path=w&strategy_name=st&run_name=r&max_points=2000', null],
  ['run.equity points', () => api.run.equity('w', 'st', 'r', 99), 'GET', '/api/run/equity?workspace_path=w&strategy_name=st&run_name=r&max_points=99', null],
  ['run.list defaults', () => api.run.list('w'), 'GET', '/api/run/list?workspace_path=w&strategy_name=&limit=20', null],
  ['run.list args', () => api.run.list('w', 's', 7), 'GET', '/api/run/list?workspace_path=w&strategy_name=s&limit=7', null],

  // ── strategies ──
  ['strategies.list', () => api.strategies.list('w'), 'GET', '/api/strategies/list?workspace_path=w', null],

  // ── permission ──
  ['permission.respond defaults', () => api.permission.respond({ tool_call_id: 't1', action: 'allow' }), 'POST', '/api/chat/permission/respond', { tool_call_id: 't1', action: 'allow', permanent: false, reason: '' }],
  ['permission.respond full', () => api.permission.respond({ tool_call_id: 't1', action: 'deny', permanent: true, reason: 'r' }), 'POST', '/api/chat/permission/respond', { tool_call_id: 't1', action: 'deny', permanent: true, reason: 'r' }],

  // ── personas ──
  ['personas.list', () => api.personas.list(), 'GET', '/api/chat/personas', null],

  // ── workflow ──
  ['workflow.list', () => api.workflow.list(), 'GET', '/api/goal/workflow/list', null],
  ['workflow.graph', () => api.workflow.graph('my dag'), 'GET', '/api/goal/workflow/my%20dag/graph', null],
  ['workflow.start', () => api.workflow.start('s1', 'wf-1', 'obj'), 'POST', '/api/goal/workflow/start', { session_id: 's1', workflow_name: 'wf-1', objective: 'obj' }],
  ['workflow.status', () => api.workflow.status('g-7'), 'GET', '/api/goal/workflow/status?goal_id=g-7', null],
  ['workflow.pause', () => api.workflow.pause('g-7'), 'POST', '/api/goal/workflow/pause?goal_id=g-7', null],
  ['workflow.resume', () => api.workflow.resume('g-7'), 'POST', '/api/goal/workflow/resume?goal_id=g-7', null],

  // ── definitions ──
  ['definitions.list', () => api.definitions.list(), 'GET', '/api/goal/workflow/definitions', null],
  ['definitions.get', () => api.definitions.get('my dag'), 'GET', '/api/goal/workflow/definitions/my%20dag', null],
  ['definitions.save', () => api.definitions.save({ name: 'd1' } as never), 'POST', '/api/goal/workflow/definitions', { name: 'd1' }],
  ['definitions.remove', () => api.definitions.remove('d1'), 'DELETE', '/api/goal/workflow/definitions/d1', null],
  ['definitions.copy', () => api.definitions.copy('d1'), 'POST', '/api/goal/workflow/definitions/d1/copy', null],
  ['definitions.graph', () => api.definitions.graph('d1'), 'GET', '/api/goal/workflow/definitions/d1/graph', null],

  // ── orchestrate ──
  ['orchestrate.session', () => api.orchestrate.session('dag-1'), 'POST', '/api/goal/workflow/orchestrate/session', { dag_id: 'dag-1' }],
  ['orchestrate.saveDraft', () => api.orchestrate.saveDraft('dag-1', [{ id: 'n1' }], [{ id: 'e1' }]), 'PUT', '/api/goal/workflow/orchestrate/draft', { dag_id: 'dag-1', nodes: [{ id: 'n1' }], edges: [{ id: 'e1' }] }],
  ['orchestrate.getDraft', () => api.orchestrate.getDraft('dag-1'), 'GET', '/api/goal/workflow/orchestrate/draft/dag-1', null],
  ['orchestrate.clearDraft', () => api.orchestrate.clearDraft('dag-1'), 'DELETE', '/api/goal/workflow/orchestrate/draft/dag-1', null],
  ['sendOrchestrate', () => api.sendOrchestrate('s1', 'go'), 'POST', '/api/chat/send_async', { session_id: 's1', content: 'go', agent_id: 'workflow_orchestrator' }],

  // ── chat ──
  ['chat.sendAsync bare', () => api.chat.sendAsync('s1', 'hi'), 'POST', '/api/chat/send_async', { session_id: 's1', content: 'hi' }],
  ['chat.sendAsync opts', () => api.chat.sendAsync('s1', 'hi', { images: ['a'], model: 'm1' }), 'POST', '/api/chat/send_async', { session_id: 's1', content: 'hi', images: ['a'], model: 'm1' }],
  ['chat.send', () => api.chat.send('s1', 'hi'), 'POST', '/api/chat/send', { session_id: 's1', content: 'hi' }],
  ['chat.cancel bare', () => api.chat.cancel('s1'), 'POST', '/api/chat/cancel', { session_id: 's1' }],
  ['chat.cancel attempt', () => api.chat.cancel('s1', 'a-1'), 'POST', '/api/chat/cancel', { session_id: 's1', attempt_id: 'a-1' }],
  ['chat.resumeQueue', () => api.chat.resumeQueue('s1'), 'POST', '/api/chat/queue/resume', { session_id: 's1' }],
  ['chat.attempts', () => api.chat.attempts('s1'), 'GET', '/api/chat/attempts?session_id=s1', null],
  ['chat.personas', () => api.chat.personas(), 'GET', '/api/chat/personas', null],
  ['chat.availableActions', () => api.chat.availableActions('s1'), 'GET', '/api/chat/session/s1/available_actions', null],
  ['chat.export default', () => api.chat.export('s1'), 'GET', '/api/chat/session/s1/export?format=markdown', null],
  ['chat.export json', () => api.chat.export('s1', 'json'), 'GET', '/api/chat/session/s1/export?format=json', null],

  // ── definitionRuns ──
  ['definitionRuns.start', () => api.definitionRuns.start('s1', 'def-1', 'obj', { p: 1 }), 'POST', '/api/goal/workflow/start-definition', { session_id: 's1', definition_name: 'def-1', objective: 'obj', params: { p: 1 } }],
  ['definitionRuns.start bare params', () => api.definitionRuns.start('s1', 'def-1', 'obj'), 'POST', '/api/goal/workflow/start-definition', { session_id: 's1', definition_name: 'def-1', objective: 'obj', params: {} }],
  ['definitionRuns.approve with edits', () => api.definitionRuns.approve('run-3', true, { x: 1 }), 'POST', '/api/goal/workflow/approve', { run_id: 'run-3', approved: true, edits: { x: 1 } }],
  ['definitionRuns.approve bare', () => api.definitionRuns.approve('run-3', false), 'POST', '/api/goal/workflow/approve', { run_id: 'run-3', approved: false }],
  ['definitionRuns.status', () => api.definitionRuns.status('run-3'), 'GET', '/api/goal/workflow/run/run-3/status', null],
  ['definitionRuns.detail', () => api.definitionRuns.detail('run-3'), 'GET', '/api/goal/workflow/run/run-3', null],
  ['definitionRuns.remove', () => api.definitionRuns.remove('run-3'), 'DELETE', '/api/goal/workflow/run/run-3', null],

  // ── adminUsers ──
  ['adminUsers.list', () => api.adminUsers.list({ limit: 10, offset: 0 }), 'GET', '/api/admin/users?limit=10&offset=0', null],
  ['adminUsers.list bare', () => api.adminUsers.list(), 'GET', '/api/admin/users', null],
  ['adminUsers.create', () => api.adminUsers.create({ username: 'u', password: 'p' }), 'POST', '/api/admin/users', { username: 'u', password: 'p' }],
  ['adminUsers.update', () => api.adminUsers.update('u-5', { role: 'admin' }), 'PATCH', '/api/admin/users/u-5', { role: 'admin' }],
  ['adminUsers.resetPassword', () => api.adminUsers.resetPassword('u-5', 'np'), 'POST', '/api/admin/users/u-5/reset-password', { new_password: 'np' }],
  ['adminUsers.disable', () => api.adminUsers.disable('u-5'), 'POST', '/api/admin/users/u-5/disable', null],
  ['adminUsers.enable', () => api.adminUsers.enable('u-5'), 'POST', '/api/admin/users/u-5/enable', null],
  ['adminUsers.data', () => api.adminUsers.data('u-5'), 'GET', '/api/admin/users/u-5/data', null],
]

describe('client wire contract — endpoint sweep', () => {
  for (const [name, run, method, url, body] of ROWS) {
    it(`${method} ${name} → ${url.replace('/api', '')}`, async () => {
      queue = [okRes({ ok: true })]
      await run()
      expect(fetchCalls.length, `exactly one fetch for ${name}`).toBe(1)
      const { input, init } = fetchCalls[0]
      expect(input).toBe(url)
      expect(init.method ?? 'GET').toBe(method)
      if (body === null) {
        expect(init.body, `${name} must send no body`).toBeUndefined()
      } else if (body !== undefined) {
        expect(JSON.parse(init.body as string)).toEqual(body)
      }
    })
  }
})

describe('client summaryWithEtag — conditional request path', () => {
  it('sends if-none-match and returns data + new etag on 200', async () => {
    queue = [{
      ok: true,
      status: 200,
      text: async () => '{"status":"ok"}',
      headers: { get: () => '"v1"' },
    }]
    const { data, etag } = await api.study.summaryWithEtag('st-9', '"v0"')
    expect(data).toEqual({ status: 'ok' })
    expect(etag).toBe('"v1"')
    const headers = fetchCalls[0].init.headers as Record<string, string>
    expect(headers['if-none-match']).toBe('"v0"')
    expect(headers['Authorization']).toBeUndefined()
  })

  it('returns null data + original etag on 304', async () => {
    queue = [{ ok: true, status: 304, headers: { get: () => null } }]
    const { data, etag } = await api.study.summaryWithEtag('st-9', '"v0"')
    expect(data).toBeNull()
    expect(etag).toBe('"v0"')
  })

  it('throws ApiError on non-ok non-304', async () => {
    queue = [{
      ok: false,
      status: 500,
      statusText: 'boom',
      json: async () => ({ detail: 'server error' }),
    }]
    await expect(api.study.summaryWithEtag('st-9')).rejects.toMatchObject({
      name: 'ApiError',
      status: 500,
      message: 'server error',
    })
  })

  it('401 logs out and redirects to /login', async () => {
    const logoutSpy = vi.fn()
    useAuthStore.setState({
      token: 'jwt',
      user: { id: 'u1', username: 'u', display_name: 'U' },
      logout: logoutSpy,
    } as never)
    queue = [{ ok: false, status: 401, json: async () => ({ detail: 'expired' }) }]
    await expect(api.study.summaryWithEtag('st-9')).rejects.toThrow('Unauthorized')
    expect(logoutSpy).toHaveBeenCalled()
    expect(window.location.href).toBe('/login')
  })
})
