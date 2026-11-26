import { useState, useEffect } from 'react'
import { Cpu, Eye, EyeOff, Save, Check, AlertCircle } from 'lucide-react'
import type { LLMConfig, SystemInfo } from './types'
import { Section } from './shared'
import { api } from '../../api/client'

interface LLMSectionProps {
  systemInfo: SystemInfo | null
  llmConfig: LLMConfig
  setLlmConfig: (cfg: LLMConfig) => void
  apiKeyInput: string
  setApiKeyInput: (s: string) => void
  sysError: string
  onSystemInfoReloaded: () => Promise<void>
}

export function LLMSection({
  systemInfo,
  llmConfig,
  setLlmConfig,
  apiKeyInput,
  setApiKeyInput,
  sysError,
  onSystemInfoReloaded,
}: LLMSectionProps) {
  const [llmLoading, setLlmLoading] = useState(false)
  const [llmMsg, setLlmMsg] = useState('')
  const [llmError, setLlmError] = useState('')
  const [showApiKey, setShowApiKey] = useState(false)

  // Reset transient messages whenever the modal re-opens (parent
  // treats open as a key change by clearing apiKeyInput via prop).
  useEffect(() => {
    if (apiKeyInput === '' && !llmLoading) {
      setLlmMsg('')
      setLlmError('')
    }
  }, [apiKeyInput, llmLoading])

  const providers = llmConfig.providers || []
  const currentProvider = providers.find((p) => p.name === llmConfig.provider)
  const currentModels = currentProvider?.models || []

  const handleSave = async () => {
    setLlmMsg('')
    setLlmError('')
    if (!llmConfig.provider) {
      setLlmError('请选择 Provider')
      return
    }
    if (!llmConfig.model) {
      setLlmError('请输入 Model')
      return
    }
    if (llmConfig.provider === 'custom' && !llmConfig.base_url) {
      setLlmError('自定义模式下 Base URL 不能为空')
      return
    }
    setLlmLoading(true)
    try {
      await api.put('/system/llm', {
        provider: llmConfig.provider,
        model: llmConfig.model,
        base_url: llmConfig.base_url,
        api_key: apiKeyInput, // 空串 → 不修改密钥（掩码值永不上传）
      })
      setLlmMsg('配置已保存')
      // Reload config + system info. A reload failure must NOT wipe
      // the success message (B8) — surface it as an explicit warning.
      try {
        const cfg = await api.get<LLMConfig>('/system/llm')
        setLlmConfig(cfg)
        setApiKeyInput('')
        await onSystemInfoReloaded()
      } catch (err: any) {
        setLlmError(`已保存，但刷新失败：${err.message || '未知错误'}`)
      }
    } catch (err: any) {
      setLlmMsg('')
      setLlmError(err.message || '保存失败')
    } finally {
      setLlmLoading(false)
    }
  }

  return (
    <Section icon={Cpu} title="LLM 配置">
      <div className="space-y-3">
        {/* Provider */}
        <div>
          <label className="mb-1 block text-xs text-muted">Provider</label>
          <select
            value={llmConfig.provider}
            onChange={(e) => {
              const provider = e.target.value
              const p = providers.find((x) => x.name === provider)
              setLlmConfig({
                ...llmConfig,
                provider,
                model: p?.model || '',
                base_url: p?.base_url || '',
              })
            }}
            className="w-full rounded-lg border border-strong bg-slate-900 px-3 py-2 text-sm text-foreground outline-none focus:border-primary-500"
          >
            <option value="">选择 Provider</option>
            {providers.map((p) => (
              <option key={p.name} value={p.name}>
                {p.label}
                {p.key_configured ? ' ✓' : '（未配置密钥）'}
              </option>
            ))}
          </select>
        </div>

        {/* Model */}
        <div>
          <label className="mb-1 block text-xs text-muted">Model</label>
          {currentModels.length > 0 ? (
            <select
              value={llmConfig.model}
              onChange={(e) => setLlmConfig({ ...llmConfig, model: e.target.value })}
              className="w-full rounded-lg border border-strong bg-slate-900 px-3 py-2 text-sm text-foreground outline-none focus:border-primary-500"
            >
              <option value="">选择 Model</option>
              {currentModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              placeholder="输入模型名称，如 qwen2.5、llama3、mistral"
              value={llmConfig.model}
              onChange={(e) => setLlmConfig({ ...llmConfig, model: e.target.value })}
              className="w-full rounded-lg border border-strong bg-slate-900 px-3 py-2 text-sm text-foreground outline-none focus:border-primary-500"
            />
          )}
        </div>

        {/* API Key */}
        <div>
          <label className="mb-1 block text-xs text-muted">API Key</label>
          <div className="relative">
            <input
              type={showApiKey ? 'text' : 'password'}
              placeholder={
                llmConfig.api_key_masked && llmConfig.api_key
                  ? `已配置 ${llmConfig.api_key}（留空则不修改）`
                  : '输入 API Key'
              }
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              className="w-full rounded-lg border border-strong bg-slate-900 px-3 py-2 pr-10 text-sm text-foreground outline-none focus:border-primary-500"
            />
            <button
              type="button"
              onClick={() => setShowApiKey(!showApiKey)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-foreground"
            >
              {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {/* Base URL */}
        <div>
          <label className="mb-1 block text-xs text-muted">
            Base URL {llmConfig.provider === 'custom' ? '(必填)' : '(可选，留空使用默认)'}
          </label>
          <input
            type="text"
            placeholder={llmConfig.provider === 'custom' ? '如 http://localhost:11434/v1' : '留空使用默认'}
            value={llmConfig.base_url}
            onChange={(e) => setLlmConfig({ ...llmConfig, base_url: e.target.value })}
            className="w-full rounded-lg border border-strong bg-slate-900 px-3 py-2 text-sm text-foreground outline-none focus:border-primary-500"
          />
        </div>

        {/* Status */}
        {systemInfo?.llm && (
          <div className="flex items-center gap-2 text-xs">
            {systemInfo.llm.configured ? (
              <>
                <Check className="h-3 w-3 text-success-fg" />
                <span className="text-success-fg">
                  当前: {llmConfig.active_profile || llmConfig.provider || systemInfo.llm.provider} /{' '}
                  {llmConfig.model || systemInfo.llm.model}
                </span>
              </>
            ) : (
              <>
                <AlertCircle className="h-3 w-3 text-warning-fg" />
                <span className="text-warning-fg">未配置</span>
              </>
            )}
          </div>
        )}

        {llmError && <p className="text-xs text-danger-fg">{llmError}</p>}
        {sysError && <p className="text-xs text-danger-fg">⚠ {sysError}</p>}
        {llmMsg && <p className="text-xs text-success-fg">{llmMsg}</p>}

        <button
          onClick={handleSave}
          disabled={llmLoading}
          className="flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
        >
          <Save className="h-4 w-4" />
          {llmLoading ? '保存中...' : '保存配置'}
        </button>
      </div>
    </Section>
  )
}