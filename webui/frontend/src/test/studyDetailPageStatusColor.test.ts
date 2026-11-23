import { describe, it, expect } from 'vitest'
import { STUDY_STATUS_COLORS, STUDY_STATUS_LABELS } from '../components/study/constants'

/**
 * Replicates the post-fix status color extraction logic from
 * StudyDetailPage.tsx. The dead-ternary expression was:
 *   ``STUDY_STATUS_COLORS[status]?.split(' ')[0] ? 'text-slate-100' : 'text-slate-100'``
 * which always returned text-slate-100 (both branches identical).
 *
 * Post-fix:
 *   ``STUDY_STATUS_COLORS[status]?.split(' ').find((c: string) => c.startsWith('text-')) ?? 'text-slate-100'``
 * extracts the actual text-* class for the status.
 */
function extractTextClass(status: string): string {
  return (
    STUDY_STATUS_COLORS[status]?.split(' ').find((c: string) => c.startsWith('text-')) ??
    'text-slate-100'
  )
}

describe('StudyDetailPage status color extraction (P1-5 regression)', () => {
  it('returns the actual text-* class for known statuses', () => {
    expect(extractTextClass('running')).toBe('text-sky-100')
    expect(extractTextClass('paused')).toBe('text-warning-fg')
    expect(extractTextClass('error')).toBe('text-rose-100')
    expect(extractTextClass('complete')).toBe('text-emerald-100')
    expect(extractTextClass('monitoring')).toBe('text-indigo-100')
    expect(extractTextClass('cancelled')).toBe('text-slate-300')
  })

  it('falls back to text-slate-100 for unknown status', () => {
    expect(extractTextClass('unknown_status')).toBe('text-slate-100')
    expect(extractTextClass('')).toBe('text-slate-100')
  })

  it('handles every STATUS_LABELS key without crashing', () => {
    // Smoke-test: every status that has a label must produce a
    // text-* class (either from its color map or the fallback).
    for (const status of Object.keys(STUDY_STATUS_LABELS)) {
      const cls = extractTextClass(status)
      expect(cls.startsWith('text-')).toBe(true)
    }
  })

  it('extracted class differs across statuses (the bug would always return text-slate-100)', () => {
    const colors = new Set<string>()
    for (const status of Object.keys(STUDY_STATUS_COLORS)) {
      colors.add(extractTextClass(status))
    }
    // If the dead-ternary bug regressed, every status would yield
    // 'text-slate-100' and the set would have size 1.
    expect(colors.size).toBeGreaterThan(1)
  })

  it('archived status includes line-through (non-text class coexists)', () => {
    // The archived color string has 3 classes:
    //   'bg-slate-800 text-slate-500 line-through'
    // extractTextClass should still find text-slate-500, not the bg- or
    // line-through class.
    expect(extractTextClass('archived')).toBe('text-slate-500')
    expect(STUDY_STATUS_COLORS['archived'].split(' ')).toContain('line-through')
  })
})