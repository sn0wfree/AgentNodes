import { useEffect, useRef, useState } from 'react'
import {
  Search,
  Hammer,
  ChevronDown,
  Sparkles,
  Brain,
  Zap,
  Bot,
  AlertTriangle,
} from 'lucide-react'
import { useModeStore } from '../../stores/mode'
import { useModelStore } from '../../stores/model'
import { useThinkingPrefStore } from '../../stores/thinkingPref'
import { useSystemStore } from '../../stores/system'
import { usePersonaStore } from '../../stores/personas'
import { useChatStore } from '../../stores/chat'
import { useContextUsage } from '../../hooks/useContextUsage'
import { ContextRing, BreakdownList } from './ContextRing'
import type { ChatPersona } from '../../api/client'
import { tokenTone } from '../../utils/tokenThresholds'
import { formatTokens } from '../../utils/contextUsage'

/** 稳定空对象（避免 zustand selector 每次返回新引用导致无限重渲染）。 */
const EMPTY_SEGMENTS: Record<string, number> = {}

interface ComposerStatusBarProps {
  sessionId: string | null
}

const MODE_CONFIG = {
  plan: { icon: Search, label: 'Plan', color: 'text-thinking-fg', bg: 'bg-thinking-surface', border: 'border-thinking-fg-muted' },
  build: { icon: Hammer, label: 'Build', color: 'text-success-fg', bg: 'bg-success-surface', border: 'border-success-fg-muted' },
} as const

const THINKING_OPTIONS = [
  { value: 'off' as const, label: 'Off', icon: null },
  { value: 'on' as const, label: 'On', icon: Sparkles },
  { value: 'auto' as const, label: 'Auto', icon: Brain },
]

export function ComposerStatusBar({ sessionId }: ComposerStatusBarProps) {
  const mode = useModeStore((s) => s.mode)
  const toggleMode = useModeStore((s) => s.toggleMode)
  const providers = useModelStore((s) => s.providers)
  const sessionModels = useModelStore((s) => s.sessionModels)
  const setSessionModel = useModelStore((s) => s.setSessionModel)
  const clearSessionModel = useModelStore((s) => s.clearSessionModel)
  const loadProviders = useModelStore((s) => s.loadProviders)
  const thinkingMode = useThinkingPrefStore((s) => s.thinkingMode)
  const setThinkingMode = useThinkingPrefStore((s) => s.setThinkingMode)
  const modelInfo = useSystemStore((s) => s.modelInfo)
  // 上下文用量：统一口径（R-014 F4-b）——实测优先，无实测则用字符估算。
  const usage = useContextUsage(sessionId)
  // R-014 H1/H2：7 段构成（来自 context.breakdown SSE），供圆环 + hover 明细。
  // 注意：`?? EMPTY_SEGMENTS` 必须用稳定常量——若用 `?? {}` 每次渲染新对象，
  // zustand selector 会认为状态变化 → 无限重渲染（实测 Maximum update depth）。
  const breakdown = useChatStore((s) =>
    sessionId ? s.contextBreakdown.get(sessionId) ?? EMPTY_SEGMENTS : EMPTY_SEGMENTS,
  )
  const tone = tokenTone(usage.percent)
  // 人设（persona）选择器 —— 与模式/模型/思考同一排（R-014 F4）。
  const personas = usePersonaStore((s) => s.personas)
  const loadPersonas = usePersonaStore((s) => s.loadPersonas)
  const getSessionPersona = usePersonaStore((s) => s.getSessionPersona)
  const setSessionPersona = usePersonaStore((s) => s.setSessionPersona)

  const [modelOpen, setModelOpen] = useState(false)
  const [thinkingOpen, setThinkingOpen] = useState(false)
  const [personaOpen, setPersonaOpen] = useState(false)
  const modelRef = useRef<HTMLDivElement>(null)
  const thinkingRef = useRef<HTMLDivElement>(null)
  const personaRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void loadPersonas()
  }, [loadPersonas])

  // Load provider catalog on mount
  useEffect(() => {
    void loadProviders()
  }, [loadProviders])

  // Close dropdowns on outside click
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (modelRef.current && !modelRef.current.contains(e.target as Node)) setModelOpen(false)
      if (thinkingRef.current && !thinkingRef.current.contains(e.target as Node)) setThinkingOpen(false)
      if (personaRef.current && !personaRef.current.contains(e.target as Node)) setPersonaOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const personaId = sessionId ? getSessionPersona(sessionId) : 'chat'
  const selectedPersona: ChatPersona | undefined =
    personas.find((p) => p.id === personaId) ?? personas.find((p) => p.id === 'chat')
  const isOrchestrator = sessionId?.startsWith('dag:') ?? false

  const currentModel = sessionId ? sessionModels[sessionId] ?? null : null
  const modeCfg = MODE_CONFIG[mode]
  const ModeIcon = modeCfg.icon

  // Parse current model display
  const modelDisplay = currentModel
    ? currentModel.split('/').pop() ?? currentModel
    : modelInfo?.model ?? 'default'

  const thinkingCfg = THINKING_OPTIONS.find((o) => o.value === thinkingMode) ?? THINKING_OPTIONS[2]

  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px]" data-testid="composer-status-bar">
      {/* Persona selector（人设）—— 与模式/模型/思考同一排（R-014 F4）*/}
      <div className="relative" ref={personaRef}>
        <button
          type="button"
          onClick={() => !isOrchestrator && setPersonaOpen((o) => !o)}
          className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200 disabled:opacity-40"
          disabled={!sessionId}
          title={isOrchestrator ? '编排会话固定使用编排助手人设' : '选择对话智能体'}
        >
          <Bot className="h-3 w-3 text-primary-400" />
          <span className="max-w-[120px] truncate font-medium text-slate-300">
            {selectedPersona?.name ?? (isOrchestrator ? '编排助手' : '通用助手')}
          </span>
          {!isOrchestrator && (
            <ChevronDown className={`h-2.5 w-2.5 text-slate-500 transition-transform ${personaOpen ? 'rotate-180' : ''}`} />
          )}
        </button>
        {personaOpen && (
          <div className="glass absolute left-0 bottom-full z-50 mb-1 w-64 overflow-hidden rounded-xl border border-slate-700/60 shadow-2xl">
            <ul className="max-h-64 overflow-y-auto py-1">
              {personas.map((p) => (
                <li key={p.id}>
                  <button
                    type="button"
                    onClick={() => {
                      if (sessionId) setSessionPersona(sessionId, p.id)
                      setPersonaOpen(false)
                    }}
                    className={`flex w-full items-start gap-2 px-3 py-2 text-left transition-colors ${
                      p.id === personaId
                        ? 'bg-slate-700/40 text-slate-100'
                        : 'text-slate-300 hover:bg-slate-700/30'
                    }`}
                  >
                    <span className="mt-0.5 text-primary-400">
                      <Bot className="h-3.5 w-3.5" />
                    </span>
                    <span className="flex-1">
                      <span className="block text-xs font-medium">{p.name}</span>
                      <span className="block text-[11px] text-slate-500">{p.description}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="h-3 w-px bg-slate-700/60" />

      {/* Mode toggle */}
      <button
        type="button"
        onClick={toggleMode}
        className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 font-medium transition-all ${modeCfg.bg} ${modeCfg.color} border ${modeCfg.border} hover:brightness-110`}
        title={mode === 'plan' ? 'Plan 模式: 只读分析' : 'Build 模式: 完整工具'}
      >
        <ModeIcon className="h-3 w-3" />
        <span>{modeCfg.label}</span>
      </button>

      <div className="h-3 w-px bg-slate-700/60" />

      {/* Model selector */}
      <div className="relative" ref={modelRef}>
        <button
          type="button"
          onClick={() => setModelOpen((o) => !o)}
          className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
          title="选择模型"
        >
          <Bot className="h-3 w-3" />
          <span className="max-w-[120px] truncate">{modelDisplay}</span>
          <ChevronDown className={`h-2.5 w-2.5 text-slate-500 transition-transform ${modelOpen ? 'rotate-180' : ''}`} />
        </button>
        {modelOpen && (
          <div className="glass absolute left-0 bottom-full z-50 mb-1 w-72 overflow-hidden rounded-xl border border-slate-700/60 shadow-2xl">
            <div className="max-h-64 overflow-y-auto py-1">
              {/* Default option */}
              <button
                type="button"
                onClick={() => {
                  if (sessionId) clearSessionModel(sessionId)
                  setModelOpen(false)
                }}
                className={`flex w-full items-center gap-2 px-3 py-2 text-left transition-colors ${
                  !currentModel ? 'bg-slate-700/40 text-slate-100' : 'text-slate-300 hover:bg-slate-700/30'
                }`}
              >
                <Bot className="h-3.5 w-3.5 text-slate-500" />
                <span className="text-xs">Default ({modelInfo?.model ?? 'auto'})</span>
              </button>
              {providers.map((p) =>
                p.models.map((m) => {
                  const fullModel = `${p.name}/${m}`
                  const isSelected = currentModel === fullModel
                  // E1 密钥门禁：key_configured=false 的 provider 无密钥，选中
                  // 后发送必然失败。置灰 + 标注，禁止选择（与设置页口径一致）。
                  const available = p.key_configured
                  return (
                    <button
                      key={fullModel}
                      type="button"
                      disabled={!available}
                      onClick={() => {
                        if (!available || !sessionId) return
                        setSessionModel(sessionId, fullModel)
                        setModelOpen(false)
                      }}
                      className={`flex w-full items-center gap-2 px-3 py-2 text-left transition-colors ${
                        !available
                          ? 'cursor-not-allowed text-slate-600 opacity-50'
                          : isSelected
                            ? 'bg-slate-700/40 text-slate-100'
                            : 'text-slate-300 hover:bg-slate-700/30'
                      }`}
                      title={available ? undefined : `未配置密钥（${p.key_var}）`}
                    >
                      <span className="text-xs font-medium">{p.label || p.name}</span>
                      <span className="text-[11px] text-slate-500">{m}</span>
                      {!available && (
                        <span className="ml-auto text-[10px] text-slate-600">未配置密钥</span>
                      )}
                    </button>
                  )
                })
              )}
            </div>
          </div>
        )}
      </div>

      <div className="h-3 w-px bg-slate-700/60" />

      {/* Thinking selector */}
      <div className="relative" ref={thinkingRef}>
        <button
          type="button"
          onClick={() => setThinkingOpen((o) => !o)}
          className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
          title="思考模式"
        >
          {thinkingCfg.icon ? <thinkingCfg.icon className="h-3 w-3" /> : <Zap className="h-3 w-3" />}
          <span>{thinkingCfg.label}</span>
          <ChevronDown className={`h-2.5 w-2.5 text-slate-500 transition-transform ${thinkingOpen ? 'rotate-180' : ''}`} />
        </button>
        {thinkingOpen && (
          <div className="glass absolute left-0 bottom-full z-50 mb-1 w-36 overflow-hidden rounded-xl border border-slate-700/60 shadow-2xl">
            <ul className="py-1">
              {THINKING_OPTIONS.map((opt) => (
                <li key={opt.value}>
                  <button
                    type="button"
                    onClick={() => {
                      setThinkingMode(opt.value)
                      setThinkingOpen(false)
                    }}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors ${
                      thinkingMode === opt.value
                        ? 'bg-slate-700/40 text-slate-100'
                        : 'text-slate-300 hover:bg-slate-700/30'
                    }`}
                  >
                    {opt.icon ? <opt.icon className="h-3 w-3" /> : <Zap className="h-3 w-3" />}
                    <span className="text-xs">{opt.label}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Context usage ring（R-014 H2：小环 + hover 7 段明细弹层）*/}
      {usage.hasLimit && (
        <div className="group/usage relative ml-auto flex items-center gap-1.5" data-testid="composer-context-usage">
          <ContextRing segments={breakdown} percent={usage.percent} tone={tone} size={20} />
          <span className="font-mono text-[10px] text-slate-500">
            {usage.source === 'estimated' ? '~' : ''}
            {formatTokens(usage.usedTokens)} / {formatTokens(usage.limitTokens)}
          </span>
          <span className="font-mono text-[10px] text-slate-500">
            {usage.percent > 0 && usage.percent < 1
              ? usage.percent.toFixed(1)
              : usage.percent.toFixed(0)}%
          </span>
          {usage.isStale && (
            <span className="text-warning-fg" title="模型信息来自内置/fallback 数据，非最新抓取" data-testid="composer-context-stale">
              <AlertTriangle className="h-3 w-3" />
            </span>
          )}
          {/* hover 弹层：7 段明细（渐进式披露，对齐 ZCode 低调查看器 + 详情分层） */}
          <div className="glass invisible absolute bottom-full right-0 z-50 mb-2 w-72 rounded-xl border border-slate-700/60 p-3 opacity-0 shadow-2xl transition-opacity group-hover/usage:visible group-hover/usage:opacity-100" data-testid="context-breakdown-popover">
            <div className="mb-2 flex items-center justify-between text-[11px]">
              <span className="font-medium text-slate-200">上下文用量</span>
              <span className="font-mono text-slate-400">
                {formatTokens(usage.usedTokens)} / {formatTokens(usage.limitTokens)} ({usage.percent.toFixed(1)}%)
              </span>
            </div>
            <div className="mb-2 flex justify-center">
              <ContextRing segments={breakdown} percent={usage.percent} tone={tone} size={64} />
            </div>
            <BreakdownList segments={breakdown} />
            <div className="mt-2 border-t border-slate-700/60 pt-2 text-[10px] text-slate-500">
              剩余可用 {formatTokens(usage.remainingTokens)} · {usage.source === 'measured' ? '后端实测' : '字符估算'}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
