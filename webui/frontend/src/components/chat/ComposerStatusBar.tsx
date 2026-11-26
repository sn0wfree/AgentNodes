import { useEffect, useRef, useState } from 'react'
import {
  Search,
  Hammer,
  ChevronDown,
  Sparkles,
  Brain,
  Zap,
  Bot,
} from 'lucide-react'
import { useModeStore } from '../../stores/mode'
import { useModelStore } from '../../stores/model'
import { useThinkingPrefStore } from '../../stores/thinkingPref'
import { useSystemStore } from '../../stores/system'
import { usePersonaStore } from '../../stores/personas'
import { useComposerCollapse } from '../../hooks/useComposerCollapse'
import type { ChatPersona } from '../../api/client'

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
  // 人设（persona）选择器 —— 状态栏左组（设计定稿 2026-11-25：左 persona+模式，右 模型+思考）。
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

  // ── 折叠档位：按**卡片**实测宽度判定（设计定稿 2026-11-25）
  const { ref: barRef, mode: collapse } = useComposerCollapse<HTMLDivElement>()

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
  const isOrchestrator = sessionId?.startsWith('dag:') ?? false
  const selectedPersona: ChatPersona | undefined =
    personas.find((p) => p.id === personaId) ?? personas.find((p) => p.id === 'chat')
  const personaName = selectedPersona?.name ?? (isOrchestrator ? '编排助手' : '通用助手')

  const currentModel = sessionId ? sessionModels[sessionId] ?? null : null
  const modeCfg = MODE_CONFIG[mode]
  const ModeIcon = modeCfg.icon

  // Parse current model display
  const modelDisplay = currentModel
    ? currentModel.split('/').pop() ?? currentModel
    : modelInfo?.model ?? 'default'
  // 折叠短名：优先用当前 session 选中的 provider label；否则退回模型名首段。
  // 例：minimax/MiniMax-M3.1-Flash → MiniMax（避免 split('-') 把 claude-3-5 截成 claude）
  const selectedProvider = providers.find((p) =>
    currentModel?.startsWith(`${p.name}/`),
  )
  const modelShort = selectedProvider?.label || selectedProvider?.name || modelDisplay.split('-')[0]

  const thinkingCfg = THINKING_OPTIONS.find((o) => o.value === thinkingMode) ?? THINKING_OPTIONS[2]
  // 收敛折叠档位判断（避免内联重复条件散落各处）
  const showLabels = collapse !== 'icon'
  const showChevrons = collapse === 'full'

  return (
    <div
      ref={barRef}
      data-collapse={collapse}
      data-composer-leading-actions
      className="flex flex-nowrap items-center gap-2 pr-9 text-[11px]"
      data-testid="composer-status-bar"
    >
      {/* 左组：persona + 模式（设计定稿 2026-11-25） */}
      <div className="flex min-w-0 items-center gap-2">
        <div className="relative" ref={personaRef}>
          <button
            type="button"
            onClick={() => !isOrchestrator && setPersonaOpen((o) => !o)}
            className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-muted transition-colors hover:bg-surface hover:text-foreground disabled:opacity-40"
            disabled={!sessionId}
            title={isOrchestrator ? '编排会话固定使用编排助手人设' : '选择对话智能体'}
          >
            <Bot className="h-3 w-3 text-primary-400" />
            {showLabels && (
              <span className="max-w-[120px] truncate font-medium text-secondary">
                {personaName}
              </span>
            )}
            {!isOrchestrator && showChevrons && (
              <ChevronDown className={`h-2.5 w-2.5 text-muted transition-transform ${personaOpen ? 'rotate-180' : ''}`} />
            )}
          </button>
          {personaOpen && (
            <div className="glass absolute left-0 bottom-full z-50 mb-1 w-64 overflow-hidden rounded-xl border border-subtle !bg-slate-900/95 shadow-2xl">
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
                          ? 'bg-surface text-foreground'
                          : 'text-secondary hover:bg-surface'
                      }`}
                    >
                      <span className="mt-0.5 text-primary-400">
                        <Bot className="h-3.5 w-3.5" />
                      </span>
                      <span className="flex-1">
                        <span className="block text-xs font-medium">{p.name}</span>
                        <span className="block text-[11px] text-muted">{p.description}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="h-3 w-px bg-surface-raised" />

        {/* Mode toggle */}
        <button
          type="button"
          onClick={toggleMode}
          className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 font-medium transition-all ${modeCfg.bg} ${modeCfg.color} border ${modeCfg.border} hover:brightness-110`}
          title={mode === 'plan' ? 'Plan 模式: 只读分析' : 'Build 模式: 完整工具'}
        >
          <ModeIcon className="h-3 w-3" />
          {showLabels && <span>{modeCfg.label}</span>}
        </button>
      </div>

      {/* 右组：模型 + 思考（贴近右下角发送按钮）；模型全名在右组右缘 */}
      <div className="ml-auto flex flex-none items-center gap-2">
        <div className="relative" ref={modelRef}>
          <button
            type="button"
            onClick={() => setModelOpen((o) => !o)}
            className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-muted transition-colors hover:bg-surface hover:text-foreground"
            title="选择模型"
          >
            <Bot className="h-3 w-3" />
            {showLabels && (
              <span className="max-w-[120px] truncate">
                {collapse === 'short' ? modelShort : modelDisplay}
              </span>
            )}
            {showChevrons && (
              <ChevronDown className={`h-2.5 w-2.5 text-muted transition-transform ${modelOpen ? 'rotate-180' : ''}`} />
            )}
          </button>
          {modelOpen && (
            <div className="glass absolute right-0 bottom-full z-50 mb-1 w-72 overflow-hidden rounded-xl border border-subtle !bg-slate-900/95 shadow-2xl">
              <div className="max-h-64 overflow-y-auto py-1">
              {/* Default option */}
              <button
                type="button"
                onClick={() => {
                  if (sessionId) clearSessionModel(sessionId)
                  setModelOpen(false)
                }}
                className={`flex w-full items-center gap-2 px-3 py-2 text-left transition-colors ${
                  !currentModel ? 'bg-surface text-foreground' : 'text-secondary hover:bg-surface'
                }`}
              >
                <Bot className="h-3.5 w-3.5 text-muted" />
                <span className="text-xs">Default ({modelInfo?.model ?? 'auto'})</span>
              </button>
              {/* 2026-11-26 定稿：未配置密钥的 provider 整体不显示（下拉只保留
                  可用选择；配置密钥走账户与设置页）。E1 密钥门禁由此前移。 */}
              {providers
                .filter((p) => p.key_configured)
                .map((p) =>
                  p.models.map((m) => {
                    const fullModel = `${p.name}/${m}`
                    const isSelected = currentModel === fullModel
                    return (
                      <button
                        key={fullModel}
                        type="button"
                        onClick={() => {
                          if (!sessionId) return
                          setSessionModel(sessionId, fullModel)
                          setModelOpen(false)
                        }}
                        className={`flex w-full items-center gap-2 px-3 py-2 text-left transition-colors ${
                          isSelected
                            ? 'bg-surface text-foreground'
                            : 'text-secondary hover:bg-surface'
                        }`}
                      >
                        <span className="text-xs font-medium">{p.label || p.name}</span>
                        <span className="text-[11px] text-muted">{m}</span>
                      </button>
                    )
                  })
                )}
              </div>
            </div>
          )}
        </div>

        {showChevrons && (
          <>
            <div className="h-3 w-px bg-surface-raised" />
            {/* Thinking selector（定稿：短名/图标档隐藏思考） */}
            <div className="relative" ref={thinkingRef}>
              <button
                type="button"
                onClick={() => setThinkingOpen((o) => !o)}
                className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-muted transition-colors hover:bg-surface hover:text-foreground"
                title="思考模式"
              >
                {thinkingCfg.icon ? <thinkingCfg.icon className="h-3 w-3" /> : <Zap className="h-3 w-3" />}
                <span>{thinkingCfg.label}</span>
                <ChevronDown className={`h-2.5 w-2.5 text-muted transition-transform ${thinkingOpen ? 'rotate-180' : ''}`} />
              </button>
              {thinkingOpen && (
                <div className="glass absolute right-0 bottom-full z-50 mb-1 w-36 overflow-hidden rounded-xl border border-subtle !bg-slate-900/95 shadow-2xl">
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
                              ? 'bg-surface text-foreground'
                              : 'text-secondary hover:bg-surface'
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
          </>
        )}
      </div>
    </div>
  )
}
