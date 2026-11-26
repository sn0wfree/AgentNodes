/**
 * StudyDAGComposer — AI compose tab for study creation.
 *
 * Lets the user enter an objective, click "AI 推荐" to call
 * /api/study/plan-dag, then review the recommended agent list and
 * optionally refine it via the StudyAgentPalette. The resulting
 * graph is returned to the parent for graph.json persistence.
 */
import { useEffect, useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { api, type StudyAgentsResponse, type StudyPlanDagResponse } from '../../api/client'
import { StudyAgentPalette } from './StudyAgentPalette'

interface Props {
  objective: string
  onGraphReady: (graph: {
    nodes: Array<{
      id: string
      type: string
      label: string
      config: Record<string, unknown>
      enabled: boolean
    }>
    edges: Array<{ source: string; target: string }>
  }, selectedAgents: string[]) => void
}

export function StudyDAGComposer({ objective, onGraphReady }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [agents, setAgents] = useState<StudyAgentsResponse | null>(null)
  const [planning, setPlanning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reasoning, setReasoning] = useState('')
  // Edges produced by the AI planner — manual palette edits keep the
  // edges between still-selected agents instead of wiping them.
  const [plannedEdges, setPlannedEdges] = useState<Array<{ source: string; target: string }>>([])

  useEffect(() => {
    api.study
      .agents()
      .then(setAgents)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '加载失败'))
  }, [])

  const handlePlan = async () => {
    if (!objective.trim()) {
      setError('请先填写研究目标')
      return
    }
    setPlanning(true)
    setError(null)
    try {
      const resp: StudyPlanDagResponse = await api.study.planDag({
        objective,
        max_agents: 12,
      })
      const ids = new Set<string>(resp.selected_agents)
      setSelected(ids)
      setReasoning(resp.reasoning)
      const edges: Array<{ source: string; target: string }> = []
      for (const node of resp.graph.nodes) {
        const deps = (
          (resp.dag_config?.dag as Record<string, string[]>) || {}
        )[node.id] || []
        for (const d of deps) edges.push({ source: d, target: node.id })
      }
      setPlannedEdges(edges)
      onGraphReady(
        {
          nodes: resp.graph.nodes.map((n) => ({
            id: n.id,
            type: n.type,
            label: n.label ?? n.id,
            config: n.config ?? {},
            enabled: n.enabled ?? true,
          })),
          edges,
        },
        [...ids],
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'AI 规划失败')
    } finally {
      setPlanning(false)
    }
  }

  const handleManualChange = (next: Set<string>) => {
    setSelected(next)
    const nodes = [...next].map((id) => ({
      id,
      type: 'llm_agent',
      label: agents?.agents.find((a) => a.id === id)?.name || id,
      config: {},
      enabled: true,
    }))
    // Keep AI-planned edges whose endpoints are both still selected —
    // the old code passed edges: [], silently discarding the planner's
    // graph on every checkbox toggle.
    const keptEdges = plannedEdges.filter(
      (e) => next.has(e.source) && next.has(e.target),
    )
    onGraphReady({ nodes, edges: keptEdges }, [...next])
  }

  return (
    <div className="flex flex-col gap-3 rounded border border-slate-200 bg-slate-50/60 p-3">
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-sky-500" />
        <span className="text-sm font-medium text-slate-800">AI 自动编排</span>
      </div>
      <p className="text-xs text-muted">
        基于研究目标自动选择合适的 agent 流水线，生成 DAG 写入 graph.json
      </p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handlePlan}
          disabled={planning || !objective.trim()}
          className="flex items-center gap-1 rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-sky-700 disabled:bg-slate-400"
        >
          {planning ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Sparkles className="h-3 w-3" />
          )}
          AI 推荐
        </button>
        {selected.size > 0 && (
          <span className="text-xs text-muted">
            已选 {selected.size} 个 agent
          </span>
        )}
      </div>
      {error && <p className="text-xs text-rose-500">{error}</p>}
      {reasoning && (
        <p className="rounded bg-white px-2 py-1.5 text-xs text-muted">
          {reasoning}
        </p>
      )}
      {agents && (
        <StudyAgentPalette
          selected={selected}
          onChange={handleManualChange}
          required={agents.required}
        />
      )}
    </div>
  )
}