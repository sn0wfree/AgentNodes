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
import { useChatStore } from '../../stores/chat'
import { tokenTone, toneFillClass } from '../../utils/tokenThresholds'

interface ComposerStatusBarProps {
  sessionId: string | null
}

const MODE_CONFIG = {
  plan: { icon: Search, label: 'Plan', color: 'text-violet-400', bg: 'bg-violet-500/10', border: 'border-violet-500/30' },
  build: { icon: Hammer, label: 'Build', color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30' },
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
  // P9：token 用量贴输入框，口径与右栏 TokenCard 同源（TOKEN_THRESHOLDS）。
  const tokensUsed = useChatStore((s) =>
    sessionId ? s.tokensUsed.get(sessionId) ?? 0 : 0
  )

  const [modelOpen, setModelOpen] = useState(false)
  const [thinkingOpen, setThinkingOpen] = useState(false)
  const modelRef = useRef<HTMLDivElement>(null)
  const thinkingRef = useRef<HTMLDivElement>(null)

  // Load provider catalog on mount
  useEffect(() => {
    void loadProviders()
  }, [loadProviders])

  // Close dropdowns on outside click
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (modelRef.current && !modelRef.current.contains(e.target as Node)) setModelOpen(false)
      if (thinkingRef.current && !thinkingRef.current.contains(e.target as Node)) setThinkingOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const currentModel = sessionId ? sessionModels[sessionId] ?? null : null
  const modeCfg = MODE_CONFIG[mode]
  const ModeIcon = modeCfg.icon

  // Parse current model display
  const modelDisplay = currentModel
    ? currentModel.split('/').pop() ?? currentModel
    : modelInfo?.model ?? 'default'

  const thinkingCfg = THINKING_OPTIONS.find((o) => o.value === thinkingMode) ?? THINKING_OPTIONS[2]

  return (
    <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px]">
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

      {/* Context usage meter（P9：贴输入框，Q7 阈值与右栏一致） */}
      {modelInfo && (
        <div className="ml-auto flex min-w-[92px] items-center gap-1.5" title="当前会话上下文用量">
          <div className="h-1 min-w-[44px] flex-1 overflow-hidden rounded-full bg-slate-700/60">
            <div
              className={`h-full transition-all ${toneFillClass(tokenTone(tokensUsed / (modelInfo.context_tokens || 1) * 100))}`}
              style={{
                width: `${modelInfo.context_tokens ? Math.min(100, tokensUsed / modelInfo.context_tokens * 100) : 0}%`,
              }}
            />
          </div>
          <span className="font-mono text-[10px] text-slate-500">
            {modelInfo.context_tokens ? `${Math.round(tokensUsed / modelInfo.context_tokens * 100)}%` : ''}
          </span>
          <span className="hidden text-slate-600 sm:inline">
            {modelInfo.context_tokens ? `${Math.round(modelInfo.context_tokens / 1000)}K` : ''}
          </span>
        </div>
      )}
    </div>
  )
}
