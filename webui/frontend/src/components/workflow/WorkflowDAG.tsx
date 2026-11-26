import { useState, useCallback, useMemo, useEffect } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,

  type NodeTypes,
  type EdgeTypes,
  BackgroundVariant,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { DAGNode, type DAGNodeData } from './DAGNode'
import { DAGEdge } from './DAGEdge'
import { DAGToolbar } from './DAGToolbar'
import { DAGProgressBar } from './DAGProgressBar'
import { DAGNodeDetail } from './DAGNodeDetail'
import { EmptyState } from '../common/EmptyState'
import { layoutWithDagre } from './layout'
import { Workflow } from 'lucide-react'

interface WorkflowDAGProps {
  workflowName?: string
  nodes?: DAGNodeData[]
  edges?: { source: string; target: string }[]
  status?: 'idle' | 'running' | 'paused' | 'completed' | 'failed'
  progress?: number
  completed?: number
  total?: number
  elapsed?: number
  onStart?: () => void
  onPause?: () => void
  onResume?: () => void
  onReset?: () => void
}

export function WorkflowDAG({
  workflowName = '未命名工作流',
  nodes: rawNodes = [],
  edges: rawEdges = [],
  status = 'idle',
  progress = 0,
  completed = 0,
  total = 0,
  elapsed,
  onStart,
  onPause,
  onResume,
  onReset,
}: WorkflowDAGProps) {
  const [selectedNode, setSelectedNode] = useState<(DAGNodeData & { id: string }) | null>(null)

  const { nodes: layoutNodes, edges: layoutEdges } = useMemo(
    () => layoutWithDagre(rawNodes, rawEdges),
    [rawNodes, rawEdges]
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutEdges)

  // Update nodes/edges when props change (effect, not memo — side
  // effects belong in useEffect; useMemo must stay pure).
  useEffect(() => {
    const { nodes: newNodes, edges: newEdges } = layoutWithDagre(rawNodes, rawEdges)
    setNodes(newNodes)
    setEdges(newEdges)
  }, [rawNodes, rawEdges, setNodes, setEdges])

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      setSelectedNode(node.data as unknown as DAGNodeData & { id: string })
    },
    []
  )

  const onPaneClick = useCallback(() => {
    setSelectedNode(null)
  }, [])

  const nodeTypes: NodeTypes = useMemo(() => ({ dagNode: DAGNode }), [])
  const edgeTypes: EdgeTypes = useMemo(() => ({ dagEdge: DAGEdge }), [])

  if (rawNodes.length === 0) {
    return (
      <EmptyState
        icon={<Workflow className="h-10 w-10" />}
        title="空工作流"
        description="添加节点后显示 DAG"
      />
    )
  }

  return (
    <div className="flex h-full flex-col">
      <DAGToolbar
        workflowName={workflowName}
        status={status}
        onStart={onStart}
        onPause={onPause}
        onResume={onResume}
        onReset={onReset}
      />
      <div className="relative flex-1 min-h-0">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.3}
          maxZoom={2}
          defaultEdgeOptions={{ type: 'dagEdge', markerEnd: 'url(#dag-arrow)' }}
          style={{ width: '100%', height: '100%' }}
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
          <Controls className="!rounded-lg" />
          <MiniMap
            nodeColor={(n) => {
              const d = n.data as unknown as DAGNodeData
              return d.agentColor || 'var(--viz-series-fallback)'
            }}
            maskColor="rgb(0 0 0 / 0.7)"
            className="wf-minimap"
          />
        </ReactFlow>

        {/* Node detail slide-out */}
        {selectedNode && (
          <DAGNodeDetail
            node={selectedNode}
            onClose={() => setSelectedNode(null)}
          />
        )}
      </div>
      <DAGProgressBar
        progress={progress}
        completed={completed}
        total={total}
        elapsed={elapsed}
      />
    </div>
  )
}
