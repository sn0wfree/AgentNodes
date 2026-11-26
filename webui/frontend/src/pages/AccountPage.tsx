import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Settings as SettingsIcon, ShieldCheck } from 'lucide-react'
import { PageShell } from '../components/layout/PageShell'
import { Section } from '../components/settings/shared'
import { LLMSection } from '../components/settings/LLMSection'
import { AccountSection } from '../components/settings/AccountSection'
import { WorkspaceSection } from '../components/settings/WorkspaceSection'
import { ChatLayoutSection } from '../components/settings/ChatLayoutSection'
import { AppearanceSection } from '../components/settings/AppearanceSection'
import { AdminUsersPage } from './AdminUsersPage'
import { useLayoutStore } from '../stores/layout'
import { useAuthStore } from '../stores/auth'
import { api } from '../api/client'
import type { SystemInfo, LLMConfig } from '../components/settings/types'

const EMPTY_LLM_CONFIG: LLMConfig = {
  provider: '', model: '', api_key: '', api_key_masked: false, base_url: '',
  active_profile: '', profiles: [], providers: [],
}

/** 统一的「账户与设置」页：所有用户可见的 5 个设置分区 + admin-only 用户管理。
 *  设置并入用户管理页（R-014 account-settings-merge-20261123）——
 *  保留普通用户可达性，用户管理表格仅 admin 渲染（后端 require_admin 兜底）。 */
export function AccountPage() {
  const navigate = useNavigate()
  const chatLayout = useLayoutStore((s) => s.chatLayout)
  const setChatLayout = useLayoutStore((s) => s.setChatLayout)
  const user = useAuthStore((s) => s.user)
  const isAdmin = user?.role === 'admin'

  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)
  const [llmConfig, setLlmConfig] = useState<LLMConfig>(EMPTY_LLM_CONFIG)
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [sysError, setSysError] = useState('')

  useEffect(() => {
    api.get<SystemInfo>('/system/info')
      .then((info) => { setSystemInfo(info); setSysError('') })
      .catch((err: any) => setSysError(err.message || '系统信息加载失败'))
    api.get<LLMConfig>('/system/llm').then(setLlmConfig).catch(() => {})
  }, [])

  const reloadSystemInfo = async () => {
    const info = await api.get<SystemInfo>('/system/info')
    setSystemInfo(info)
    setSysError('')
  }

  return (
    <PageShell title="账户与设置" subtitle="个人信息、模型与环境偏好" icon={<SettingsIcon className="h-4 w-4" />}>
      <div className="max-w-3xl space-y-6">
        {sysError && (
          <div className="rounded-lg border border-red-800 bg-red-900/30 px-3 py-2 text-sm text-danger-fg">
            {sysError}
          </div>
        )}
        <LLMSection systemInfo={systemInfo} llmConfig={llmConfig} setLlmConfig={setLlmConfig}
          apiKeyInput={apiKeyInput} setApiKeyInput={setApiKeyInput} sysError={sysError}
          onSystemInfoReloaded={reloadSystemInfo} />
        <AccountSection username={user?.username || ''} />
        <WorkspaceSection systemInfo={systemInfo} />
        <ChatLayoutSection chatLayout={chatLayout} setChatLayout={setChatLayout} />
        <AppearanceSection />
        {isAdmin && (
          <Section icon={ShieldCheck} title="用户管理">
            <AdminUsersPage embedded />
          </Section>
        )}
        <button onClick={() => navigate('/chat')} className="text-xs text-muted hover:text-secondary">
          ← 返回会话
        </button>
      </div>
    </PageShell>
  )
}
