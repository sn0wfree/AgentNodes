import { create } from 'zustand'

export type AgentStatus = 'pending' | 'running' | 'completed' | 'failed' | 'aborted'

export interface IterationToolCall {
  id: string
  name: string
  status: 'running' | 'done' | 'error'
}

export interface IterationDetail {
  iteration: number
  thought?: string
  tool_calls: IterationToolCall[]
  timestamp: number
}

export interface Agent {
  id: string
  session_id: string
  status: AgentStatus
  name: string
  description?: string
  created_at: number
  updated_at: number
  finished_reason?: 'success' | 'failure' | 'abort' | 'max_iterations'
  tool_calls_count: number
  compaction_count: number
  last_compaction?: { layer: string; timestamp: number }
  context_tokens: number
  context_tokens_limit: number
  iterations_detail: IterationDetail[]
  color?: string
}

interface AgentState {
  agents: Map<string, Agent>
  setAgents: (agents: Agent[]) => void
  updateAgent: (id: string, updater: (agent: Agent) => void) => void
  addAgent: (agent: Agent) => void
}

const AGENT_COLORS = [
  'var(--agent-1)', 'var(--agent-2)', 'var(--agent-3)', 'var(--agent-4)', 'var(--agent-5)',
]

export const useAgentStore = create<AgentState>()((set) => ({
  agents: new Map(),
  setAgents: (agents) =>
    set(() => {
      const map = new Map<string, Agent>()
      agents.forEach((a, i) => {
        // Copy before assigning color: never mutate caller-owned
        // objects (API responses) in place (B11).
        map.set(a.id, { ...a, color: AGENT_COLORS[i % AGENT_COLORS.length] })
      })
      return { agents: map }
    }),
  updateAgent: (id, updater) =>
    set((state) => {
      // TODO(architecture): silently no-ops on a missing id, and
      // nothing backfills the agent list after a page reload — SSE
      // only updates EXISTING entries and the replay buffer holds only
      // recent events, so RightPanel's agent list stays empty until a
      // new run starts. Fix: backfill from GET /api/session/... or a
      // run-scoped agent endpoint on session load.
      const agent = state.agents.get(id)
      if (agent) updater(agent)
      return state
    }),
  addAgent: (agent) =>
    set((state) => {
      const idx = state.agents.size
      state.agents.set(
        agent.id,
        { ...agent, color: AGENT_COLORS[idx % AGENT_COLORS.length] },
      )
      return state
    }),
}))
