import { useState } from 'react'
import * as Tabs from '@radix-ui/react-tabs'
import { Network, Bot, Zap, CheckCircle, Clock, Moon, Sun, PenTool } from 'lucide-react'
import { IconNav } from '../components/layout/IconNav'
import { AgentItem } from '../components/agent/AgentItem'
import { useAgentStore } from '../stores/agents'
import { useThemeStore } from '../stores/theme'
import { DefinitionWorkflowPage } from '../components/workflow/DefinitionWorkflowPage'

export function DAGPage() {
  const [tab, setTab] = useState('edit')
  const theme = useThemeStore((s) => s.theme)
  const toggleTheme = useThemeStore((s) => s.toggleTheme)

  const agents = useAgentStore((s) => s.agents)
  const agentList = Array.from(agents.values()).sort(
    (a, b) => b.created_at - a.created_at,
  )
  const runningCount = agentList.filter((a) => a.status === 'running').length
  const completedCount = agentList.filter(
    (a) => a.status === 'completed' || a.status === 'failed',
  ).length
  const pendingCount = agentList.filter((a) => a.status === 'pending').length

  return (
    <div className="relative flex h-screen overflow-hidden bg-app">
      <div>
        <div className="grid-layer" />
        <div />
        <div className="vignette-layer" />
        <div className="grain-layer" />
      </div>

      <div className="relative z-10 flex h-full w-full overflow-hidden">
        <IconNav />
        <div className="flex flex-1 flex-col overflow-hidden">
          <header className="glass flex h-12 flex-shrink-0 items-center justify-between border-b border-subtle px-4">
            <div className="flex min-w-0 items-center gap-2.5">
              <span className="text-primary-400"><Network className="h-4 w-4" /></span>
              <h1 className="truncate text-sm font-semibold tracking-tight text-foreground">编排</h1>
              <span className="hidden truncate font-mono text-[10px] text-muted md:inline">
                工作流设计 · 运行 · 历史 · Agent 监控
              </span>
            </div>
            <button
              onClick={toggleTheme}
              title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
              className="flex items-center gap-1.5 rounded-lg border border-strong bg-surface px-2.5 py-1.5 text-xs text-muted transition-colors hover:border-strong hover:text-secondary"
            >
              {theme === 'dark' ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
            </button>
          </header>

          <Tabs.Root value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
            <Tabs.List className="wf-panel flex flex-shrink-0 border-b px-4">
              <Tabs.Trigger
                value="edit"
                className="flex items-center gap-2 border-b-2 border-transparent px-5 py-2.5 wf-text-sub text-sm transition-colors
                  data-[state=active]:border-primary-500 data-[state=active]:text-foreground"
              >
                <PenTool className="h-4 w-4" />
                编排
              </Tabs.Trigger>
              <Tabs.Trigger
                value="agent"
                className="flex items-center gap-2 border-b-2 border-transparent px-5 py-2.5 wf-text-sub text-sm transition-colors
                  data-[state=active]:border-primary-500 data-[state=active]:text-foreground"
              >
                <Bot className="h-4 w-4" />
                Agent 监控
              </Tabs.Trigger>
            </Tabs.List>

            <Tabs.Content value="edit" className="min-h-0 flex-1 overflow-hidden">
              <DefinitionWorkflowPage />
            </Tabs.Content>

            <Tabs.Content value="agent" className="min-h-0 flex-1 overflow-y-auto">
              <div className="mx-auto max-w-[1440px] px-6 py-5">
                {/* Summary row */}
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <SummaryStat
                    icon={<Bot className="h-4 w-4" />}
                    label="Agent 总数"
                    value={String(agentList.length)}
                    color="text-info-fg"
                    bg="border border-info-fg/30 bg-info-surface text-info-fg"
                  />
                  <SummaryStat
                    icon={<Zap className="h-4 w-4" />}
                    label="运行中"
                    value={String(runningCount)}
                    color="text-warning-fg"
                    bg="border border-warning/30 bg-warning-fill text-warning-fg"
                  />
                  <SummaryStat
                    icon={<CheckCircle className="h-4 w-4" />}
                    label="已完成"
                    value={String(completedCount)}
                    color="text-success-fg"
                    bg="border border-success-fg-muted bg-success-surface text-success-fg"
                  />
                  <SummaryStat
                    icon={<Clock className="h-4 w-4" />}
                    label="待启动"
                    value={String(pendingCount)}
                    color="text-muted"
                    bg="border border-strong bg-slate-500/10 text-muted"
                  />
                </div>

                {agentList.length === 0 ? (
                  <div className="mt-8 rounded-xl border border-dashed border-subtle px-4 py-12 text-center">
                    <Bot className="mx-auto h-10 w-10 text-muted" />
                    <p className="wf-text-sub mt-3 text-sm">暂无 Agent</p>
                    <p className="wf-text-sub mt-1 text-xs">
                      Agent 会在聊天或编排任务执行时自动创建
                    </p>
                  </div>
                ) : (
                  <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                    {agentList.map((agent) => (
                      <AgentItem key={agent.id} agent={agent} />
                    ))}
                  </div>
                )}
              </div>
            </Tabs.Content>
          </Tabs.Root>
        </div>
      </div>
    </div>
  )
}

function SummaryStat({
  icon,
  label,
  value,
  color,
  bg,
}: {
  icon: React.ReactNode
  label: string
  value: string
  color: string
  bg: string
}) {
  return (
    <div className="wf-panel flex items-center gap-3 rounded-xl border px-4 py-3 shadow-soft transition-colors hover:border-strong">
      <div className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg ${bg}`}>
        {icon}
      </div>
      <div>
        <div className={`font-mono text-xl font-bold tabular-nums ${color}`}>{value}</div>
        <div className="wf-text-sub text-[10px]">{label}</div>
      </div>
    </div>
  )
}
