import { useEffect, useRef, useState } from 'react'

interface Props {
  /**
   * Called with the drag delta as a fraction of the parent container's
   * width. Positive when the pointer moves right, negative when left.
   * The parent decides which panel(s) the delta applies to, so a single
   * divider maps to exactly one boundary.
   */
  onDrag: (deltaRatio: number) => void
  hitWidth?: number
}

/**
 * A thin vertical divider that sits BETWEEN two flex siblings. It is an
 * independent flex node, so there is exactly one bar per boundary — it
 * can never stack two handles into a single gap.
 *
 * The bar pairs a wide invisible hit-area (`hitWidth`, default 10px) with
 * a centered 1px visual line so the affordance reads as a single handle
 * rather than bleeding into the adjacent panel's background.
 *
 * Drag is bound on `window` so the pointer can leave the divider mid-drag
 * without interrupting the gesture. The delta is reported as a ratio of
 * the divider's parent width, letting the caller resize the adjacent panel.
 */
export function SplitDivider({ onDrag, hitWidth = 10 }: Props) {
  const hitRef = useRef<HTMLDivElement>(null)
  const lastClientX = useRef<number | null>(null)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    if (!dragging) return
    const onMove = (e: MouseEvent) => {
      const hit = hitRef.current
      const parent = hit?.parentElement
      if (!hit || !parent) return
      const parentRect = parent.getBoundingClientRect()
      if (parentRect.width <= 0) return
      if (lastClientX.current !== null) {
        onDrag((e.clientX - lastClientX.current) / parentRect.width)
      }
      lastClientX.current = e.clientX
    }
    const onUp = () => {
      setDragging(false)
      lastClientX.current = null
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      lastClientX.current = null
    }
  }, [dragging, onDrag])

  return (
    <div
      ref={hitRef}
      onMouseDown={(e) => {
        e.preventDefault()
        lastClientX.current = e.clientX
        setDragging(true)
      }}
      className="group relative flex h-full cursor-col-resize items-center justify-center bg-transparent"
      style={{ width: hitWidth }}
      data-testid="split-divider"
    >
      <div
        className={`h-full w-px transition-colors ${
          dragging ? 'bg-primary-500' : 'bg-surface-raised group-hover:bg-primary-500'
        }`}
      />
    </div>
  )
}