import { create } from 'zustand'
import { api } from '../api/client'

export interface LLMInfo {
  provider: string
  model: string
  configured: boolean
  /** 统一重试上限（来自后端 llm.json max_retries，经 /system/info 下发）。 */
  max_retries: number
}

export interface ModelInfo {
  provider: string
  model: string
  models_dev_id: string
  context_tokens: number
  max_output_tokens: number
  supports_vision: boolean
  supports_audio: boolean
  supports_pdf: boolean
  supports_tools: boolean
  supports_reasoning: boolean
  supports_structured_output: boolean
  cost_input: number | null
  cost_output: number | null
  cost_cache_read: number | null
  cost_cache_write: number | null
  description: string
  release_date: string | null
  source: 'bundled' | 'cached' | 'fetched' | 'fallback'
  fetched_at: string | null
}

interface SystemState {
  workspacePath: string
  llm: LLMInfo
  modelInfo: ModelInfo | null
  fetchSystemInfo: () => Promise<void>
  refreshModelInfo: (provider?: string, model?: string) => Promise<ModelInfo | null>
}

const initialLLM: LLMInfo = {
  provider: '',
  model: '',
  configured: false,
  max_retries: 0, // 0 = 尚未从后端加载（调用方据此走默认兜底）
}

export const useSystemStore = create<SystemState>((set) => ({
  workspacePath: '',
  llm: initialLLM,
  modelInfo: null,
  fetchSystemInfo: async () => {
    try {
      const data = await api.get<{ workspace_path?: string; llm: LLMInfo; model_info: ModelInfo | null }>(
        '/system/info'
      )
      if (data?.workspace_path) {
        set({ workspacePath: data.workspace_path })
      }
      const llm = data?.llm
      if (llm) {
        set({
          llm: {
            provider: llm.provider ?? '',
            model: llm.model ?? '',
            configured: !!llm.configured,
            max_retries: Number.isFinite(llm.max_retries) ? llm.max_retries : 0,
          },
        })
      }
      if (data?.model_info) {
        set({ modelInfo: data.model_info })
      }
    } catch {
      // Silent: keep defaults
    }
  },
  refreshModelInfo: async (provider, model) => {
    try {
      const body: { provider?: string; model?: string } = {}
      if (provider) body.provider = provider
      if (model) body.model = model
      const info = await api.post<ModelInfo>('/system/model-info/refresh', body)
      set({ modelInfo: info })
      return info
    } catch (err) {
      console.error('refreshModelInfo failed:', err)
      return null
    }
  },
}))