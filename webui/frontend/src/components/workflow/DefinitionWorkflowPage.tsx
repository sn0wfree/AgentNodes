import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Play, Plus, Save, RefreshCw, Loader2, FileJson,
  ChevronDown, ChevronUp, History, FileClock, ArrowLeft,
} from 'lucide-react'
import { api, type DefinitionListItem, type DefinitionNode, type DefinitionEdge, type DefinitionRunSnapshot, type DefinitionNodeOutput, type DefinitionPayload } from '../../api/client'
import { useSessionStore } from '../../stores/session'
import { WorkflowEditor } from './WorkflowEditor'
import { WorkflowDAG } from './WorkflowDAG'
import type { DAGNodeData } from './DAGNode'
import { ApprovalDialog } from './ApprovalDialog'
import { ImportDefinitionDialog } from './ImportDefinitionDialog'
import { EmptyState } from '../common/EmptyState'

type RunStatus = 'pending' | 'running' | 'awaiting' | 'completed' | 'failed' | 'cancelled'

const RUN_STATUS_LABELS: Record<RunStatus, { text: string; cls: string }> = {
  pending: { text: '等待中', cls: 'badge-neutral' },
  running: { text: '运行中', cls: 'badge-running' },
  awaiting: { text: '等待审批', cls: 'badge-awaiting' },
  completed: { text: '已完成', cls: 'badge-completed' },
  failed: { text: '失败', cls: 'badge-failed' },
  cancelled: { text: '已取消', cls: 'badge-neutral' },
}

const GOAL_STATUS_LABELS: Record<string, string> = {
  active: '进行中',
  complete: '已完成',
  cancelled: '已取消',
  in_review: '审核中',
}

interface HistoryGoal {
  goal_id: string
  workflow_id: string
  objective: string
  goal_status: string
  created_at: string
}

function mapAgentStatus(s: string): DAGNodeData['status'] {
  switch (s) {
    case 'success': return 'completed'
    case 'failed': return 'failed'
    case 'skipped': return 'skipped'
    case 'running': return 'running'
    default: return 'pending'
  }
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    const pad = (n: number) => n.toString().padStart(2, '0')
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return '—'
  }
}

// Legacy engine agents have functional ids (researcher, backtest…), no
// node types — map id patterns to the palette type colors so playback
// nodes aren't flat grey.
const LEGACY_AGENT_COLOR_HINTS: Array<[RegExp, string]> = [
  [/planner|plan/i, '#a78bfa'],
  [/evaluat|risk|controller|check|audit|decide|quality|review/i, '#34d399'],
  [/backtest|report|data|market|scan|stress|benchmark|factor|attribution/i, '#fb923c'],
  [/researcher|analyst|strategist|writer|construction|scanner|regime/i, '#38bdf8'],
]

function legacyAgentColor(id: string): string | undefined {
  const hit = LEGACY_AGENT_COLOR_HINTS.find(([re]) => re.test(id))
  return hit?.[1]
}

// ── Dify-style orchestration editor body ──────────────────────
// Top-left workflow info bar (name dropdown + actions), node
// palette on the left, canvas in the middle, node config on the
// right, run drawer (with goal history playback) at the bottom.

export function DefinitionWorkflowPage() {
  const sessionId = useSessionStore((s) => s.currentSessionId)

  const [definitions, setDefinitions] = useState<DefinitionListItem[]>([])
  const [editing, setEditing] = useState<{ name: string | null; nodes: DefinitionNode[]; edges: DefinitionEdge[] } | null>(null)
  const [editingName, setEditingName] = useState('')
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState('')
  const [error, setError] = useState('')

  const [runOpen, setRunOpen] = useState(false)
  const [objective, setObjective] = useState('')
  const [starting, setStarting] = useState(false)
  const [run, setRun] = useState<DefinitionRunSnapshot | null>(null)
  const [nodeOutputs, setNodeOutputs] = useState<DefinitionNodeOutput[]>([])
  const [runStatus, setRunStatus] = useState<RunStatus>('pending')
  const [approvalOpen, setApprovalOpen] = useState(false)
  const [approvalBusy, setApprovalBusy] = useState(false)
  const [approvalPreview, setApprovalPreview] = useState('')
  const [importOpen, setImportOpen] = useState(false)
  const [importBusy, setImportBusy] = useState(false)
  const sseRef = useRef<EventSource | null>(null)
  const saveRef = useRef<(() => void) | null>(null)

  // ── Run history (goal playback) ──────────────────────────────
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<HistoryGoal[]>([])
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [viewingGoal, setViewingGoal] = useState<HistoryGoal | null>(null)
  const [viewingGraph, setViewingGraph] = useState<{ nodes: DAGNodeData[]; edges: Array<{ source: string; target: string }> } | null>(null)
  const [viewingStatus, setViewingStatus] = useState<'idle' | 'running' | 'paused' | 'completed' | 'failed'>('idle')
  const [viewingProgress, setViewingProgress] = useState(0)
  const viewingSseRef = useRef<EventSource | null>(null)

  const loadDefinitions = useCallback(async () => {
    try {
      const r = await api.definitions.list()
      setDefinitions(r.definitions ?? [])
    } catch (err) {
      setError((err as Error).message)
    }
  }, [])

  const loadHistory = useCallback(async () => {
    setLoadingHistory(true)
    try {
      const r = await api.goal.list({ limit: 100 })
      setHistory(
        (r.goals ?? [])
          .filter((g) => Boolean(g.workflow_id))
          .map((g) => ({
            goal_id: g.goal_id,
            workflow_id: g.workflow_id as string,
            objective: g.objective,
            goal_status: g.goal_status,
            created_at: g.created_at,
          })),
      )
    } catch {
      // Non-critical — history list can be absent
    } finally {
      setLoadingHistory(false)
    }
  }, [])

  useEffect(() => {
    void loadDefinitions()
    void loadHistory()
  }, [loadDefinitions, loadHistory])

  useEffect(() => () => {
    sseRef.current?.close()
    viewingSseRef.current?.close()
  }, [])

  const closeSSE = useCallback(() => {
    sseRef.current?.close()
    sseRef.current = null
  }, [])

  // ── Editing ─────────────────────────────────────────────────

  const startEdit = async (name: string) => {
    closeViewing()
    setError('')
    try {
      const r = await api.definitions.get(name)
      const d = r.definition
      setEditing({
        name: d.name,
        nodes: (d.nodes ?? []) as DefinitionNode[],
        edges: (d.edges ?? []) as DefinitionEdge[],
      })
      setEditingName(d.name)
      setSavedAt('')
      // Restore the auto-saved draft (uncommitted editor state) if any —
      // the orchestrator chat persisted it step by step.
      try {
        const draft = await api.orchestrate.getDraft(d.name)
        if (draft.dag) {
          setEditing({
            name: d.name,
            nodes: (draft.dag.nodes as DefinitionNode[]) ?? [],
            edges: (draft.dag.edges as DefinitionEdge[]) ?? [],
          })
          setSavedAt('（已恢复未保存的编辑）')
        }
      } catch {
        // draft restore is best-effort
      }
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const startNew = () => {
    closeViewing()
    setEditing({ name: null, nodes: [], edges: [] })
    setEditingName('')
    setSavedAt('')
  }

  const saveDefinition = async (nodes: DefinitionNode[], edges: DefinitionEdge[]) => {
    if (!editing) return
    setSaving(true)
    setError('')
    try {
      const name = editing.name ?? editingName.trim()
      if (!name) {
        setError('请输入定义名称')
        return
      }
      await api.definitions.save({ name, description: '', nodes, edges })
      await loadDefinitions()
      setEditing((prev) => (prev ? { ...prev, name } : prev))
      setEditingName(name)
      setSavedAt(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
      // Explicit save makes the draft obsolete — clear it (including the
      // uncommitted new:{name} draft key when renaming).
      try {
        await api.orchestrate.clearDraft(name)
        if (name !== editingName.trim()) {
          await api.orchestrate.clearDraft(`new-${editingName.trim()}`)
        }
      } catch {
        // best-effort
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  // ── JSON import ────────────────────────────────────────────

  const importToCanvas = (payload: DefinitionPayload) => {
    closeSSE()
    resetRun()
    setError('')
    setEditing({
      name: payload.name || null,
      nodes: (payload.nodes ?? []) as DefinitionNode[],
      edges: (payload.edges ?? []) as DefinitionEdge[],
    })
    setEditingName(payload.name || '')
    setSavedAt('')
    setImportOpen(false)
  }

  const importAndSave = async (payload: DefinitionPayload) => {
    if (!payload.name) {
      setError('导入保存需要 name 字段')
      return
    }
    setImportBusy(true)
    setError('')
    try {
      await api.definitions.save({
        name: payload.name,
        description: payload.description,
        version: payload.version,
        budget: payload.budget,
        llm: payload.llm,
        params: payload.params,
        nodes: payload.nodes,
        edges: payload.edges,
      })
      setImportOpen(false)
      await loadDefinitions()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setImportBusy(false)
    }
  }

  // ── Running ─────────────────────────────────────────────────

  const startRun = async () => {
    if (!editing?.name || !objective.trim() || !sessionId) return
    setStarting(true)
    setError('')
    closeSSE()
    try {
      const r = await api.definitionRuns.start(sessionId, editing.name, objective.trim())
      setRunOpen(true)
      applyRun(r.run_id, r.run)
      connectRunSSE(r.run_id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setStarting(false)
    }
  }

  const applyRun = (runId: string, snap: DefinitionRunSnapshot) => {
    setRun(snap)
    setRunStatus(snap.status as RunStatus)
    setNodeOutputs([])
    void api.definitionRuns.detail(runId).then((d) => {
      setNodeOutputs(d.node_outputs ?? [])
    }).catch(() => {})
  }

  const connectRunSSE = (runId: string) => {
    closeSSE()
    const source = api.sse(`/goal/workflow/run/${encodeURIComponent(runId)}/events`)
    sseRef.current = source

    const refresh = () => {
      void api.definitionRuns.status(runId).then((r) => {
        const snap = r.run as DefinitionRunSnapshot
        setRun(snap)
        setRunStatus(snap.status as RunStatus)
        if (snap.status === 'awaiting') {
          setApprovalPreview(`完成节点：${snap.completed_nodes.join('、') || '—'}`)
          setApprovalOpen(true)
        } else {
          setApprovalOpen(false)
        }
      }).catch(() => {})
      void api.definitionRuns.detail(runId).then((d) => {
        setNodeOutputs(d.node_outputs ?? [])
      }).catch(() => {})
    }

    source.addEventListener('awaiting_approval', () => refresh())
    source.addEventListener('node_completed', () => refresh())
    source.addEventListener('plan_created', (e) => {
      try {
        const d = JSON.parse((e as MessageEvent).data) as { steps?: string[] }
        setApprovalPreview(`计划步骤：${d.steps?.join(' → ') || '—'}`)
      } catch { /* ignore */ }
    })
    source.addEventListener('run_completed', () => refresh())
    source.addEventListener('run_failed', () => refresh())
    source.addEventListener('run_terminal', () => {
      closeSSE()
      refresh()
    })
    source.onerror = () => refresh()
  }

  const respondApproval = async (approved: boolean, edits?: string) => {
    if (!run) return
    setApprovalBusy(true)
    setError('')
    try {
      const r = await api.definitionRuns.approve(run.run_id, approved, edits ? { note: edits } : undefined)
      applyRun(run.run_id, r.run as DefinitionRunSnapshot)
      if ((r.run as DefinitionRunSnapshot).status === 'awaiting') {
        setApprovalPreview('已响应，等待下一轮审批')
      } else {
        setApprovalOpen(false)
      }
    } catch (err) {
      setError((err as Error).message)
      setApprovalOpen(false)
    } finally {
      setApprovalBusy(false)
    }
  }

  const resetRun = () => {
    closeSSE()
    setRun(null)
    setRunStatus('pending')
    setNodeOutputs([])
    setApprovalOpen(false)
    setObjective('')
    setRunOpen(false)
  }

  // ── Goal history playback ───────────────────────────────────

  const closeViewing = useCallback(() => {
    viewingSseRef.current?.close()
    viewingSseRef.current = null
    setViewingGoal(null)
    setViewingGraph(null)
    setViewingStatus('idle')
    setViewingProgress(0)
  }, [])

  const openHistoryGoal = async (g: HistoryGoal) => {
    setError('')
    try {
      const r = await api.workflow.graph(g.workflow_id)
      setViewingGoal(g)
      setViewingGraph({
        nodes: r.nodes.map((n) => ({
          label: n.label,
          status: 'pending' as const,
          agentName: n.id,
          type: (n as { type?: string }).type,
          agentColor: (n as { type?: string }).type
            ? undefined
            : legacyAgentColor(n.id),
        })),
        edges: r.edges,
      })
      setViewingStatus('idle')
      setViewingProgress(0)
      const st = await api.workflow.status(g.goal_id)
      if (st.status === 'ok' && st.progress) {
        const p = st.progress
        setViewingStatus(p.paused ? 'paused' : p.status === 'completed' ? 'completed' : p.status === 'error' ? 'failed' : 'running')
        const total = Number(p.agents_total ?? 0)
        const done = Number(p.agents_completed ?? 0)
        if (total > 0) setViewingProgress(Math.round((done / total) * 100))
        if (p.agent_statuses) {
          setViewingGraph((g2) => {
            if (!g2) return g2
            return {
              ...g2,
              nodes: g2.nodes.map((n) => {
                const st2 = p.agent_statuses![n.agentName ?? '']
                return st2 ? { ...n, status: mapAgentStatus(st2) } : n
              }),
            }
          })
        }
        if (p.status === 'running' || p.paused) connectViewingSSE(g.goal_id)
      } else if (g.goal_status === 'complete') {
        setViewingStatus('completed')
      }
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const connectViewingSSE = (gid: string) => {
    viewingSseRef.current?.close()
    const source = api.sse(`/goal/workflow/events?goal_id=${encodeURIComponent(gid)}`)
    viewingSseRef.current = source

    const applyProgress = (data: Record<string, any>) => {
      if (data.status === 'running' || data.status === 'paused') {
        setViewingStatus(data.paused ? 'paused' : 'running')
      } else if (data.status === 'completed') {
        setViewingStatus('completed')
      } else if (data.status === 'error') {
        setViewingStatus('failed')
      }
      const total = Number(data.agents_total ?? 0)
      const done = Number(data.agents_completed ?? 0)
      if (total > 0) setViewingProgress(Math.round((done / total) * 100))
      if (data.agent_statuses) {
        setViewingGraph((g) => {
          if (!g) return g
          return {
            ...g,
            nodes: g.nodes.map((n) => {
              const st = data.agent_statuses[n.agentName ?? '']
              return st ? { ...n, status: mapAgentStatus(st) } : n
            }),
          }
        })
      }
    }

    source.addEventListener('progress', (e) => {
      try { applyProgress(JSON.parse((e as MessageEvent).data)) } catch { /* ignore */ }
    })
    source.addEventListener('dag_update', (e) => {
      try {
        const d = JSON.parse((e as MessageEvent).data) as { node_id?: string; status?: string }
        if (!d.node_id) return
        setViewingGraph((g) => {
          if (!g) return g
          return {
            ...g,
            nodes: g.nodes.map((n) =>
              n.agentName === d.node_id && d.status ? { ...n, status: mapAgentStatus(d.status) } : n,
            ),
          }
        })
      } catch { /* ignore */ }
    })
  }

  const onPauseViewing = async () => {
    if (!viewingGoal) return
    try {
      await api.workflow.pause(viewingGoal.goal_id)
      setViewingStatus('paused')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const onResumeViewing = async () => {
    if (!viewingGoal) return
    try {
      await api.workflow.resume(viewingGoal.goal_id)
      setViewingStatus('running')
      connectViewingSSE(viewingGoal.goal_id)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const statusMeta = RUN_STATUS_LABELS[runStatus]
  const currentSource = definitions.find((d) => d.name === editingName)?.source
  const currentDesc = definitions.find((d) => d.name === editingName)?.description ?? ''

  return (
    <div className="flex h-full min-h-0 flex-col">
      {error && (
        <div className="mx-4 mt-2 rounded border border-rose-800 bg-rose-950/50 px-3 py-1.5 text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* ── Workflow info bar (canvas top-left) ─────────────── */}
      <div className="wf-panel flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <FileClock className="h-4 w-4 text-primary-400" />
        {editing?.name === null ? (
          <input
            value={editingName}
            onChange={(e) => setEditingName(e.target.value)}
            placeholder="定义名称"
            className="wf-input w-44 rounded border px-2 py-1.5 text-[13px] outline-none focus:border-primary-500"
          />
        ) : (
          <select
            value={editingName || ''}
            onChange={(e) => e.target.value && startEdit(e.target.value)}
            className="wf-input max-w-56 rounded border px-2 py-1.5 text-[13px] outline-none focus:border-primary-500"
            title="切换工作流定义"
          >
            <option value="" disabled>
              {editingName ? editingName : '选择工作流…'}
            </option>
            {definitions.map((d) => (
              <option key={d.name} value={d.name}>{d.name}</option>
            ))}
          </select>
        )}
        {currentSource && (
          <span className={`badge-${currentSource === 'builtin' ? 'builtin' : 'user'} rounded px-1.5 py-0.5 text-[10px] font-medium`}>
            {currentSource === 'builtin' ? '内置' : '用户'}
          </span>
        )}
        {currentDesc && (
          <span className="wf-text-sub hidden truncate text-xs md:inline">{currentDesc}</span>
        )}
        {savedAt && <span className="badge-completed rounded px-1.5 py-0.5 text-[10px] font-medium">已保存 {savedAt}</span>}

        <div className="flex-1" />

        {run && (
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${statusMeta.cls}`}>
            {statusMeta.text}
          </span>
        )}
        <button
          onClick={startNew}
          className="inline-flex items-center gap-1 rounded border border-slate-600 bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
        >
          <Plus className="h-3 w-3" /> 新建
        </button>
        <button
          onClick={() => setImportOpen(true)}
          className="inline-flex items-center gap-1 rounded border border-slate-600 bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
        >
          <FileJson className="h-3 w-3" /> 导入
        </button>
        {editing && (
          <button
            onClick={() => saveRef.current?.()}
            disabled={saving}
            className="inline-flex items-center gap-1 rounded bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            <Save className="h-3 w-3" /> {saving ? '保存中…' : '保存'}
          </button>
        )}
        {editing && (
          <button
            onClick={() => setRunOpen(true)}
            disabled={!editing.name || !sessionId}
            title={!sessionId ? '需要先打开一个会话' : '启动运行'}
            className="inline-flex items-center gap-1 rounded bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-500 disabled:opacity-40"
          >
            <Play className="h-3 w-3" /> 运行
          </button>
        )}
        {run && runStatus === 'awaiting' && (
          <button
            onClick={() => setApprovalOpen(true)}
            className="rounded bg-warning px-2 py-1 text-xs text-white hover:bg-warning"
          >
            待审批
          </button>
        )}
        {run && runStatus !== 'completed' && runStatus !== 'failed' && (
          <button
            onClick={resetRun}
            className="wf-text-sub inline-flex items-center gap-1 rounded px-2 py-1 text-xs hover:bg-slate-800"
          >
            <RefreshCw className="h-3 w-3" /> 停止追踪
          </button>
        )}
      </div>

      {/* ── Main: editor canvas or goal playback ────────────── */}
      <div className="min-h-0 flex-1">
        {viewingGoal ? (
          <div className="flex h-full min-h-0 flex-col">
            <div className="wf-panel flex items-center gap-2 border-b px-3 py-1.5">
              <button
                onClick={closeViewing}
                className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-sky-300 hover:bg-slate-800"
              >
                <ArrowLeft className="h-3 w-3" /> 返回编辑
              </button>
              <History className="h-3 w-3 text-slate-500" />
              <span className="wf-text-main text-xs">回看：{viewingGoal.workflow_id}</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                viewingGoal.goal_status === 'complete' ? 'badge-completed'
                  : viewingGoal.goal_status === 'active' ? 'badge-running'
                    : 'badge-neutral'
              }`}>
                {GOAL_STATUS_LABELS[viewingGoal.goal_status] ?? viewingGoal.goal_status}
              </span>
              {viewingStatus === 'running' && (
                <button
                  onClick={onPauseViewing}
                  className="rounded bg-warning px-2 py-0.5 text-[11px] text-white hover:bg-warning"
                >
                  暂停
                </button>
              )}
              {viewingStatus === 'paused' && (
                <button
                  onClick={onResumeViewing}
                  className="rounded bg-emerald-600 px-2 py-0.5 text-[11px] text-white hover:bg-emerald-500"
                >
                  恢复
                </button>
              )}
              <span className="wf-text-sub truncate text-[11px]">{viewingGoal.objective}</span>
            </div>
            <div className="min-h-0 flex-1">
              {viewingGraph ? (
                <WorkflowDAG
                  workflowName={viewingGoal.workflow_id}
                  nodes={viewingGraph.nodes}
                  edges={viewingGraph.edges}
                  status={viewingStatus}
                  progress={viewingProgress}
                  completed={viewingGraph.nodes.filter((n) => n.status === 'completed').length}
                  total={viewingGraph.nodes.length}
                />
              ) : (
                <div className="flex h-full items-center justify-center">
                  <EmptyState icon={<History className="h-10 w-10" />} title="加载中…" description="" />
                </div>
              )}
            </div>
          </div>
        ) : editing ? (
          <WorkflowEditor
            key={editing.name ?? `new-${editingName}`}
            nodes={editing.nodes}
            edges={editing.edges}
            onSave={saveDefinition}
            saving={saving}
            saveRef={saveRef}
            dagId={editing.name ?? `new-${editingName}`}
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <EmptyState
              icon={<Save className="h-10 w-10" />}
              title="选择或新建工作流"
              description="从左上角下拉选择定义，或点击「新建」从零搭建（拖拽式画布）"
            />
          </div>
        )}
      </div>

      {/* ── Bottom run drawer ───────────────────────────────── */}
      {runOpen && (
        <div className="wf-panel border-t">
          <div className="flex items-center gap-3 px-4 pt-2">
            <div className="wf-text-sub text-[11px] font-medium uppercase tracking-wide">运行面板</div>
            <button
              onClick={() => setRunOpen(false)}
              className="wf-text-sub ml-auto inline-flex items-center gap-1 rounded px-2 py-1 text-[10px] hover:bg-slate-800"
            >
              <ChevronDown className="h-3 w-3" /> 折叠
            </button>
          </div>
          <div className="flex items-start gap-3 px-4 py-2">
            <div className="flex-1">
              <textarea
                rows={1}
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                placeholder="例：找出沪深300上 Sharpe > 1.5 的动量因子"
                className="wf-input w-full resize-none rounded border px-2 py-1.5 text-xs outline-none focus:border-primary-500"
              />
              {!sessionId && (
                <p className="mt-1 text-[10px] text-warning-fg">需要先打开一个会话</p>
              )}
            </div>
            <button
              onClick={startRun}
              disabled={starting || !editing?.name || !objective.trim() || !sessionId}
              className="inline-flex items-center gap-1 rounded bg-emerald-600 px-3 py-1.5 text-xs text-white hover:bg-emerald-500 disabled:opacity-40"
            >
              <Play className="h-3 w-3" />
              {starting ? <Loader2 className="h-3 w-3 animate-spin" /> : '启动运行'}
            </button>
            {run && (
              <button
                onClick={resetRun}
                className="wf-text-sub inline-flex items-center gap-1 rounded border px-2 py-1.5 text-xs hover:bg-slate-800"
              >
                <RefreshCw className="h-3 w-3" /> 停止追踪
              </button>
            )}
          </div>
          {run && (
            <div className="flex flex-wrap items-center gap-3 px-4 pb-2 text-[11px]">
              <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${statusMeta.cls}`}>
                {statusMeta.text}
              </span>
              <span className="wf-text-sub">{run.run_id}</span>
              <span className="wf-text-sub">段：{run.segment_idx}/{run.segments_total}</span>
              <span className="wf-text-sub">重规划：{run.replan_count}/{run.replan_max}</span>
              <span className="wf-text-sub">已完成节点：{run.completed_nodes.length}</span>
              {run.failures.length > 0 && (
                <span className="text-rose-400">失败：{run.failures.join('；')}</span>
              )}
            </div>
          )}
          {run && nodeOutputs.length > 0 && (
            <div className="flex gap-2 overflow-x-auto px-4 pb-2">
              {nodeOutputs.map((o) => (
                <div key={o.node_id} className="wf-panel w-52 shrink-0 rounded border px-2 py-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      o.status === 'success' ? 'bg-emerald-400' : o.status === 'error' ? 'bg-rose-400' : 'bg-slate-500'
                    }`} />
                    <span className="wf-text-main truncate text-[10px]">{o.node_id}</span>
                    <span className="wf-text-sub ml-auto text-[10px]">{o.elapsed_s}s</span>
                  </div>
                  {o.summary && (
                    <div className="wf-text-sub mt-0.5 line-clamp-2 text-[9px]">{o.summary}</div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Run history */}
          <div className="wf-border border-t">
            <button
              onClick={() => setHistoryOpen((v) => !v)}
              className="wf-text-sub flex w-full items-center gap-2 px-4 py-1.5 text-[10px] hover:bg-slate-800/60"
            >
              <History className="h-3 w-3" />
              运行记录 {history.length > 0 ? `(${history.length})` : ''}
              {loadingHistory && <RefreshCw className="h-3 w-3 animate-spin" />}
              {historyOpen ? <ChevronDown className="h-3 w-3 ml-auto" /> : <ChevronUp className="h-3 w-3 ml-auto" />}
            </button>
            {historyOpen && (
              <div className="max-h-40 overflow-y-auto px-4 pb-2">
                {history.length === 0 ? (
                  <p className="wf-text-sub py-1 text-[11px]">暂无历史运行</p>
                ) : (
                  <ul className="space-y-1">
                    {history.map((g) => (
                      <li key={g.goal_id}>
                        <button
                          onClick={() => openHistoryGoal(g)}
                          className={`wf-panel w-full rounded border px-2 py-1 text-left transition-colors ${
                            viewingGoal?.goal_id === g.goal_id
                              ? 'border-sky-500/40 bg-sky-500/10'
                              : 'hover:border-slate-600'
                          }`}
                        >
                          <div className="flex items-center gap-1.5">
                            <span className={`rounded px-1 py-0.5 text-[9px] font-medium ${
                              g.goal_status === 'complete'
                                ? 'bg-emerald-900/50 text-emerald-400'
                                : g.goal_status === 'active'
                                  ? 'bg-sky-900/50 text-sky-300'
                                  : 'bg-slate-800 text-slate-400'
                            }`}>
                              {GOAL_STATUS_LABELS[g.goal_status] ?? g.goal_status}
                            </span>
                            <span className="wf-text-main truncate text-[10px]">{g.objective}</span>
                            <span className="wf-text-sub ml-auto text-[9px]">{formatTime(g.created_at)}</span>
                          </div>
                          <div className="wf-text-sub mt-0.5 text-[9px]">{g.workflow_id}</div>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Collapsed run toggle */}
      {!runOpen && run && (
        <button
          onClick={() => setRunOpen(true)}
          className="wf-panel wf-text-sub flex items-center gap-2 border-t px-4 py-1.5 text-[10px] hover:bg-slate-800"
        >
          <ChevronUp className="h-3 w-3" />
          <span className={`rounded px-1 py-0.5 font-medium ${statusMeta.cls}`}>{statusMeta.text}</span>
          <span className="wf-text-sub">{run.run_id}</span>
        </button>
      )}

      <ApprovalDialog
        open={approvalOpen}
        runId={run?.run_id ?? ''}
        planPreview={approvalPreview}
        busy={approvalBusy}
        onApprove={(edits) => respondApproval(true, edits)}
        onReject={(edits) => respondApproval(false, edits)}
        onClose={() => setApprovalOpen(false)}
      />

      <ImportDefinitionDialog
        open={importOpen}
        busy={importBusy}
        onImportToCanvas={importToCanvas}
        onSave={importAndSave}
        onClose={() => setImportOpen(false)}
      />
    </div>
  )
}
