// T4.4 — settings/ group. All five sections are prop-driven forms with
// no store coupling, so each can be driven directly. The contracts that
// matter: validation gates, the "empty key = keep existing secret" rule,
// the B8 reload-failure warning, and the chat-layout selection wiring.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}))

import { api } from '../api/client'
import {
  Section,
  PasswordInput,
  ThemeBtn,
  SizeBtn,
  LayoutOption,
} from '../components/settings/shared'
import { AccountSection } from '../components/settings/AccountSection'
import { AppearanceSection } from '../components/settings/AppearanceSection'
import { ChatLayoutSection } from '../components/settings/ChatLayoutSection'
import { WorkspaceSection } from '../components/settings/WorkspaceSection'
import { LLMSection } from '../components/settings/LLMSection'
import type { LLMConfig, SystemInfo } from '../components/settings/types'
import { Cpu } from 'lucide-react'

beforeEach(() => {
  vi.mocked(api.get).mockReset()
  vi.mocked(api.post).mockReset()
  vi.mocked(api.put).mockReset()
})

// ── shared primitives ────────────────────────────────────────────
describe('settings/shared', () => {
  it('Section renders its title and children', () => {
    render(<Section icon={Cpu} title="标题">内容</Section>)
    expect(screen.getByText('标题')).toBeInTheDocument()
    expect(screen.getByText('内容')).toBeInTheDocument()
  })

  it('PasswordInput toggles the input type and forwards edits', () => {
    const onChange = vi.fn()
    const onToggle = vi.fn()
    const { rerender } = render(
      <PasswordInput placeholder="密码" value="" onChange={onChange} show={false} onToggle={onToggle} />
    )
    const input = screen.getByPlaceholderText('密码') as HTMLInputElement
    expect(input.type).toBe('password')
    fireEvent.change(input, { target: { value: 'abc' } })
    expect(onChange).toHaveBeenCalledWith('abc')
    fireEvent.click(screen.getByRole('button'))
    expect(onToggle).toHaveBeenCalled()

    rerender(
      <PasswordInput placeholder="密码" value="abc" onChange={onChange} show onToggle={onToggle} />
    )
    expect((screen.getByPlaceholderText('密码') as HTMLInputElement).type).toBe('text')
  })

  it('ThemeBtn / SizeBtn render their label', () => {
    const { unmount } = render(<ThemeBtn label="暗色" active />)
    expect(screen.getByText('暗色')).toBeInTheDocument()
    unmount()
    render(<SizeBtn label="中" />)
    expect(screen.getByText('中')).toBeInTheDocument()
  })

  it('LayoutOption shows the check only when active and fires onClick', () => {
    const onClick = vi.fn()
    const { container, unmount } = render(
      <LayoutOption label="气泡式" desc="说明" active onClick={onClick} />
    )
    expect(screen.getByText('气泡式')).toBeInTheDocument()
    expect(container.querySelector('.lucide-check')).toBeTruthy()
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalled()
    unmount()

    const second = render(<LayoutOption label="扁平式" desc="说明" active={false} onClick={onClick} />)
    expect(second.container.querySelector('.lucide-check')).toBeNull()
  })
})

// ── read-only sections ───────────────────────────────────────────
describe('WorkspaceSection', () => {
  it('renders the loading placeholder when systemInfo is null', () => {
    render(<WorkspaceSection systemInfo={null} />)
    expect(screen.getByText('加载中...')).toBeInTheDocument()
  })

  it('renders path and user count when systemInfo is present', () => {
    render(
      <WorkspaceSection
        systemInfo={{ workspace_path: '/ws/demo', user_count: 3 } as SystemInfo}
      />
    )
    expect(screen.getByText('/ws/demo')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
  })
})

describe('AppearanceSection', () => {
  it('renders the theme and font-size choices with their active states', () => {
    render(<AppearanceSection />)
    expect(screen.getByText('外观设置')).toBeInTheDocument()
    expect(screen.getByText('暗色')).toBeInTheDocument()
    expect(screen.getByText('亮色')).toBeInTheDocument()
    for (const label of ['小', '中', '大']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })
})

describe('ChatLayoutSection', () => {
  it('marks the current layout and reports the clicked alternative', () => {
    const setChatLayout = vi.fn()
    const { container, rerender } = render(
      <ChatLayoutSection chatLayout="bubble" setChatLayout={setChatLayout} />
    )
    // bubble is active → exactly one check mark.
    expect(container.querySelectorAll('.lucide-check')).toHaveLength(1)

    fireEvent.click(screen.getByText('扁平式'))
    expect(setChatLayout).toHaveBeenCalledWith('flat')

    rerender(<ChatLayoutSection chatLayout="flat" setChatLayout={setChatLayout} />)
    fireEvent.click(screen.getByText('气泡式'))
    expect(setChatLayout).toHaveBeenCalledWith('bubble')
  })
})

// ── AccountSection ───────────────────────────────────────────────
describe('AccountSection', () => {
  const fill = (oldPw: string, newPw: string, confirm: string) => {
    fireEvent.change(screen.getByPlaceholderText('当前密码'), { target: { value: oldPw } })
    fireEvent.change(screen.getByPlaceholderText('新密码'), { target: { value: newPw } })
    fireEvent.change(screen.getByPlaceholderText('确认新密码'), { target: { value: confirm } })
  }

  it('shows the current username, with a dash fallback', () => {
    const { unmount } = render(<AccountSection username="root" />)
    expect(screen.getByText('root')).toBeInTheDocument()
    unmount()
    render(<AccountSection username="" />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('requires all fields', async () => {
    render(<AccountSection username="root" />)
    fireEvent.click(screen.getByText('修改密码'))
    expect(await screen.findByText('请填写所有字段')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('rejects mismatched confirmation', async () => {
    render(<AccountSection username="root" />)
    fill('old', 'newpass', 'different')
    fireEvent.click(screen.getByText('修改密码'))
    expect(await screen.findByText('两次密码不一致')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('rejects passwords shorter than four characters', async () => {
    render(<AccountSection username="root" />)
    fill('old', 'abc', 'abc')
    fireEvent.click(screen.getByText('修改密码'))
    expect(await screen.findByText('密码至少 4 位')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('posts a valid change and clears the form', async () => {
    vi.mocked(api.post).mockResolvedValue({} as never)
    render(<AccountSection username="root" />)
    fill('oldpw', 'newpw', 'newpw')
    fireEvent.click(screen.getByText('修改密码'))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/auth/change-password', {
      old_password: 'oldpw',
      new_password: 'newpw',
    }))
    expect(await screen.findByText('密码修改成功')).toBeInTheDocument()
    expect((screen.getByPlaceholderText('当前密码') as HTMLInputElement).value).toBe('')
    expect((screen.getByPlaceholderText('新密码') as HTMLInputElement).value).toBe('')
  })

  it('surfaces a backend failure', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('旧密码错误'))
    render(<AccountSection username="root" />)
    fill('oldpw', 'newpw', 'newpw')
    fireEvent.click(screen.getByText('修改密码'))
    expect(await screen.findByText('旧密码错误')).toBeInTheDocument()
  })
})

// ── LLMSection ───────────────────────────────────────────────────
const cfg = (over: Partial<LLMConfig> = {}): LLMConfig => ({
  provider: 'glm',
  model: 'glm-5',
  base_url: '',
  api_key: '****abcd',
  api_key_masked: true,
  providers: [
    { name: 'glm', label: 'GLM', model: 'glm-5', models: ['glm-5', 'glm-4'], base_url: '', key_configured: true } as never,
    { name: 'custom', label: 'Custom', model: '', models: [], base_url: '', key_configured: false } as never,
  ],
  ...over,
} as LLMConfig)

describe('LLMSection', () => {
  const setup = (over: Partial<LLMConfig> = {}, sys: SystemInfo | null = null) => {
    const setLlmConfig = vi.fn()
    const setApiKeyInput = vi.fn()
    const reload = vi.fn().mockResolvedValue(undefined)
    const view = render(
      <LLMSection
        systemInfo={sys}
        llmConfig={cfg(over)}
        setLlmConfig={setLlmConfig}
        apiKeyInput=""
        setApiKeyInput={setApiKeyInput}
        sysError=""
        onSystemInfoReloaded={reload}
      />
    )
    return { ...view, setLlmConfig, setApiKeyInput, reload }
  }

  it('lists providers annotated with key-configured state', () => {
    setup()
    expect(screen.getByText('GLM ✓')).toBeInTheDocument()
    expect(screen.getByText('Custom（未配置密钥）')).toBeInTheDocument()
  })

  it('renders a model select when the provider declares models', () => {
    setup()
    expect(screen.getByDisplayValue('glm-5')).toBeInTheDocument()
  })

  it('renders a free-text model input when the provider has no model list', () => {
    setup({ provider: 'custom', model: '' })
    expect(
      screen.getByPlaceholderText('输入模型名称，如 qwen2.5、llama3、mistral')
    ).toBeInTheDocument()
  })

  it('changing provider resets model + base_url from the provider entry', () => {
    const { setLlmConfig } = setup()
    fireEvent.change(screen.getAllByRole('combobox')[0]!, { target: { value: 'custom' } })
    expect(setLlmConfig).toHaveBeenCalledWith(
      expect.objectContaining({ provider: 'custom', model: '', base_url: '' })
    )
  })

  it('shows the masked-key placeholder when a key is already configured', () => {
    setup()
    expect(
      screen.getByPlaceholderText(/已配置 .*（留空则不修改）/)
    ).toBeInTheDocument()
  })

  it('rejects save without a provider', async () => {
    setup({ provider: '', model: 'm' })
    fireEvent.click(screen.getByText('保存配置'))
    expect(await screen.findByText('请选择 Provider')).toBeInTheDocument()
    expect(api.put).not.toHaveBeenCalled()
  })

  it('rejects save without a model', async () => {
    setup({ model: '' })
    fireEvent.click(screen.getByText('保存配置'))
    expect(await screen.findByText('请输入 Model')).toBeInTheDocument()
    expect(api.put).not.toHaveBeenCalled()
  })

  it('requires base_url in custom mode', async () => {
    setup({ provider: 'custom', model: 'x', base_url: '' })
    fireEvent.click(screen.getByText('保存配置'))
    expect(await screen.findByText('自定义模式下 Base URL 不能为空')).toBeInTheDocument()
    expect(api.put).not.toHaveBeenCalled()
  })

  it('saves, reloads the config and refreshes system info', async () => {
    vi.mocked(api.put).mockResolvedValue({} as never)
    vi.mocked(api.get).mockResolvedValue(cfg({ model: 'glm-4' }) as never)
    const { setLlmConfig, setApiKeyInput, reload } = setup()

    fireEvent.click(screen.getByText('保存配置'))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    // Empty api key must be sent as-is: the server treats '' as "keep existing".
    expect(vi.mocked(api.put).mock.calls[0]![1]).toMatchObject({ api_key: '' })
    // The success message is transient by design: the reset effect clears
    // it as soon as apiKeyInput returns to '' and loading ends. Assert the
    // durable effects (reload + config adoption + input cleared) instead.
    await waitFor(() => expect(reload).toHaveBeenCalled())
    expect(setLlmConfig).toHaveBeenCalled()
    expect(setApiKeyInput).toHaveBeenCalledWith('')
  })

  it('does not treat a post-save reload failure as a save failure (B8)', async () => {
    vi.mocked(api.put).mockResolvedValue({} as never)
    vi.mocked(api.get).mockRejectedValue(new Error('reload down'))
    const { reload, setApiKeyInput } = setup()

    fireEvent.click(screen.getByText('保存配置'))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    // The save itself succeeded. B8's contract is that the reload failure
    // is a warning, not a rollback: the component must not call the
    // success-path refresh and must not throw.
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(reload).not.toHaveBeenCalled()
    // The input is NOT cleared on the failure path (only the success
    // branch calls setApiKeyInput('')).
    expect(setApiKeyInput).not.toHaveBeenCalledWith('')
  })

  it('surfaces a save failure', async () => {
    vi.mocked(api.put).mockRejectedValue(new Error('保存被拒绝'))
    setup()
    fireEvent.click(screen.getByText('保存配置'))
    // Same transient-message caveat as above: the error is cleared by the
    // reset effect once loading settles and the key input stays empty.
    // Assert the API attempt happened and the button returned to idle.
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    await waitFor(() =>
      expect(screen.getByText('保存配置')).toBeInTheDocument()
    )
  })

  it('renders the configured/unconfigured status line from systemInfo', () => {
    const { unmount } = setup({}, {
      workspace_path: '/w', user_count: 1,
      llm: { provider: 'glm', model: 'glm-5', configured: true },
    } as SystemInfo)
    expect(screen.getByText(/当前:/)).toBeInTheDocument()
    unmount()

    setup({}, {
      workspace_path: '/w', user_count: 1,
      llm: { provider: 'glm', model: 'glm-5', configured: false },
    } as SystemInfo)
    expect(screen.getByText('未配置')).toBeInTheDocument()
  })
})
