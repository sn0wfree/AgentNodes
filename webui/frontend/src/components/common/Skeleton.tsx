interface SkeletonProps {
  className?: string
  variant?: 'rect' | 'circle' | 'text'
}

// Label retracted (2026-10-08): imported by catalog/stories.tsx + skeleton.test.tsx.
// TODO(feature): MessageSkeleton / AgentSkeleton are never imported.
// Written for the initial loading states; the app now renders real
// content immediately (store is populated from backend before paint),
// so skeletons were never hooked up. Use when adding per-tab lazy
// loading, or remove with that plan.
export function Skeleton({ className = '', variant = 'rect' }: SkeletonProps) {
  const base = 'animate-pulse bg-surface'
  const variantClass = {
    rect: 'rounded-lg',
    circle: 'rounded-full',
    text: 'rounded h-4',
  }[variant]

  return (
    <div className={`${base} ${variantClass} ${className}`} />
  )
}

export function MessageSkeleton() {
  return (
    <div className="flex gap-3 px-4 py-3">
      <Skeleton variant="circle" className="h-8 w-8 flex-shrink-0" />
      <div className="flex-1 space-y-2">
        <Skeleton variant="text" className="w-24" />
        <Skeleton variant="text" className="w-full" />
        <Skeleton variant="text" className="w-3/4" />
      </div>
    </div>
  )
}

export function AgentSkeleton() {
  return (
    <div className="flex items-center gap-3 px-4 py-3">
      <Skeleton variant="circle" className="h-6 w-6" />
      <Skeleton variant="text" className="w-32" />
      <Skeleton variant="rect" className="ml-auto h-5 w-16 rounded-full" />
    </div>
  )
}
