/**
 * StudyAgentPalette — agent catalog browser + checkbox grid for
 * manually refining a planner-composed study DAG.
 *
 * Fetches /api/study/agents on mount; renders one row per plugin
 * with a checkbox, category badge, description, and keyword chips.
 */
import { useEffect, useState } from 'react'
import { api, type StudyAgentSpec } from '../../api/client'

interface Props {
  selected: Set<string>
  onChange: (next: Set<string>) => void
  required?: string[]
}

export function StudyAgentPalette({ selected, onChange, required = [] }: Props) {
  const [agents, setAgents] = useState<StudyAgentSpec[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api.study
      .agents()
      .then((r) => {
        if (alive) {
          setAgents(r.agents)
          setLoading(false)
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setError(e instanceof Error ? e.message : '加载失败')
          setLoading(false)
        }
      })
    return () => {
      alive = false
    }
  }, [])

  const toggle = (id: string) => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    onChange(next)
  }

  const selectAll = () => onChange(new Set(agents.map((a) => a.id)))
  const clearOptional = () => {
    // "仅必选": keep only required agents. The old loop re-added every
    // SELECTED optional agent — the exact opposite of the label.
    onChange(new Set<string>(required))
  }

  if (loading) {
    return <div className="text-xs text-slate-500">加载 agent catalog…</div>
  }
  if (error) {
    return <div className="text-xs text-rose-500">agent 加载失败: {error}</div>
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-slate-700">
          可用 Agents ({agents.length})
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={selectAll}
            className="text-xs text-sky-600 hover:underline"
          >
            全选
          </button>
          <button
            type="button"
            onClick={clearOptional}
            className="text-xs text-slate-500 hover:underline"
          >
            仅必选
          </button>
        </div>
      </div>
      <div className="grid max-h-72 grid-cols-1 gap-1 overflow-y-auto pr-1">
        {agents.map((a) => {
          const checked = selected.has(a.id)
          const isRequired = required.includes(a.id)
          return (
            <label
              key={a.id}
              className={`flex cursor-pointer items-start gap-2 rounded border px-2 py-1.5 text-xs transition-colors ${
                checked
                  ? 'border-sky-300 bg-sky-50'
                  : 'border-slate-200 bg-white hover:bg-slate-50'
              }`}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggle(a.id)}
                disabled={isRequired}
                className="mt-0.5"
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="font-medium text-slate-800">{a.name}</span>
                  <span className="rounded bg-slate-100 px-1 text-[10px] text-slate-600">
                    {a.category}
                  </span>
                  {isRequired && (
                    <span className="rounded bg-slate-100 px-1 text-[10px] text-slate-700">
                      必选
                    </span>
                  )}
                </div>
                <p className="mt-0.5 line-clamp-2 text-slate-600">{a.description}</p>
                {a.keywords.length > 0 && (
                  <p className="mt-0.5 text-[10px] text-slate-400">
                    {a.keywords.join(' · ')}
                  </p>
                )}
              </div>
            </label>
          )
        })}
      </div>
    </div>
  )
}