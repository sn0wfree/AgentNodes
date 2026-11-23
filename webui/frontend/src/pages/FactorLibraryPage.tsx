import { useMemo, useState } from 'react'
import { Sigma, Search, X } from 'lucide-react'
import { PageShell } from '../components/layout/PageShell'
import { FACTOR_ZOOS, FACTORS } from '../data/factorMock'

const ZOO_TOTAL = FACTOR_ZOOS.reduce((s, z) => s + z.count, 0)

export function FactorLibraryPage() {
  const [zoo, setZoo] = useState('alpha101')
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return FACTORS.filter((f) => {
      if (f.zoo !== zoo) return false
      if (!q) return true
      const haystack = [
        f.id,
        f.nickname,
        f.formula_latex,
        ...f.theme,
        ...f.columns_required,
      ].join(' ').toLowerCase()
      return haystack.includes(q)
    })
  }, [zoo, query])

  return (
    <PageShell
      title="因子库"
      subtitle={`mock 数据 · 共 ${ZOO_TOTAL} 个因子`}
      icon={<Sigma className="h-4 w-4" />}
      stickyBar={
        <div className="mx-auto w-full max-w-[1440px] px-6 py-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex flex-wrap gap-1.5">
              {FACTOR_ZOOS.map((z) => (
                <button
                  key={z.id}
                  onClick={() => setZoo(z.id)}
                  className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                    zoo === z.id
                      ? 'border-primary-500/50 bg-primary-500/10 text-primary-300'
                      : 'border-slate-700 bg-slate-900 text-slate-400 hover:border-slate-600 hover:text-slate-200'
                  }`}
                >
                  {z.label}
                  <span className="ml-1 font-mono text-[9px] opacity-60">{z.count}</span>
                </button>
              ))}
            </div>
            <div className="relative ml-auto min-w-[200px] flex-1 sm:flex-none sm:w-80">
              <Search className="absolute left-2.5 top-1/2 z-10 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索 id / 名称 / 公式 / 主题…"
                className="relative z-10 w-full rounded-lg border border-slate-700 bg-slate-900 py-1.5 pl-8 pr-8 text-xs text-slate-200 outline-none placeholder:text-slate-600"
              />
              {query && (
                <button
                  onClick={() => setQuery('')}
                  className="absolute right-2 top-1/2 z-10 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                  aria-label="清空搜索"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        </div>
      }
    >
      {filtered.length === 0 ? (
        <div className="py-16 text-center text-sm text-slate-500">没有匹配的因子</div>
      ) : (
        <div className="grid grid-cols-1 gap-2.5 xl:grid-cols-2">
          {filtered.map((f) => (
            <div
              key={f.id}
              className="cursor-pointer rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3 shadow-soft transition-all hover:-translate-y-px hover:border-primary-500/40 hover:shadow-elevated"
            >
              <div className="flex items-center gap-2.5">
                <span className="font-mono text-xs font-semibold text-primary-300">
                  {f.id}
                </span>
                <span className="truncate text-xs text-slate-200">{f.nickname}</span>
                <div className="flex-1" />
                {f.frequency.map((fq) => (
                  <span
                    key={fq}
                    className="rounded-full border border-slate-700 px-2 py-0.5 font-mono text-[9px] text-slate-400"
                  >
                    {fq}
                  </span>
                ))}
              </div>
              <div className="mt-2 rounded-md border border-slate-800 bg-slate-950/60 px-3 py-2 font-mono text-[10.5px] leading-relaxed text-slate-400">
                {f.formula_latex}
              </div>
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {f.theme.map((t) => (
                  <span
                    key={t}
                    className="rounded-full border border-primary-500/30 bg-primary-500/10 px-2 py-0.5 text-[9px] text-primary-300"
                  >
                    {t}
                  </span>
                ))}
                {f.columns_required.length > 0 && (
                  <span className="rounded-full border border-slate-700 px-2 py-0.5 font-mono text-[9px] text-slate-500">
                    {f.columns_required.join(', ')}
                  </span>
                )}
                {f.universe.length > 0 && (
                  <span className="rounded-full border border-slate-700 px-2 py-0.5 font-mono text-[9px] text-slate-500">
                    {f.universe.join(', ')}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </PageShell>
  )
}
