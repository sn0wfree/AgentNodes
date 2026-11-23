import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Activity, Bot, Target, ChartLine, HeartPulse, Zap,
  MessageSquare, Sigma, Layers, ArrowRight, Cpu, FolderOpen,
} from 'lucide-react'
import { PageShell } from '../components/layout/PageShell'
import { api, type RunListItem, type StudySummary } from '../api/client'
import { useSystemStore } from '../stores/system'
import { useSessionStore } from '../stores/session'
import { useAgentStore } from '../stores/agents'
import { statusLabel } from '../utils/status'

const ACTIVE_STATUSES = ['running', 'queued', 'monitoring']

function StatCard({
  icon,
  iconCls,
  value,
  label,
  valueCls = 'text-slate-100',
}: {
  icon: React.ReactNode
  iconCls: string
  value: string
  label: string
  valueCls?: string
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3.5 shadow-soft transition-colors hover:border-slate-700">
      <div className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg ${iconCls}`}>{icon}</div>
      <div className="min-w-0">
        <div className={`font-mono text-xl font-bold tabular-nums ${valueCls}`}>{value}</div>
        <div className="text-[10px] text-slate-500">{label}</div>
      </div>
    </div>
  )
}

function QuickEntry({
  to,
  icon,
  iconCls,
  title,
  desc,
}: {
  to: string
  icon: React.ReactNode
  iconCls: string
  title: string
  desc: string
}) {
  const navigate = useNavigate()
  return (
    <button
      onClick={() => navigate(to)}
      className="group flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3.5 text-left shadow-soft transition-all hover:-translate-y-0.5 hover:border-primary-500/40 hover:shadow-elevated"
    >
      <div className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl ${iconCls}`}>
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-slate-100">{title}</div>
        <div className="truncate text-[10px] text-slate-500">{desc}</div>
      </div>
      <ArrowRight className="h-4 w-4 flex-shrink-0 text-slate-600 transition-colors group-hover:text-primary-400" />
    </button>
  )
}

function ShimmerCard() {
  return <div className="h-[74px] rounded-xl border border-slate-800/50" />
}

export function MonitorPage() {
  const navigate = useNavigate()
  const workspacePath = useSystemStore((s) => s.workspacePath)
  const llm = useSystemStore((s) => s.llm)
  const sessionId = useSessionStore((s) => s.currentSessionId)
  const agents = useAgentStore((s) => s.agents)

  const [studies, setStudies] = useState<StudySummary[]>([])
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [studyError, setStudyError] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const requestId = useRef(0)

  const load = useCallback(async () => {
    const currentRequest = ++requestId.current
    if (!workspacePath) {
      setLoading(false)
      setError('工作区尚未就绪')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [studyRes, runRes] = await Promise.allSettled([
        sessionId ? api.study.list({ session_id: sessionId, limit: 20 }) : Promise.resolve(null),
        api.run.list(workspacePath, '', 10),
      ])
      if (currentRequest !== requestId.current) return
      setStudies(studyRes.status === 'fulfilled' && studyRes.value ? studyRes.value.studies : [])
      setRuns(runRes.status === 'fulfilled' ? runRes.value.runs : [])
      setStudyError(studyRes.status === 'rejected' ? String(studyRes.reason instanceof Error ? studyRes.reason.message : studyRes.reason) : null)
      setRunError(runRes.status === 'rejected' ? String(runRes.reason instanceof Error ? runRes.reason.message : runRes.reason) : null)
    } catch (e) {
      if (currentRequest === requestId.current) {
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      if (currentRequest === requestId.current) setLoading(false)
    }
  }, [workspacePath, sessionId])

  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(), 15000)
    return () => {
      clearInterval(timer)
      requestId.current += 1
    }
  }, [load])

  const agentList = Array.from(agents.values()).sort((a, b) => b.created_at - a.created_at)
  const runningAgents = agentList.filter((a) => a.status === 'running')
  const activeStudies = studies.filter((s) => ACTIVE_STATUSES.includes(s.execution_status))

  const fmtMetric = (metrics: Record<string, number | string> | null | undefined, key: string) => {
    const v = metrics?.[key]
    if (v === undefined || v === null) return '—'
    if (typeof v === 'number') {
      if (key === 'total_return' || key === 'max_drawdown') return (v * 100).toFixed(1) + '%'
      return v.toFixed(2)
    }
    return String(v)
  }

  return (
    <PageShell
      title="监控"
      subtitle="聚合总览 · 15s 自动刷新"
      icon={<Activity className="h-4 w-4" />}
    >
      {/* KPI band */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          icon={<Target className="h-4 w-4" />}
          iconCls="border border-sky-500/30 bg-sky-500/10 text-sky-400"
          value={loading ? '…' : error || studyError || !sessionId ? '—' : String(activeStudies.length)}
          label="活跃研究"
          valueCls="text-sky-400"
        />
        <StatCard
          icon={<Zap className="h-4 w-4" />}
          iconCls="border-warning/30 bg-warning-fill text-warning-fg"
          value={String(runningAgents.length)}
          label="运行中 Agent"
          valueCls="text-warning-fg"
        />
        <StatCard
          icon={<ChartLine className="h-4 w-4" />}
          iconCls="border border-primary-500/30 bg-primary-500/10 text-primary-400"
          value={loading ? '…' : error || runError ? '—' : String(runs.length)}
          label="最近 Run"
          valueCls="text-primary-400"
        />
        <StatCard
          icon={<HeartPulse className="h-4 w-4" />}
          iconCls="border border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
          value={loading ? '加载中' : error || studyError || runError ? '异常' : '完成'}
          label="数据加载"
          valueCls={error || studyError || runError ? 'text-red-400' : 'text-emerald-400'}
        />
      </div>

      {/* Status strip */}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-slate-500">
          <span className="inline-flex items-center gap-1.5">
            <Cpu className="h-3 w-3 text-slate-600" />
            LLM: <span className="font-mono text-slate-300">{llm.provider || '—'} / {llm.model || '—'}</span>
          </span>
          {workspacePath && (
            <span className="inline-flex items-center gap-1.5">
              <FolderOpen className="h-3 w-3 text-slate-600" />
              <span className="font-mono text-slate-300">{workspacePath}</span>
            </span>
          )}
          {error && (
            <span className="text-red-400">加载异常: {error}</span>
          )}
        </div>
      </div>

      {/* Quick entries */}
      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <QuickEntry
          to="/chat"
          icon={<MessageSquare className="h-5 w-5" />}
          iconCls="border border-primary-500/30 bg-primary-500/10 text-primary-400"
          title="Chat"
          desc="会话与消息"
        />
        <QuickEntry
          to="/trace"
          icon={<Activity className="h-5 w-5" />}
          iconCls="border border-sky-500/30 bg-sky-500/10 text-sky-400"
          title="Trace"
          desc="历史运行回放"
        />
        <QuickEntry
          to="/factors"
          icon={<Sigma className="h-5 w-5" />}
          iconCls="border border-violet-500/30 bg-violet-500/10 text-violet-400"
          title="因子库"
          desc="305 个因子目录"
        />
        <QuickEntry
          to="/strategies"
          icon={<Layers className="h-5 w-5" />}
          iconCls="border-slate-600/40 bg-slate-700/20 text-slate-300"
          title="策略库"
          desc="策略 + 研究记录"
        />
      </div>

      {/* Two-column: active studies / recent runs */}
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        {/* Active studies */}
        <section>
          <h2 className="mb-2.5 flex items-center gap-2 text-xs font-semibold text-slate-500">
            <Activity className="h-3.5 w-3.5 text-red-400" />
            进行中的研究
          </h2>
          {loading ? (
            <div className="space-y-2">
              <ShimmerCard /><ShimmerCard />
            </div>
          ) : error || studyError ? (
            <div role="alert" className="rounded-xl border border-red-500/30 px-4 py-8 text-center text-xs text-red-400">
              研究数据加载失败：{error || studyError}
            </div>
          ) : !sessionId ? (
            <div className="rounded-xl border border-dashed border-slate-800 px-4 py-8 text-center text-xs text-slate-600">
              请选择会话以查看研究
            </div>
          ) : activeStudies.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-800 px-4 py-8 text-center text-xs text-slate-600">
              暂无进行中的研究 —— 去 <span className="cursor-pointer text-primary-400 hover:underline" onClick={() => navigate('/chat')}>Chat</span> 发起一个吧
            </div>
          ) : (
            <div className="space-y-2">
              {activeStudies.map((s) => (
                <div
                  key={s.study_id}
                  className="flex cursor-pointer items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3 shadow-soft transition-all hover:translate-x-0.5 hover:border-slate-700 hover:shadow-elevated"
                  onClick={() => navigate(`/study/${s.study_id}`)}
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-slate-200">
                      {s.objective || '未命名研究'}
                      <span className="ml-1.5 font-mono text-[10px] text-primary-300">{s.strategy_name}</span>
                    </div>
                    <div className="mt-0.5 font-mono text-[10px] text-slate-500">
                      轮次 {s.current_round ?? 0} · {s.updated_at ? new Date(s.updated_at).toLocaleTimeString() : '—'}
                    </div>
                  </div>
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-[9px] text-emerald-400">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                    {s.execution_status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Recent runs */}
        <section>
          <h2 className="mb-2.5 flex items-center gap-2 text-xs font-semibold text-slate-500">
            <ChartLine className="h-3.5 w-3.5" />
            最近回测运行
            <span className="ml-auto font-mono font-normal text-slate-600">/api/run/list</span>
          </h2>
          {loading ? (
            <div className="space-y-2">
              <ShimmerCard /><ShimmerCard /><ShimmerCard />
            </div>
          ) : error || runError ? (
            <div role="alert" className="rounded-xl border border-red-500/30 px-4 py-8 text-center text-xs text-red-400">
              运行数据加载失败：{error || runError}
            </div>
          ) : runs.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-800 px-4 py-8 text-center text-xs text-slate-600">
              暂无运行记录
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border border-slate-800">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-800 bg-slate-900/80 text-left text-[10px] text-slate-500">
                    <th className="px-3 py-2.5 font-medium">Run</th>
                    <th className="px-3 py-2.5 font-medium">总收益</th>
                    <th className="px-3 py-2.5 font-medium">夏普</th>
                    <th className="px-3 py-2.5 font-medium">回撤</th>
                    <th className="px-3 py-2.5 font-medium">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => {
                    const m = r.metrics
                    const tr = Number((m?.total_return as number | undefined) ?? 0)
                    return (
                      <tr
                        key={r.name}
                        className="cursor-pointer border-b border-slate-800/50 transition-colors last:border-b-0 hover:bg-slate-800/30"
                        onClick={() => navigate(`/run/*/${r.name}`)}
                      >
                        <td className="px-3 py-2.5 font-mono text-primary-300">{r.name}</td>
                        <td className={`px-3 py-2.5 font-mono tabular-nums ${tr >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {fmtMetric(m, 'total_return')}
                        </td>
                        <td className="px-3 py-2.5 font-mono tabular-nums text-sky-400">{fmtMetric(m, 'sharpe')}</td>
                        <td className="px-3 py-2.5 font-mono tabular-nums text-red-400">{fmtMetric(m, 'max_drawdown')}</td>
                        <td className="px-3 py-2.5">
                          <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[9px] text-emerald-400">
                            ✓
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      {/* Two-column: agents / system info */}
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        {/* Agent overview */}
        <section>
          <h2 className="mb-2.5 flex items-center gap-2 text-xs font-semibold text-slate-500">
            <Bot className="h-3.5 w-3.5" />
            Agent 概览
            <span className="ml-auto font-normal text-slate-600">SSE 实时</span>
          </h2>
          {agentList.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-800 px-4 py-8 text-center text-xs text-slate-600">
              暂无 Agent —— 运行编排任务后实时展示
            </div>
          ) : (
            <div className="space-y-2">
              {agentList.slice(0, 8).map((a) => (
                <div key={a.id} className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-2.5 shadow-soft">
                  <div className="relative flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full" style={{ backgroundColor: a.color || '#8b5cf6' }}>
                    <Bot className="h-4 w-4 text-white" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-slate-200">
                      {a.name || a.id.slice(0, 8)}
                    </div>
                    <div className="font-mono text-[10px] text-slate-500">
                      {a.iterations_detail.length} 轮迭代 · {Math.round(a.context_tokens / 1000)}k tokens
                    </div>
                  </div>
                  <span className={`text-[10px] ${a.status === 'running' ? 'text-warning-fg' : a.status === 'completed' ? 'text-emerald-400' : 'text-slate-500'}`}>
                    {statusLabel(a.status)}
                  </span>
                  {a.status === 'running' && <Zap className="h-3.5 w-3.5 animate-pulse text-warning-fg" />}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* System info */}
        <section>
          <h2 className="mb-2.5 flex items-center gap-2 text-xs font-semibold text-slate-500">
            <Cpu className="h-3.5 w-3.5" />
            系统信息
          </h2>
          <div className="overflow-hidden rounded-xl border border-slate-800">
            <table className="w-full text-xs">
              <tbody>
                {[
                  ['LLM 提供方', llm.provider || '—'],
                  ['模型', llm.model || '—'],
                  ['配置状态', llm.configured ? '已配置' : '未配置'],
                  ['Workspace', workspacePath || '—'],
                  // P10 假状态修复：监控页无 useSSE 订阅，移除 SSEStatus
                  // 以免显示永久误导性「已断开」——连接状态只在 /chat 提供。
                ].map(([k, v], i) => (
                  <tr key={String(k)} className={`border-b border-slate-800/50 last:border-b-0 ${i % 2 === 0 ? 'bg-slate-900/40' : 'bg-slate-900/20'}`}>
                    <td className="w-32 px-3 py-2.5 text-slate-500">{String(k)}</td>
                    <td className="px-3 py-2.5 text-slate-300">{v as React.ReactNode}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </PageShell>
  )
}
