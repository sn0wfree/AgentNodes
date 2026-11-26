import { useEffect, useState } from 'react'
import { X, FileText, GitCompare, RotateCcw, Loader2, AlertTriangle, Check } from 'lucide-react'
import { api } from '../../api/client'
import type {
  StudyRoundSummary,
  StudyRoundManifestResponse,
  StudyRoundArtifactsResponse,
  StudyRoundDiffResponse,
  StudyRoundSummaryMdResponse,
  StudyAdoptResponse,
} from '../../api/client'

interface Props {
  studyId: string
  round: StudyRoundSummary
  onClose: () => void
  onAdopted?: (note: string) => void
}

function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${bytes} B`
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted">
        {title}
      </div>
      {children}
    </div>
  )
}

export function RoundDetailDrawer({ studyId, round, onClose, onAdopted }: Props) {
  const [manifest, setManifest] = useState<StudyRoundManifestResponse | null>(null)
  const [artifacts, setArtifacts] = useState<StudyRoundArtifactsResponse | null>(null)
  const [summaryMd, setSummaryMd] = useState<StudyRoundSummaryMdResponse | null>(null)
  const [diff, setDiff] = useState<StudyRoundDiffResponse | null>(null)
  const [diffFailed, setDiffFailed] = useState(false)
  const [diffAgainst, setDiffAgainst] = useState(0)
  const [adopting, setAdopting] = useState(false)
  const [redoing, setRedoing] = useState(false)
  const [adopted, setAdopted] = useState<StudyAdoptResponse | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const [m, a, s] = await Promise.all([
          api.study.roundManifest(studyId, round.round_num).catch(() => null),
          api.study.roundArtifacts(studyId, round.round_num).catch(() => null),
          api.study.roundSummaryMd(studyId, round.round_num).catch(() => null),
        ])
        if (cancelled) return
        setManifest(m)
        setArtifacts(a)
        setSummaryMd(s)
      } catch {
        if (!cancelled) setError('加载轮次详情失败')
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [studyId, round.round_num])

  useEffect(() => {
    let cancelled = false
    if (typeof api.study?.roundDiff !== 'function') return
    api.study
      .roundDiff(studyId, round.round_num, diffAgainst)
      .then((d) => {
        if (!cancelled) setDiff(d)
        if (!cancelled) setDiffFailed(!d)
      })
      .catch(() => {
        if (!cancelled) {
          setDiff(null)
          setDiffFailed(true)
        }
      })
    return () => {
      cancelled = true
    }
  }, [studyId, round.round_num, diffAgainst])

  const m = manifest?.manifest as Record<string, any> | undefined
  const verdict = (m?.verdict as Record<string, string>) ?? {}
  const next = (m?.next as Record<string, any>) ?? {}
  const changes = Array.isArray(m?.strategy_changes) ? m.strategy_changes : []
  const hypothesis = (m?.hypothesis as Record<string, any>) ?? {}

  const onAdopt = async () => {
    setAdopting(true)
    setError('')
    try {
      const r = await api.study.adoptRound(studyId, round.round_num)
      setAdopted(r)
      onAdopted?.(r.note)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setAdopting(false)
    }
  }

  const onRedo = async () => {
    // Redo is destructive (wipes the round's artifacts + DB record) —
    // require explicit confirmation before firing.
    const ok = window.confirm(
      `重跑 R${round.round_num} 会删除该轮现有产物与记录，确定继续？`,
    )
    if (!ok) return
    setRedoing(true)
    setError('')
    try {
      const r = await api.study.redoRound(studyId, round.round_num)
      onAdopted?.(`已重排：${r.action} — 将重跑 R${round.round_num}`)
      onClose()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setRedoing(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative flex h-full w-full max-w-lg flex-col border-l border-strong bg-slate-900 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-subtle px-4 py-3">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-primary-400" />
            <span className="text-sm font-semibold text-foreground">
              R{round.round_num} 详情
            </span>
            <span className="rounded-full border border-strong bg-surface-raised px-1.5 py-0.5 font-mono text-[10px] text-muted">
              {round.run_name}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer rounded-lg p-1.5 text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {error && (
            <div className="rounded-lg border border-rose-800 bg-rose-950/50 px-2.5 py-1.5 text-[11px] text-rose-300">
              {error}
            </div>
          )}

          {/* Verdict + error */}
          <Section title="结论">
            <div className="flex items-center gap-2">
              <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-success-fg">
                {round.verdict}
              </span>
              {verdict.reason && (
                <span className="text-[11px] text-muted">{verdict.reason}</span>
              )}
            </div>
          </Section>

          {/* Hypothesis */}
          {hypothesis.text && (
            <Section title="假设">
              <p className="rounded-lg border border-subtle bg-slate-950/60 px-2.5 py-1.5 text-[11px] text-secondary">
                {hypothesis.text}
              </p>
            </Section>
          )}

          {/* Strategy changes */}
          {changes.length > 0 && (
            <Section title="本轮改动">
              <div className="space-y-1">
                {changes.map((c: Record<string, unknown>, i: number) => (
                  <div
                    key={i}
                    className="flex items-start gap-1.5 rounded-lg border border-subtle bg-slate-950/60 px-2.5 py-1.5 text-[11px] text-secondary"
                  >
                    <GitCompare className="mt-0.5 h-3 w-3 flex-shrink-0 text-info-fg" />
                    <span>{String(c.what ?? c.factor ?? c.param ?? JSON.stringify(c))}</span>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {/* Next suggestions */}
          {(next.suggested_focus || (next.open_questions ?? []).length > 0) && (
            <Section title="下一步建议">
              <div className="space-y-1">
                {next.suggested_focus && (
                  <div className="rounded-lg border border-sky-800/40 bg-sky-950/30 px-2.5 py-1.5 text-[11px] text-sky-300">
                    {next.suggested_focus}
                  </div>
                )}
                {(next.open_questions ?? []).map((q: string, i: number) => (
                  <div key={i} className="text-[11px] text-muted">
                    ? {q}
                  </div>
                ))}
              </div>
            </Section>
          )}

          {/* Diff */}
          <Section title={`Diff · R${round.round_num} vs baseline`}>
            <div className="mb-1.5 flex items-center gap-1.5">
              <select
                value={diffAgainst}
                onChange={(e) => setDiffAgainst(Number(e.target.value))}
                className="rounded-lg border border-strong bg-slate-950 px-2 py-1 text-[11px] text-secondary outline-none"
              >
                <option value={0}>vs baseline</option>
                {Array.from({ length: Math.max(0, round.round_num - 1) }, (_, i) => {
                  const n = i + 1
                  return (
                    <option key={n} value={n}>
                      vs R{n}
                    </option>
                  )
                })}
              </select>
              {diff?.stats && (
                <span className="text-[10px] text-muted">
                  +{diff.stats.adds} / -{diff.stats.dels} / {diff.stats.context} ctx
                </span>
              )}
            </div>
            {diff ? (
              <div className="max-h-48 overflow-y-auto rounded-lg border border-subtle bg-slate-950/80 font-mono text-[10px] leading-relaxed">
                {(diff.diff ?? []).map((d, i) => (
                  <div
                    key={i}
                    className={
                      d.kind === 'add'
                        ? 'bg-emerald-950/40 text-emerald-400 px-2'
                        : d.kind === 'del'
                          ? 'bg-rose-950/40 text-rose-400 px-2'
                          : 'px-2 text-muted'
                    }
                  >
                    {d.kind === 'add' ? '+ ' : d.kind === 'del' ? '- ' : '  '}
                    {d.line}
                  </div>
                ))}
              </div>
            ) : diffFailed ? (
              <p className="text-[11px] text-danger-fg">加载 diff 失败</p>
            ) : (
              <p className="text-[11px] text-muted">加载 diff...</p>
            )}
          </Section>

          {/* Summary markdown */}
          {summaryMd?.summary_md && (
            <Section title="Summary.md">
              <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg border border-subtle bg-slate-950/60 px-2.5 py-1.5 text-[10px] leading-relaxed text-muted">
                {summaryMd.summary_md}
              </pre>
            </Section>
          )}

          {/* Artifacts */}
          {artifacts && (artifacts.artifacts?.length ?? 0) > 0 && (
            <Section title={`产物 · ${artifacts.artifacts.length} 个文件`}>
              <div className="max-h-40 space-y-1 overflow-y-auto">
                {(artifacts.artifacts ?? []).map((a) => (
                  <div
                    key={a.path}
                    className="flex items-center justify-between gap-2 rounded-lg bg-slate-950/60 px-2 py-1 text-[10px]"
                  >
                    <span className="min-w-0 truncate font-mono text-muted" title={a.path}>
                      {a.path}
                    </span>
                    <span className="flex-shrink-0 font-mono text-muted">{fmtSize(a.size)}</span>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {/* Redo this round */}
          <Section title="重跑本轮">
            <button
              type="button"
              onClick={onRedo}
              disabled={redoing}
              className="inline-flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-rose-700/50 bg-rose-950/30 px-2.5 py-2 text-[11px] font-medium text-rose-300 transition-colors hover:bg-rose-900/40 disabled:opacity-50"
            >
              {redoing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RotateCcw className="h-3.5 w-3.5" />
              )}
              丢弃 R{round.round_num} 并从上一轮重跑
            </button>
            <div className="mt-1 flex items-start gap-1 text-[10px] text-muted">
              <AlertTriangle className="mt-0.5 h-3 w-3 flex-shrink-0" />
              破坏性：删除本轮产物 + DB 记录；study 运行中不可用
            </div>
          </Section>

          {/* Adopt */}
          <Section title="采用本轮策略">
            {adopted ? (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-700/40 bg-emerald-950/30 px-2.5 py-2 text-[11px] text-emerald-400">
                <Check className="h-3.5 w-3.5 flex-shrink-0" />
                {adopted.note}
              </div>
            ) : (
              <button
                type="button"
                onClick={onAdopt}
                disabled={adopting}
                className="inline-flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-amber-700/50 bg-amber-950/30 px-2.5 py-2 text-[11px] font-medium text-amber-300 transition-colors hover:bg-amber-900/40 disabled:opacity-50"
              >
                {adopting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RotateCcw className="h-3.5 w-3.5" />
                )}
                将 R{round.round_num} 的策略设为下一轮起点
              </button>
            )}
            <div className="mt-1 flex items-start gap-1 text-[10px] text-muted">
              <AlertTriangle className="mt-0.5 h-3 w-3 flex-shrink-0" />
              非破坏性：只复制到本 study 的 baseline，不影响共享策略目录
            </div>
          </Section>
        </div>
      </div>
    </div>
  )
}
