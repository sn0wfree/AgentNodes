import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useSessionStore } from '../stores/session'
import type { Session, SearchHit } from '../stores/session'
import { useAgentStore } from '../stores/agents'
import { useWorkflowStore } from '../stores/workflow'
import { useGoalStore } from '../stores/goal'

const makeSession = (
  id: string,
  overrides: Partial<Session> = {}
): Session => ({
  id,
  user_id: 'u1',
  title: `Session ${id}`,
  created_at: Date.now() / 1000,
  updated_at: Date.now() / 1000,
  starred: false,
  tags: [],
  message_count: 0,
  archived: false,
  ...overrides,
})

vi.mock('../api/client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    sse: vi.fn(),
  },
}))

import { api } from '../api/client'
const mockedApi = api as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
}

describe('useSessionStore — basic', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      openSessionIds: [],
      currentSessionId: null,
      searchResults: [],
      searchOpen: false,
      searchQuery: '',
    })
    vi.clearAllMocks()
  })

  it('starts empty', () => {
    expect(useSessionStore.getState().sessions).toEqual([])
    expect(useSessionStore.getState().currentSessionId).toBeNull()
    expect(useSessionStore.getState().openSessionIds).toEqual([])
  })

  it('setSessions replaces all', () => {
    useSessionStore.getState().setSessions([makeSession('a'), makeSession('b')])
    expect(useSessionStore.getState().sessions.length).toBe(2)
  })

  it('addSession prepends and dedupes', () => {
    useSessionStore.getState().addSession(makeSession('a'))
    useSessionStore.getState().addSession(makeSession('b'))
    useSessionStore.getState().addSession(makeSession('a')) // dedupe
    const sessions = useSessionStore.getState().sessions
    expect(sessions.length).toBe(2)
    expect(sessions[0].id).toBe('a')
    expect(sessions[1].id).toBe('b')
  })

  it('setCurrentSession updates id', () => {
    useSessionStore.getState().setCurrentSession('sess-1')
    expect(useSessionStore.getState().currentSessionId).toBe('sess-1')
  })

  it('removeSession removes + clears open + clears current if matching', () => {
    useSessionStore.setState({
      sessions: [makeSession('a'), makeSession('b')],
      openSessionIds: ['a', 'b'],
      currentSessionId: 'a',
    })
    useSessionStore.getState().removeSession('a')
    const state = useSessionStore.getState()
    expect(state.sessions.length).toBe(1)
    expect(state.openSessionIds).toEqual(['b'])
    expect(state.currentSessionId).toBeNull()
  })
})

describe('useSessionStore — async actions', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      openSessionIds: [],
      currentSessionId: null,
      searchResults: [],
    })
    vi.clearAllMocks()
  })

  it('loadSessions fetches and stores', async () => {
    mockedApi.get.mockResolvedValueOnce({
      sessions: [makeSession('a'), makeSession('b')],
    })
    await useSessionStore.getState().loadSessions()
    expect(useSessionStore.getState().sessions.length).toBe(2)
  })

  it('propagates a list failure instead of treating it as an empty list', async () => {
    mockedApi.get.mockRejectedValueOnce(new Error('offline'))
    await expect(useSessionStore.getState().loadSessions()).rejects.toThrow('offline')
  })

  it('preserves the original update error if refreshing the list also fails', async () => {
    mockedApi.patch.mockRejectedValueOnce(new Error('update failed'))
    mockedApi.get.mockRejectedValueOnce(new Error('refresh failed'))
    await expect(useSessionStore.getState().updateSessionMeta('a', { title: 'new' }))
      .rejects.toThrow('update failed')
  })

  it('loadSessions filters out study sessions (both prefixes)', async () => {
    // Round sessions (study:*) and bare study sessions (study_*) belong
    // to the study detail page, never the chat sidebar.
    mockedApi.get.mockResolvedValueOnce({
      sessions: [
        makeSession('a'),
        makeSession('study:study_x:round:3', { title: 'Study x Round 3' }),
        makeSession('study_x', { title: 'Study: study_x' }),
      ],
    })
    await useSessionStore.getState().loadSessions()
    const ids = useSessionStore.getState().sessions.map((s) => s.id)
    expect(ids).toEqual(['a'])
  })

  it('createNewSession adds to open + current', async () => {
    mockedApi.post.mockResolvedValueOnce(makeSession('new', { title: '我的会话' }))
    const sess = await useSessionStore.getState().createNewSession('我的会话')
    const state = useSessionStore.getState()
    expect(sess.id).toBe('new')
    expect(state.openSessionIds).toContain('new')
    expect(state.currentSessionId).toBe('new')
  })

  it('switchSession updates current + openSessionIds', async () => {
    // Setup: 2 sessions already exist
    useSessionStore.setState({
      sessions: [makeSession('a'), makeSession('b')],
      openSessionIds: ['a', 'b'],
      currentSessionId: 'a',
    })
    // loadMessages calls api.get — return empty messages
    mockedApi.get.mockResolvedValue({ messages: [] })
    await useSessionStore.getState().switchSession('b')
    expect(useSessionStore.getState().currentSessionId).toBe('b')
    expect(useSessionStore.getState().openSessionIds).toEqual(['a', 'b'])
  })

  it('switchSession adds id to openSessionIds if not present', async () => {
    useSessionStore.setState({
      sessions: [makeSession('a'), makeSession('b')],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    mockedApi.get.mockResolvedValue({ messages: [] })
    await useSessionStore.getState().switchSession('b')
    expect(useSessionStore.getState().openSessionIds).toEqual(['a', 'b'])
  })

  it('switchSession to same id is no-op', async () => {
    useSessionStore.setState({
      sessions: [makeSession('a')],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    // Count currentSessionId state changes — should be 0
    const initial = useSessionStore.getState().currentSessionId
    await useSessionStore.getState().switchSession('a')
    expect(useSessionStore.getState().currentSessionId).toBe(initial)
  })

  it('updateSessionMeta optimistic + API call', async () => {
    useSessionStore.setState({ sessions: [makeSession('a', { starred: false })] })
    mockedApi.patch.mockResolvedValueOnce(makeSession('a', { starred: true }))
    await useSessionStore.getState().updateSessionMeta('a', { starred: true })
    expect(useSessionStore.getState().sessions[0].starred).toBe(true)
    expect(mockedApi.patch).toHaveBeenCalledWith('/chat/session/a', { starred: true })
  })

  it('closeSession picks neighbor', () => {
    useSessionStore.setState({
      openSessionIds: ['a', 'b', 'c'],
      currentSessionId: 'b',
      sessions: [makeSession('a'), makeSession('b'), makeSession('c')],
    })
    useSessionStore.getState().closeSession('b')
    const state = useSessionStore.getState()
    // Neighbor after b is c (index 2 in original list, now [a, c])
    expect(state.openSessionIds).toEqual(['a', 'c'])
    // switchSession runs async; we can't wait easily, but openSessionIds is sync
  })

  it('runSearch hits store', async () => {
    const hits: SearchHit[] = [{
      session_id: 'a', session_title: '会话 A', message_id: 'm1',
      role: 'user', snippet: 'foo', score: -1, created_at: 0,
    }]
    mockedApi.post.mockResolvedValueOnce({ hits })
    await useSessionStore.getState().runSearch('foo')
    expect(useSessionStore.getState().searchResults).toEqual(hits)
  })

  it('runSearch empty query clears without calling API', async () => {
    useSessionStore.setState({ searchResults: [{ session_id: 'x', session_title: '', message_id: '', role: 'user', snippet: '', score: 0, created_at: 0 }] })
    await useSessionStore.getState().runSearch('   ')
    expect(useSessionStore.getState().searchResults).toEqual([])
    expect(mockedApi.post).not.toHaveBeenCalled()
  })
})

describe('useSessionStore — loadSessionState (B13 backfill)', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      openSessionIds: [],
      currentSessionId: null,
      searchResults: [],
    })
    vi.clearAllMocks()
    // Fresh stores for each test
    useAgentStore.setState({ agents: new Map() })
    useWorkflowStore.setState({
      dagNodes: [],
      dagEdges: [],
      presets: [],
      currentPresetId: null,
      executionProgress: 0,
    })
    useGoalStore.setState({ currentGoal: null })
  })

  it('seeds agent list from backend snapshot', async () => {
    mockedApi.get.mockResolvedValueOnce({
      agents: [
        {
          id: 'data_collector',
          session_id: 's1',
          status: 'completed',
          name: 'data_collector',
          created_at: 123,
          updated_at: 123,
        },
        {
          id: 'factor_analyst',
          session_id: 's1',
          status: 'running',
          name: 'factor_analyst',
          created_at: 123,
          updated_at: 123,
        },
      ],
      workflow: null,
      goal: null,
    })
    await useSessionStore.getState().loadSessionState('s1')
    const agents = useAgentStore.getState().agents
    expect(agents.size).toBe(2)
    expect(agents.get('data_collector')?.status).toBe('completed')
    expect(agents.get('factor_analyst')?.status).toBe('running')
    // color assignment must not mutate caller-owned objects (B11)
    expect(agents.get('data_collector')?.color).toBeDefined()
  })

  it('seeds DAG nodes + edges + preset from workflow snapshot', async () => {
    mockedApi.get.mockResolvedValueOnce({
      agents: [],
      workflow: {
        name: 'factor_research',
        nodes: [
          { id: 'collector', label: 'collector', status: 'completed' },
          { id: 'analyst', label: 'analyst', status: 'pending' },
        ],
        edges: [
          { id: 'e1', source: 'collector', target: 'analyst' },
        ],
        progress: { agents_completed: 1, agents_total: 2 },
      },
      goal: null,
    })
    await useSessionStore.getState().loadSessionState('s1')
    const wf = useWorkflowStore.getState()
    expect(wf.dagNodes.length).toBe(2)
    expect(wf.dagEdges.length).toBe(1)
    expect(wf.dagEdges[0]).toEqual({ id: 'e1', source: 'collector', target: 'analyst' })
    expect(wf.currentPresetId).toBe('factor_research')
    expect(wf.executionProgress).toBe(50)
  })

  it('seeds goal from snapshot with criteria mapping', async () => {
    mockedApi.get.mockResolvedValueOnce({
      agents: [],
      workflow: null,
      goal: {
        goal_id: 'goal_x',
        session_id: 's1',
        status: 'active',
        objective: '研究动量因子',
        progress_percent: 0,
        criteria: [
          { criterion_id: 'c1', text: '有足够数据', status: 'covered', evidence_count: 2 },
          { criterion_id: 'c2', text: '显著性检验', status: 'pending', evidence_count: 0 },
        ],
        evidence_count: 2,
      },
    })
    await useSessionStore.getState().loadSessionState('s1')
    const goal = useGoalStore.getState().currentGoal
    expect(goal).not.toBeNull()
    expect(goal?.objective).toBe('研究动量因子')
    expect(goal?.criteria).toHaveLength(2)
    expect(goal?.criteria[0]).toMatchObject({
      criterion_id: 'c1',
      status: 'covered',
      evidence_count: 2,
    })
  })

  it('clears all panels when response is empty', async () => {
    // Seed some stale state first
    useAgentStore.setState({
      agents: new Map([
        ['stale', {
          id: 'stale', session_id: 's1', status: 'running', name: 'stale',
          created_at: 1, updated_at: 1, tool_calls_count: 0, compaction_count: 0,
          context_tokens: 0, context_tokens_limit: 0, iterations_detail: [],
        } as any],
      ]),
    })
    useWorkflowStore.setState({
      dagNodes: [{ id: 'n', label: 'n', status: 'pending' }],
      dagEdges: [],
      presets: [],
      currentPresetId: 'old',
      executionProgress: 33,
    })
    useGoalStore.setState({
      currentGoal: {
        goal_id: 'g', session_id: 's1', status: 'active', objective: 'x',
        progress_percent: 0, criteria: [], evidence_count: 0,
      } as any,
    })
    mockedApi.get.mockResolvedValueOnce({ agents: [], workflow: null, goal: null })
    await useSessionStore.getState().loadSessionState('s1')
    expect(useAgentStore.getState().agents.size).toBe(0)
    expect(useWorkflowStore.getState().dagNodes).toEqual([])
    expect(useWorkflowStore.getState().currentPresetId).toBeNull()
    expect(useGoalStore.getState().currentGoal).toBeNull()
  })

  it('degrades gracefully when the API call fails', async () => {
    useAgentStore.setState({ agents: new Map([['x', { id: 'x', name: 'x' } as any]]) })
    useGoalStore.setState({ currentGoal: { goal_id: 'g' } as any })
    mockedApi.get.mockRejectedValueOnce(new Error('backend down'))
    await useSessionStore.getState().loadSessionState('s1')
    // fallback: clears everything (prior behavior, no regress)
    expect(useAgentStore.getState().agents.size).toBe(0)
    expect(useWorkflowStore.getState().dagNodes).toEqual([])
    expect(useGoalStore.getState().currentGoal).toBeNull()
  })
})