import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  ReactFlowProvider,
  type Connection,
  type Node,
  type Edge,
  type NodeTypes,
  type OnNodesChange,
  type OnEdgesChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from '../../api/client'
import { OrchestratorChat } from './OrchestratorChat'
import { sanitizeSpec, validateDag, mergeDagStep, type DagSpec } from './dagSpec'
import { TYPE_META, NODE_PALETTE, CONFIG_FIELDS, type PaletteItem } from './nodeTypes'
import { Trash2, Plus, Search, LayoutGrid, Undo2, Redo2 } from 'lucide-react'
import { DAGNode, type DAGNodeData } from './DAGNode'
import { DAGEdge } from './DAGEdge'
import { layoutWithDagre } from './layout'
import type { DefinitionEdge, DefinitionNode } from '../../api/client'

const DRAG_DATA_KEY = 'application/x-workflow-node-type'
const ARROW_MARKER = 'url(#dag-arrow)'

interface WorkflowEditorProps {
  nodes: DefinitionNode[]
  edges: DefinitionEdge[]
  onSave: (nodes: DefinitionNode[], edges: DefinitionEdge[]) => void
  saving?: boolean
  saveRef?: React.MutableRefObject<(() => void) | null>
  /** DAG-bound orchestrator session id (definition name or new:{name}). */
  dagId?: string
}

function buildRfNode(n: DefinitionNode): Node {
  const meta = TYPE_META[n.type] as PaletteItem | undefined
  return {
    id: n.id,
    type: 'dagNode',
    position: { x: 100 + Math.random() * 120, y: 100 + Math.random() * 120 },
    data: {
      label: (n.label || meta?.label) ?? n.id,
      status: 'pending' as const,
      agentName: n.id,
      type: n.type,
      agentColor: meta?.color,
      config: n.config,
    } as DAGNodeData,
  }
}

export function WorkflowEditor({ nodes, edges, onSave, saving, saveRef, dagId }: WorkflowEditorProps) {
  return (
    <ReactFlowProvider>
      <WorkflowEditorInner nodes={nodes} edges={edges} onSave={onSave} saving={saving} saveRef={saveRef} dagId={dagId} />
    </ReactFlowProvider>
  )
}

function WorkflowEditorInner({ nodes, edges, onSave, saving, saveRef, dagId }: WorkflowEditorProps) {
  const [rfNodes, setRfNodes, onNodesChangeRaw] = useNodesState(nodes.map(buildRfNode))
  const [rfEdges, setRfEdges, onEdgesChangeRaw] = useEdgesState<Edge>(
    edges.map((e, i) => ({ id: `e-${i}`, source: e.source, target: e.target, type: 'dagEdge', markerEnd: ARROW_MARKER }) as Edge),
  )
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [canUndo, setCanUndo] = useState(false)
  const [canRedo, setCanRedo] = useState(false)

  const undoStack = useRef<Array<{ nodes: Node[]; edges: Edge[] }>>([])
  const redoStack = useRef<Array<{ nodes: Node[]; edges: Edge[] }>>([])
  const dragTypeRef = useRef<string | null>(null)
  const instance = useReactFlow()

  const nodeTypes: NodeTypes = useMemo(() => ({ dagNode: DAGNode }), [])
  const edgeTypes = useMemo(() => ({ dagEdge: DAGEdge }), [])

  // ── undo / redo ──────────────────────────────────────────────

  const snapshot = useCallback(() => ({ nodes: rfNodes, edges: rfEdges }), [rfNodes, rfEdges])

  const pushHistory = useCallback(() => {
    undoStack.current.push({ nodes: rfNodes.map((n) => ({ ...n, position: { ...n.position } })), edges: rfEdges })
    redoStack.current = []
    setCanUndo(true)
    setCanRedo(false)
  }, [rfNodes, rfEdges])

  const undo = useCallback(() => {
    const prev = undoStack.current.pop()
    if (!prev) return
    redoStack.current.push(snapshot())
    setRfNodes(prev.nodes)
    setRfEdges(prev.edges)
    setCanUndo(undoStack.current.length > 0)
    setCanRedo(true)
  }, [setRfNodes, setRfEdges, snapshot])

  const redo = useCallback(() => {
    const next = redoStack.current.pop()
    if (!next) return
    undoStack.current.push(snapshot())
    setRfNodes(next.nodes)
    setRfEdges(next.edges)
    setCanRedo(redoStack.current.length > 0)
    setCanUndo(true)
  }, [setRfNodes, setRfEdges, snapshot])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      const typing = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable
      if (typing || !(e.ctrlKey || e.metaKey)) return
      if (e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) redo()
        else undo()
      } else if (e.key.toLowerCase() === 'y') {
        e.preventDefault()
        redo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undo, redo])

  // ── node / edge changes (push history for structural changes) ─

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => {
      const structural = changes.some((c) => c.type === 'add' || c.type === 'remove')
      if (structural) pushHistory()
      else if (changes.some((c) => c.type === 'position' && c.dragging === false)) pushHistory()
      onNodesChangeRaw(changes)
    },
    [onNodesChangeRaw, pushHistory],
  )

  const onEdgesChange: OnEdgesChange = useCallback(
    (changes) => {
      if (changes.some((c) => c.type === 'add' || c.type === 'remove')) pushHistory()
      onEdgesChangeRaw(changes)
    },
    [onEdgesChangeRaw, pushHistory],
  )

  const onConnect = useCallback(
    (conn: Connection) => {
      if (!conn.source || !conn.target || conn.source === conn.target) return
      setRfEdges((eds) => addEdge({ ...conn, type: 'dagEdge', markerEnd: ARROW_MARKER }, eds))
      pushHistory()
    },
    [setRfEdges, pushHistory],
  )

  const addNode = useCallback(
    (type: string, position?: { x: number; y: number }) => {
      const meta = TYPE_META[type]
      if (!meta) return
      const id = `${type}_${Math.random().toString(36).slice(2, 6)}`
      const node: Node = {
        id,
        type: 'dagNode',
        position: position ?? { x: 100 + Math.random() * 200, y: 150 + Math.random() * 150 },
        data: {
          label: meta.label,
          status: 'pending' as const,
          agentName: id,
          type,
          agentColor: meta.color,
          config: { ...meta.defaults },
        } as DAGNodeData,
      }
      setRfNodes((ns) => [...ns, node])
      setSelectedId(id)
      pushHistory()
    },
    [setRfNodes, pushHistory],
  )

  const removeNode = useCallback(
    (id: string) => {
      setRfNodes((ns) => ns.filter((n) => n.id !== id))
      setRfEdges((eds) => eds.filter((e) => e.source !== id && e.target !== id))
      setSelectedId(null)
      pushHistory()
    },
    [setRfNodes, setRfEdges, pushHistory],
  )

  const updateConfig = useCallback(
    (id: string, patch: Record<string, unknown>) => {
      setRfNodes((ns) =>
        ns.map((n) =>
          n.id === id
            ? { ...n, data: { ...(n.data as DAGNodeData), config: { ...((n.data as DAGNodeData).config as object), ...patch } } }
            : n,
        ),
      )
    },
    [setRfNodes],
  )

  const updateLabel = useCallback(
    (id: string, label: string) => {
      setRfNodes((ns) =>
        ns.map((n) =>
          n.id === id ? { ...n, data: { ...(n.data as DAGNodeData), label } } : n,
        ),
      )
    },
    [setRfNodes],
  )

  // ── drop-to-add from palette ─────────────────────────────────

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      const type = dragTypeRef.current ?? e.dataTransfer.getData(DRAG_DATA_KEY)
      if (!type || !TYPE_META[type]) return
      const pos = instance.screenToFlowPosition({ x: e.clientX, y: e.clientY })
      addNode(type, { x: pos.x - 90, y: pos.y - 35 })
      dragTypeRef.current = null
    },
    [instance, addNode],
  )

  // ── auto layout ──────────────────────────────────────────────

  const autoLayout = useCallback(() => {
    if (rfNodes.length === 0) return
    const { nodes: laidOut } = layoutWithDagre(
      rfNodes.map((n) => ({ id: n.id, ...(n.data as DAGNodeData) })),
      rfEdges.map((e) => ({ source: e.source, target: e.target })),
      { nodeType: 'dagNode', edgeType: 'dagEdge' },
    )
    pushHistory()
    setRfNodes((ns) =>
      ns.map((n) => {
        const laid = laidOut.find((l) => l.id === n.id)
        return laid ? { ...n, position: laid.position } : n
      }),
    )
  }, [rfNodes, rfEdges, setRfNodes, pushHistory])

  // ── save ─────────────────────────────────────────────────────

  const handleSave = () => {
    const defNodes: DefinitionNode[] = rfNodes.map((n) => {
      const d = n.data as DAGNodeData
      return {
        id: n.id,
        type: (d.type as DefinitionNode['type']) || 'llm_agent',
        label: (d.label as string) || '',
        config: ((d.config as object) ?? {}) as Record<string, unknown>,
      }
    })
    const defEdges: DefinitionEdge[] = rfEdges.map((e) => ({ source: e.source, target: e.target }))
    onSave(defNodes, defEdges)
  }

  // Expose save to the page-level info bar (top-left workflow config bar)
  useEffect(() => {
    if (saveRef) saveRef.current = handleSave
    return () => {
      if (saveRef) saveRef.current = null
    }
  }, [saveRef, rfNodes, rfEdges, onSave])

  // ── orchestration chat: applyDag + auto-save draft ─────────────
  const saveDraft = useCallback(async () => {
    if (!dagId || rfNodes.length === 0) return
    const draftNodes = rfNodes.map((n) => {
      const d = n.data as DAGNodeData
      return { id: n.id, type: d.type ?? 'llm_agent', label: d.label ?? '', config: (d.config as object) ?? {} }
    })
    const draftEdges = rfEdges.map((e) => ({ source: e.source, target: e.target }))
    try {
      await api.orchestrate.saveDraft(dagId, draftNodes, draftEdges)
    } catch {
      // autosave is best-effort
    }
  }, [dagId, rfNodes, rfEdges])

  const draftTimer = useRef<number | null>(null)
  useEffect(() => {
    if (draftTimer.current) window.clearTimeout(draftTimer.current)
    draftTimer.current = window.setTimeout(() => void saveDraft(), 2000)
    return () => {
      if (draftTimer.current) window.clearTimeout(draftTimer.current)
    }
  }, [rfNodes, rfEdges, saveDraft])

  const getSnapshot = useCallback(
    (): DagSpec => ({
      nodes: rfNodes.map((n) => {
        const d = n.data as DAGNodeData
        return { id: n.id, type: d.type ?? 'llm_agent', label: d.label ?? '', config: (d.config as Record<string, unknown>) ?? {} }
      }),
      edges: rfEdges.map((e) => ({ source: e.source, target: e.target })),
    }),
    [rfNodes, rfEdges],
  )

  const applyDag = useCallback(
    (spec: DagSpec) => {
      const clean = sanitizeSpec(spec)
      // Merge semantics: the LLM submits only what it touched this round
      // (one edit per round); canvas nodes/edges are kept and overridden
      // rather than replaced wholesale.
      const merged = mergeDagStep(getSnapshot(), clean)
      const errors = validateDag(merged.nodes, merged.edges)
      if (errors.length > 0) return { ok: false as const, errors }
      const rawNodes: Node[] = merged.nodes.map((n) => {
        const meta = TYPE_META[n.type]
        return {
          id: n.id,
          type: 'dagNode',
          position: { x: 100 + Math.random() * 120, y: 100 + Math.random() * 120 },
          data: {
            label: n.label ?? meta?.label ?? n.id,
            status: 'pending' as const,
            agentName: n.id,
            type: n.type,
            agentColor: meta?.color,
            config: n.config ?? {},
          } as DAGNodeData,
        }
      })
      const rawEdges: Edge[] = merged.edges.map((e, i) => ({
        id: `e-${i}`, source: e.source, target: e.target, type: 'dagEdge', markerEnd: ARROW_MARKER,
      }))
      pushHistory()
      const { nodes: laidOut } = layoutWithDagre(
        rawNodes.map((n) => ({ id: n.id, ...(n.data as DAGNodeData) })),
        merged.edges.map((e) => ({ source: e.source, target: e.target })),
        { nodeType: 'dagNode', edgeType: 'dagEdge' },
      )
      setRfNodes(laidOut.length > 0 ? laidOut : rawNodes)
      setRfEdges(rawEdges)
      // persist immediately so a refresh right after this step restores it
      void saveDraft()
      return { ok: true as const }
    },
    [getSnapshot, pushHistory, saveDraft, setRfEdges, setRfNodes],
  )

  const selected = rfNodes.find((n) => n.id === selectedId)
  const selectedData = selected?.data as DAGNodeData | undefined
  const selectedType = selectedData?.type as string | undefined
  const selectedConfig = (selectedData?.config as Record<string, unknown> | undefined) ?? {}

  const palette = useMemo(
    () =>
      search.trim()
        ? NODE_PALETTE.filter(
            (p) => p.label.includes(search) || p.type.includes(search) || p.desc.includes(search),
          )
        : NODE_PALETTE,
    [search],
  )

  return (
    <div className="flex h-full min-h-0">
      {/* Orchestration chat */}
      {dagId && (
        <OrchestratorChat dagId={dagId} getSnapshot={getSnapshot} onApplyDag={(spec) => void applyDag(spec)} />
      )}

      {/* Palette */}
      <aside className="wf-panel-solid flex w-56 shrink-0 flex-col gap-1.5 overflow-y-auto border-r p-2">
        <div className="relative">
          <Search className="wf-text-sub pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索节点…"
            className="wf-input w-full rounded-lg border py-2 pl-8 pr-2 text-xs outline-none focus:border-primary-500"
          />
        </div>
        <div className="wf-text-sub px-1 pt-1 text-[11px] font-medium uppercase tracking-wide">节点库</div>
        {palette.map((p) => {
          const Icon = p.icon
          return (
            <button
              key={p.type}
              draggable
              onDragStart={(e) => {
                dragTypeRef.current = p.type
                e.dataTransfer.setData(DRAG_DATA_KEY, p.type)
                e.dataTransfer.effectAllowed = 'move'
              }}
              onDragEnd={() => { dragTypeRef.current = null }}
              onClick={() => addNode(p.type)}
              className="group flex items-center gap-2.5 rounded-lg border border-subtle bg-slate-900 px-2.5 py-2 text-left transition-colors hover:border-slate-500 hover:bg-surface-raised dark-mode-card"
              title={`${p.desc}\n点击添加，或拖拽到画布指定位置`}
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg" style={{ backgroundColor: 'var(--wf-node-1-soft)', color: p.color, boxShadow: 'inset 0 0 0 1px var(--wf-node-1-soft)' }}>
                <Icon className="h-4 w-4" />
              </span>
              <span className="min-w-0">
                <span className="block text-[13px] font-medium leading-tight text-foreground wf-card-title">{p.label}</span>
                <span className="mt-0.5 block truncate font-mono text-[11px] leading-tight text-muted wf-card-sub">{p.type}</span>
              </span>
              <Plus className="ml-auto h-3.5 w-3.5 shrink-0 text-muted group-hover:text-secondary wf-card-plus" />
            </button>
          )
        })}
        <p className="wf-text-sub px-1 pb-1 text-[11px] leading-relaxed">
          点击添加节点，或拖拽到画布指定位置。Ctrl+Z 撤销，Ctrl+Shift+Z 重做，Delete 删除选中。
        </p>
      </aside>

      {/* Canvas */}
      <div className="relative min-w-0 flex-1" style={{ background: "var(--canvas-bg)" }}>
        {rfNodes.length === 0 && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
            <div className="wf-panel rounded-2xl border border-dashed px-9 py-8 text-center backdrop-blur-sm">
              <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl border border-strong bg-surface-raised/90 shadow-lg">
                <Plus className="h-5 w-5 text-primary-400" />
              </div>
              <div className="wf-text-main text-sm font-medium">空画布</div>
              <div className="wf-text-sub mt-1.5 text-xs leading-relaxed">
                从左侧节点库<strong className="wf-text-main font-medium">点击</strong>或<strong className="wf-text-main font-medium">拖拽</strong>添加节点
              </div>
              <div className="wf-text-sub mt-1 text-[11px]">
                从节点右侧把手拖到目标左侧把手连线 · Delete 删除 · Ctrl+Z 撤销
              </div>
            </div>
          </div>
        )}
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodeClick={(_, n) => setSelectedId(n.id)}
          onPaneClick={() => setSelectedId(null)}
          onDragOver={onDragOver}
          onDrop={onDrop}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.2}
          maxZoom={1.5}
          deleteKeyCode={['Delete', 'Backspace']}
          proOptions={{ hideAttribution: true }}
        >
          <defs>
            <marker
              id="dag-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--marker-fill)" />
            </marker>
          </defs>
          <Background variant={BackgroundVariant.Dots} gap={26} size={1.2} color="var(--canvas-dot)" />
          <Background variant={BackgroundVariant.Lines} gap={130} size={0.6} color="var(--canvas-line)" />
          <Controls />
          <MiniMap pannable zoomable className="!bg-slate-900 wf-minimap" nodeColor={(n) => {
            const d = n.data as DAGNodeData
            return d.agentColor ?? 'var(--viz-series-fallback)'
          }} />
        </ReactFlow>
        <div className="absolute bottom-4 left-3 z-10 flex gap-1.5">
          <button
            onClick={undo}
            disabled={!canUndo}
            title="撤销 (Ctrl+Z)"
            className="wf-panel wf-text-sub inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-[10px] shadow-md backdrop-blur-sm hover:bg-surface-raised hover:text-foreground disabled:opacity-40"
          >
            <Undo2 className="h-3 w-3" /> 撤销
          </button>
          <button
            onClick={redo}
            disabled={!canRedo}
            title="重做 (Ctrl+Shift+Z)"
            className="wf-panel wf-text-sub inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-[10px] shadow-md backdrop-blur-sm hover:bg-surface-raised hover:text-foreground disabled:opacity-40"
          >
            <Redo2 className="h-3 w-3" /> 重做
          </button>
          <button
            onClick={autoLayout}
            disabled={rfNodes.length === 0}
            title="dagre 自动布局"
            className="wf-panel wf-text-sub inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-[10px] shadow-md backdrop-blur-sm hover:bg-surface-raised hover:text-foreground disabled:opacity-40"
          >
            <LayoutGrid className="h-3 w-3" /> 自动布局
          </button>
        </div>
      </div>

      {/* Config panel */}
      <aside className="wf-panel-solid flex w-72 shrink-0 flex-col border-l p-3">
        <div className="wf-text-sub mb-2 text-[11px] font-medium uppercase tracking-wide">节点配置</div>
        {selected && selectedData ? (
          <div className="space-y-3">
            <div>
              <label className="wf-text-sub mb-1 block text-[10px]">名称</label>
              <input
                value={(selectedData.label as string) ?? ''}
                onChange={(e) => updateLabel(selected.id, e.target.value)}
                className="wf-input w-full rounded border px-2 py-1 text-xs outline-none focus:border-primary-500"
              />
            </div>
            <div className="wf-input wf-text-sub rounded px-2 py-1 text-[10px]">
              id: <span className="wf-text-main">{selected.id}</span> · type:{' '}
              <span className="wf-text-main">{selectedType}</span>
            </div>
            {CONFIG_FIELDS[selectedType ?? '']?.map((f) => (
              <div key={f.key}>
                <label className="wf-text-sub mb-1 block text-[10px]">{f.label}</label>
                {f.type === 'select' ? (
                  <select
                    value={(selectedConfig[f.key] as string) ?? ''}
                    onChange={(e) => updateConfig(selected.id, { [f.key]: e.target.value })}
                    className="wf-input w-full rounded border px-2 py-1 text-xs outline-none focus:border-primary-500"
                  >
                    <option value="">—</option>
                    {f.options?.map((o) => (
                      <option key={o} value={o}>{o}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={(selectedConfig[f.key] as string | number | undefined) ?? ''}
                    type={f.type === 'number' ? 'number' : 'text'}
                    placeholder={f.placeholder}
                    onChange={(e) => updateConfig(selected.id, { [f.key]: f.type === 'number' ? Number(e.target.value) : e.target.value })}
                    className="wf-input w-full rounded border px-2 py-1 text-xs outline-none focus:border-primary-500"
                  />
                )}
              </div>
            ))}
            {selectedType === 'approval' && (
              <p className="text-[10px] text-warning-fg/80">审批节点是图切点：上游段完成后暂停等待确认，超时保持等待。</p>
            )}
            <button
              onClick={() => removeNode(selected.id)}
              className="inline-flex w-full items-center justify-center gap-1 rounded border border-rose-800 bg-rose-950/40 px-2 py-1 text-xs text-rose-300 hover:bg-rose-950"
            >
              <Trash2 className="h-3 w-3" /> 删除节点
            </button>
          </div>
        ) : (
          <p className="wf-text-sub text-xs leading-relaxed">
            点击画布节点查看/编辑配置。连线：从节点右侧把手拖到目标节点左侧把手。
          </p>
        )}

        <div className="wf-border mt-auto space-y-1.5 border-t pt-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className="w-full rounded bg-indigo-600 px-2 py-1.5 text-xs text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {saving ? '保存中...' : '保存定义'}
          </button>
          <p className="wf-text-sub text-[11px] leading-relaxed">校验规则：6 种节点类型、planner/evaluator/approval 各最多 1 个、无环。</p>
        </div>
      </aside>
    </div>
  )
}
