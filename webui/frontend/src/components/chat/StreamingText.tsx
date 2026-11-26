import { useState, useEffect, useRef } from 'react'
import { MarkdownRenderer } from './MarkdownRenderer'

interface StreamingTextProps {
  text: string
  isDone: boolean
  /**
   * Optional override for the part id — feeds the `cacheKey` of the
   * stable-prefix MarkdownRenderer so a previous render of the same
   * prefix content skips re-parsing. Defaults to `text.length` which
   * is the most common case (stable prefix length grows monotonically
   * during streaming).
   */
  partId?: string
}

/**
 * Compute next reveal length, preferring word/character boundaries.
 *
 * Adopted from opencode's createPacedValue: a slow 8%-per-tick reveal
 * gives the eye time to track the text; the 512-character fast-path
 * kicks in when the LLM dumps a big batch (e.g. 2000 tokens at once
 * from a non-streaming-flush) so we don't fall further and further
 * behind — better to show the full batch immediately than to render
 * a perpetually-stale prefix.
 */
function nextRevealLen(text: string, lastLen: number): number {
  const target = text.length
  if (target <= lastLen) return lastLen

  const diff = target - lastLen
  // Long-burst fast-path: skip the per-tick reveal entirely so the
  // UI catches up to the live stream. Below threshold, fall through
  // to the paced reveal.
  if (diff > 512) return target
  if (diff <= 2) return target
  const want = Math.max(1, Math.ceil(diff * 0.08))
  let newLen = lastLen + want
  if (newLen >= target) return target

  // Word-boundary snap: prefer to land on whitespace / CJK punctuation
  // so we don't cut through a character or token mid-stream.
  while (newLen < target && !/[\s\n\u3002\uff0c\uff01\uff1f]/.test(text[newLen])) {
    newLen++
  }
  if (newLen < target && /[\s\n]/.test(text[newLen])) newLen++

  return Math.min(newLen, target)
}

/**
 * Split `displayed` into:
 *   - stablePrefix: complete lines (ending in \n). cacheKey = length
 *     so the MarkdownRenderer memo skips re-render when only the live
 *     tail grows.
 *   - liveTail: the partial last line (if any). cacheKey = a key that
 *     changes with each new character (no memoisation — the user
 *     wants to see the cursor chase the last character).
 */
function splitStableTail(displayed: string): {
  stablePrefix: string
  liveTail: string
  tailStart: number
} {
  if (!displayed) return { stablePrefix: '', liveTail: '', tailStart: 0 }
  const idx = displayed.lastIndexOf('\n')
  if (idx === -1) {
    return { stablePrefix: '', liveTail: displayed, tailStart: 0 }
  }
  return {
    stablePrefix: displayed.slice(0, idx + 1),
    liveTail: displayed.slice(idx + 1),
    tailStart: idx + 1,
  }
}

export function StreamingText({ text, isDone, partId }: StreamingTextProps) {
  const [displayed, setDisplayed] = useState('')
  const rafRef = useRef<number>(0)
  const lastLenRef = useRef(0)

  useEffect(() => {
    if (isDone) {
      // Snap to final on done. Skip the paced reveal.
      setDisplayed(text)
      lastLenRef.current = text.length
      return
    }

    const tick = () => {
      const target = text.length
      const newLen = nextRevealLen(text, lastLenRef.current)
      if (newLen > lastLenRef.current) {
        setDisplayed(text.slice(0, newLen))
        lastLenRef.current = newLen
      }
      if (lastLenRef.current < target) {
        rafRef.current = requestAnimationFrame(tick)
      }
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [text, isDone])

  const isStreaming = !isDone && displayed.length < text.length

  // Always show the streamed text (fallback to `text` only if we never
  // got a chance to animate — e.g. zero-length stream that finishes
  // before the first tick runs).
  const finalText = displayed || (!isStreaming ? text : '')

  const { stablePrefix, liveTail } = splitStableTail(finalText)
  const prefixCacheKey = partId
    ? `prefix-${partId}-${stablePrefix.length}`
    : stablePrefix.length
  const tailCacheKey = partId
    ? `tail-${partId}-${liveTail.length}`
    : `tail-${liveTail.length}`

  return (
    <div
      className="relative rounded-lg transition-shadow duration-500"
      style={
        isStreaming
          ? {
              boxShadow:
                '0 0 24px -4px rgb(var(--glow-stream-color) / 0.12), 0 0 8px -2px rgb(var(--glow-stream-color) / 0.06)',
            }
          : undefined
      }
    >
      {/* Stable prefix — memoised, never re-parses the same length. */}
      {stablePrefix && (
        <MarkdownRenderer
          content={stablePrefix}
          streaming={false}
          cacheKey={prefixCacheKey}
        />
      )}
      {/* Live tail — re-renders per character so the cursor chases. */}
      {liveTail && (
        <MarkdownRenderer
          content={liveTail}
          streaming={isStreaming}
          cacheKey={tailCacheKey}
        />
      )}
      {isStreaming && (
        <span
          className="streaming-cursor ml-0.5"
          aria-label="streaming"
        />
      )}
    </div>
  )
}
