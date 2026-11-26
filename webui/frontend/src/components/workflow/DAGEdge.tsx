import { memo } from 'react'
import { BaseEdge, getSmoothStepPath, type EdgeProps } from '@xyflow/react'

export interface DAGEdgeData {
  animated?: boolean
  [key: string]: unknown
}

export const DAGEdge = memo(function DAGEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
  markerEnd,
}: EdgeProps) {
  const edgeData = (data || {}) as unknown as DAGEdgeData
  const [edgePath] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 10,
  })

  const isAnimated = edgeData.animated
  const isActive = selected || isAnimated

  return (
    <BaseEdge
      id={id}
      path={edgePath}
      markerEnd={markerEnd}
      className={isAnimated ? 'dag-edge-flowing' : undefined}
      style={{
        stroke: isActive ? 'var(--wf-node-1)' : 'var(--edge-stroke)',
        strokeWidth: isActive ? 2.2 : 1.5,
        strokeDasharray: isAnimated ? '7 4' : undefined,
        filter: isActive ? 'drop-shadow(0 0 5px rgb(var(--wf-node-1-rgb) / 0.45))' : undefined,
      }}
    />
  )
})
