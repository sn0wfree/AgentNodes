/**
 * JsonActionCard — renders a structured JSON object (from a study agent) in
 * an academic-style card.  Supports two modes:
 *   1. Action mode — JSON has an `action` field → styled badge per action type
 *   2. Generic mode — JSON without `action` → neutral "JSON 输出" badge
 *
 * The primary field is `hypothesis` (highlighted) when present, otherwise
 * the first short string field is shown.  All other fields are collapsed
 * behind a toggle.
 *
 * Design language: serif hints, muted palette, restrained colour —
 * academic paper / lab-notebook aesthetic.
 */
import { useState } from 'react'
import { getSchemaFor, type FieldHints } from '../../api/agentSchemas'

export interface JsonActionCardProps {
  /** The action type if the JSON contains an "action" field, else undefined. */
  action?: string
  /** The hypothesis or main explanatory field. */
  hypothesis?: string
  /** The full parsed JSON object. */
  fullJson: Record<string, unknown>
  /**
   * Agent role (message.agent_id). When a prompt-derived schema is loaded
   * (see /api/agents/schemas), field order / labels / core-visibility /
   * enum mappings / percentage formats follow the schema instead of the
   * generic KEY_FIELDS heuristics.
   */
  agentId?: string
}

const ACTION_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  optimize_param:  { bg: 'bg-sky-900/30',    text: 'text-sky-300',     label: '参数优化' },
  blocker:         { bg: 'bg-warning-fill',   text: 'text-warning-fg',   label: '阻塞' },
  keep:            { bg: 'bg-emerald-900/30', text: 'text-success-fg', label: '保留' },
  discard:         { bg: 'bg-rose-900/30',    text: 'text-rose-300',    label: '丢弃' },
  report_progress: { bg: 'bg-indigo-900/30',  text: 'text-indigo-300',  label: '进度报告' },
  request_data:    { bg: 'bg-cyan-900/30',    text: 'text-cyan-300',    label: '数据请求' },
  risk_assessment: { bg: 'bg-orange-900/30',  text: 'text-orange-300',  label: '风险评估' },
  portfolio:       { bg: 'bg-teal-900/30',    text: 'text-teal-300',    label: '组合配置' },
  overfit:         { bg: 'bg-violet-900/30',  text: 'text-violet-300',  label: '过拟合检查' },
}

const GENERIC_STYLE = { bg: 'bg-surface', text: 'text-secondary', label: 'JSON 输出' }

/** Fields used as "hypothesis"-equivalent (highlighted above the field list). */
const HYPOTHESIS_LIKE = ['hypothesis', 'interpretation', 'summary', 'description', 'reason', 'message']

/**
 * Core (decision/status) fields always shown by default — these carry the
 * final conclusion and shouldn't require expanding the card.
 *   - verdict / risk_passed / overfit_passed: pass/fail boolean
 *   - risk_rating / status / level: state/level enum
 *   - recommendation / decision / next_action: action advice
 *   - thresholds_breached / blockers / open_required_items: why
 */
const KEY_FIELDS = [
  'verdict', 'risk_passed', 'overfit_passed',
  'risk_rating', 'status', 'level',
  'recommendation', 'decision', 'next_action',
  'thresholds_breached', 'blockers', 'open_required_items',
]

/** Chinese label overrides for the key fields. */
const KEY_FIELD_LABELS: Record<string, string> = {
  verdict: '结论',
  risk_passed: '是否通过',
  overfit_passed: '是否过拟合',
  risk_rating: '风险评级',
  status: '状态',
  level: '等级',
  recommendation: '建议',
  decision: '决策',
  next_action: '下一步',
  thresholds_breached: '触发规则',
  blockers: '阻塞原因',
  open_required_items: '待办项',
}

function pickHypothesis(json: Record<string, unknown>): string | undefined {
  for (const key of HYPOTHESIS_LIKE) {
    const v = json[key]
    if (typeof v === 'string' && v.trim().length > 0) return v
  }
  return undefined
}

function renderValue(v: unknown, hints?: FieldHints | null): React.ReactNode {
  if (typeof v === 'boolean') {
    return v
      ? <span className="text-success-fg font-medium">✓</span>
      : <span className="text-danger-fg font-medium">✗</span>
  }
  if (typeof v === 'number') {
    // Schema format hint: percentage (0.25 → "25.0%")
    if (hints?.format === 'percentage') {
      return (
        <span className="font-mono text-foreground">
          {(v * 100).toFixed(1)}%
        </span>
      )
    }
    return <span className="font-mono text-foreground">{String(v)}</span>
  }
  if (typeof v === 'string') {
    // Schema enum mapping: display label takes precedence over raw value
    if (hints?.enum_values && v in hints.enum_values) {
      return <span className="text-foreground">{hints.enum_values[v]}</span>
    }
    const s = v
    if (s.length <= 80) {
      return <span className="text-foreground">{s}</span>
    }
    // Long string: show inline, full text on hover
    return (
      <span className="text-foreground" title={s}>
        {s.slice(0, 200)}
        {s.length > 200 ? '…' : ''}
      </span>
    )
  }
  if (Array.isArray(v)) {
    if (v.length === 0) return <span className="text-muted">[]</span>
    // For small string arrays, join; otherwise render as compact list
    const allStrings = v.every((x) => typeof x === 'string')
    if (allStrings && v.length <= 4) {
      const totalLen = v.reduce((s, x) => s + (x as string).length, 0)
      if (totalLen <= 200) {
        return <span className="text-foreground">{(v as string[]).join(' · ')}</span>
      }
    }
    return (
      <span className="text-secondary font-mono text-[11px]">
        [{v.length} 项]
      </span>
    )
  }
  if (v && typeof v === 'object') {
    return <span className="text-muted font-mono">{'{…}'}</span>
  }
  return <span className="text-muted">{String(v ?? 'null')}</span>
}

export function JsonActionCard({ action, hypothesis, fullJson, agentId }: JsonActionCardProps) {
  const [expanded, setExpanded] = useState(false)
  const isActionMode = !!action && action in ACTION_STYLES
  const style = isActionMode
    ? ACTION_STYLES[action!]
    : (action ? { bg: 'bg-surface', text: 'text-secondary', label: action } : GENERIC_STYLE)
  const hyp = hypothesis ?? (isActionMode ? undefined : pickHypothesis(fullJson))

  // ── Field selection ─────────────────────────────────────────────
  // Schema-driven when a prompt-derived schema is loaded for this agent;
  // generic KEY_FIELDS heuristics otherwise.
  const schema = agentId ? getSchemaFor(agentId) : undefined
  const skipKeys = new Set(['action', 'hypothesis', ...HYPOTHESIS_LIKE])

  const keyEntries: Array<readonly [string, unknown, FieldHints | null]> = []
  const extraFields: Array<string> = []
  const labelFor = (k: string, h: FieldHints | null | undefined): string =>
    h?.label ?? KEY_FIELD_LABELS[k] ?? k

  if (schema) {
    const seen = new Set<string>()
    // 1. Schema-declared fields, in schema order
    for (const k of schema.fields) {
      if (!(k in fullJson) || skipKeys.has(k)) continue
      seen.add(k)
      const h = schema.field_hints[k] ?? null
      if (h?.core) keyEntries.push([k, fullJson[k], h] as const)
      else extraFields.push(k)
    }
    // 2. Runtime fields beyond the prompt schema (agent output evolved):
    //    fall back to the generic heuristics for core/label.
    for (const k of Object.keys(fullJson)) {
      if (seen.has(k) || skipKeys.has(k)) continue
      if (KEY_FIELDS.includes(k)) keyEntries.push([k, fullJson[k], null] as const)
      else extraFields.push(k)
    }
  } else {
    for (const k of KEY_FIELDS) {
      if (k in fullJson) keyEntries.push([k, fullJson[k], null] as const)
    }
    for (const k of Object.keys(fullJson)) {
      if (!skipKeys.has(k) && !KEY_FIELDS.includes(k)) extraFields.push(k)
    }
  }

  return (
    <div className="my-1 rounded border border-subtle bg-surface text-[12px] leading-relaxed overflow-hidden">
      {/* Header — badge */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-strong/30">
        <span
          className={`inline-block rounded px-2 py-0.5 font-mono font-medium text-[11px] ${style.bg} ${style.text}`}
        >
          {action ?? 'json'}
        </span>
        {style.label && (
          <span className="text-[11px] text-muted">{style.label}</span>
        )}
      </div>

      {/* Hypothesis / highlighted field */}
      {hyp && (
        <div className="px-3 py-2 text-foreground whitespace-pre-wrap">
          {hyp}
        </div>
      )}

      {/* Core decision/status fields — always visible */}
      {keyEntries.length > 0 && (
        <div className="px-3 py-2 border-t border-strong/30 space-y-1.5">
          {keyEntries.map(([k, v, h]) => (
            <div key={k} className="flex items-baseline gap-2 leading-relaxed">
              <span className="shrink-0 text-[11px] text-muted min-w-20 text-right">
                {labelFor(k, h)}:
              </span>
              <div className="min-w-0 flex-1 break-words" title={h?.description ?? undefined}>
                {renderValue(v, h)}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Other fields — collapsible */}
      {extraFields.length > 0 && (
        <div className="border-t border-strong/30">
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="w-full flex items-center justify-between px-3 py-1.5 text-[11px] text-muted hover:text-secondary transition-colors"
          >
              <span>其他字段 ({extraFields.length})</span>
              <span className="text-[10px]">{expanded ? '▼' : '▶'}</span>
            </button>
            {expanded && (
              <div className="px-3 pb-2">
                <pre className="text-[11px] text-muted font-mono whitespace-pre-wrap break-words">
                  {JSON.stringify(
                    Object.fromEntries(extraFields.map((k) => [k, fullJson[k]])),
                    null,
                    2,
                  )}
                </pre>
              </div>
            )}
        </div>
      )}
    </div>
  )
}

// ── Content parser ──────────────────────────────────────────────

export interface ParsedSegment {
  kind: 'text' | 'json'
  /** For kind=text: markdown text. */
  text?: string
  /** For kind=json: parsed object + optional action key. */
  json?: Record<string, unknown>
  action?: string
}

export interface ParseResult {
  segments: ParsedSegment[]
  /** True if any segment is a JSON card (action / generic). */
  hasStructured: boolean
}

/**
 * Parse a text part into an ordered list of markdown text segments and
 * JSON object segments.  Handles three input shapes:
 *   1. Pure markdown — single text segment
 *   2. Pure JSON object — single JSON segment (action or generic)
 *   3. Markdown with embedded JSON — multiple segments interleaved
 *
 * Returns { segments, hasStructured }.  Malformed JSON is treated as plain
 * text (no crash, no card).
 */
export function parseStructuredContent(rawText: string): ParseResult {
  const text = rawText.trim()
  if (!text) return { segments: [], hasStructured: false }

  // Fast path: pure JSON object (starts with { ends with })
  if (text.startsWith('{') && text.endsWith('}')) {
    const obj = tryParseJson(text)
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
      const jsonObj = obj as Record<string, unknown>
      return {
        segments: [{
          kind: 'json',
          json: jsonObj,
          action: typeof jsonObj.action === 'string' ? jsonObj.action : undefined,
        }],
        hasStructured: true,
      }
    }
  }

  // Mixed / markdown: scan top-level JSON blocks via bracket pairing.
  const segments: ParsedSegment[] = []
  let cursor = 0
  let foundAnyJson = false

  while (cursor < text.length) {
    const startIdx = text.indexOf('{', cursor)
    if (startIdx === -1) {
      // No more JSON — push remaining as text
      segments.push({ kind: 'text', text: text.slice(cursor) })
      break
    }
    // Push any markdown before the JSON
    if (startIdx > cursor) {
      segments.push({ kind: 'text', text: text.slice(cursor, startIdx) })
    }
    // Find balanced end
    const endIdx = findBalancedJsonEnd(text, startIdx)
    if (endIdx === -1) {
      // Unbalanced — treat remaining as text
      segments.push({ kind: 'text', text: text.slice(startIdx) })
      break
    }
    const candidate = text.slice(startIdx, endIdx + 1)
    const obj = tryParseJson(candidate)
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
      const jsonObj = obj as Record<string, unknown>
      segments.push({
        kind: 'json',
        json: jsonObj,
        action: typeof jsonObj.action === 'string' ? jsonObj.action : undefined,
      })
      foundAnyJson = true
      cursor = endIdx + 1
    } else {
      // Malformed JSON — skip just this character, treat rest as text
      cursor = startIdx + 1
    }
  }

  // Drop empty text segments (whitespace between JSON blocks).
  return {
    segments: segments.filter(
      (s) => s.kind === 'json' || (s.text && s.text.trim().length > 0),
    ),
    hasStructured: foundAnyJson,
  }
}

function tryParseJson(s: string): unknown {
  try {
    return JSON.parse(s)
  } catch {
    return null
  }
}

/**
 * Given text and a start index pointing at '{', find the index of the
 * matching '}' via depth counting (respecting strings).  Returns -1 if
 * unbalanced.
 */
function findBalancedJsonEnd(text: string, startIdx: number): number {
  let depth = 0
  let i = startIdx
  let inString = false
  let escape = false
  while (i < text.length) {
    const ch = text[i]
    if (inString) {
      if (escape) {
        escape = false
      } else if (ch === '\\') {
        escape = true
      } else if (ch === '"') {
        inString = false
      }
    } else {
      if (ch === '"') {
        inString = true
      } else if (ch === '{') {
        depth += 1
      } else if (ch === '}') {
        depth -= 1
        if (depth === 0) return i
      }
    }
    i += 1
  }
  return -1
}