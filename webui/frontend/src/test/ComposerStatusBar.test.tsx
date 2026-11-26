import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ComposerStatusBar } from '../components/chat/ComposerStatusBar'
import { useModeStore } from '../stores/mode'
import { useModelStore } from '../stores/model'
import { useThinkingPrefStore } from '../stores/thinkingPref'
import { useSystemStore } from '../stores/system'
import { usePersonaStore } from '../stores/personas'

beforeEach(() => {
  // Reset stores
  useModeStore.setState({ mode: 'build' })
  useModelStore.setState({ providers: [], sessionModels: {} })
  useThinkingPrefStore.setState({ thinkingMode: 'auto' })
  useSystemStore.setState({ modelInfo: { model: 'test-model', context_tokens: 128000 } as any })
  usePersonaStore.setState({ personas: [], loaded: true, sessionPersona: {} } as never)
})

describe('ComposerStatusBar', () => {
  it('renders mode toggle with default build mode', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    expect(screen.getByText('Build')).toBeDefined()
  })

  it('toggles mode on click', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    const btn = screen.getByText('Build')
    fireEvent.click(btn)
    expect(useModeStore.getState().mode).toBe('plan')
    expect(screen.getByText('Plan')).toBeDefined()
  })

  it('renders model selector', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    expect(screen.getByText('test-model')).toBeDefined()
  })

  it('renders thinking selector with auto default', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    expect(screen.getByText('Auto')).toBeDefined()
  })

  it('opens thinking dropdown on click', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    const thinkingBtn = screen.getByText('Auto')
    fireEvent.click(thinkingBtn)
    expect(screen.getByText('Off')).toBeDefined()
    expect(screen.getByText('On')).toBeDefined()
  })

  it('changes thinking mode', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    const thinkingBtn = screen.getByText('Auto')
    fireEvent.click(thinkingBtn)
    const offBtn = screen.getByText('Off')
    fireEvent.click(offBtn)
    expect(useThinkingPrefStore.getState().thinkingMode).toBe('off')
  })

  // ---- 设计定稿 2026-11-25：左右分组 + 用量环移出状态栏 ----
  it('splits into left (persona/mode) and right (model/thinking) groups', () => {
    const { container } = render(<ComposerStatusBar sessionId="s1" />)
    // 状态栏自身不再渲染用量环（环移到卡片右上角，Composer 级验证）
    expect(screen.queryByTestId('composer-context-usage')).toBeNull()
    expect(screen.queryByTestId('context-breakdown-popover')).toBeNull()
    // 结构：存在左组（min-w-0）与右组（ml-auto）；组内按钮总数 = persona+mode+model+thinking
    const bar = container.querySelector('[data-testid="composer-status-bar"]')!
    const left = bar.firstElementChild as HTMLElement
    const right = bar.lastElementChild as HTMLElement
    expect(left.className).toContain('min-w-0')
    expect(right.className).toContain('ml-auto')
    expect(left.querySelectorAll('button').length).toBe(2) // persona + mode
    expect(right.querySelectorAll('button').length).toBe(2) // model + thinking
  })

  it('defaults to full collapse mode when width is not measurable (jsdom)', () => {
    const { container } = render(<ComposerStatusBar sessionId="s1" />)
    const bar = container.querySelector('[data-testid="composer-status-bar"]')!
    expect(bar.getAttribute('data-collapse')).toBe('full')
    // full 档：思考与模型 chevron 可见
    expect(screen.getByText('Auto')).toBeDefined()
  })

  // ---- 模型下拉密钥门禁（2026-11-26 定稿：无密钥 provider 直接不显示，
  // ---- 不再渲染禁用项——下拉只保留可用的选择，减少信息噪音）----
  const mkProvider = (over: Record<string, unknown> = {}) => ({
    name: 'deepseek', label: 'deepseek', model: 'deepseek-chat',
    models: ['deepseek-chat'], base_url: '', key_var: 'DEEPSEEK_API_KEY',
    key_configured: false, ...over,
  })

  it('hides models from providers without a configured key', () => {
    useModelStore.setState({ providers: [mkProvider()] })
    render(<ComposerStatusBar sessionId="s1" />)
    fireEvent.click(screen.getByTitle('选择模型'))
    expect(screen.queryByText('deepseek-chat')).toBeNull()
    expect(screen.queryByText('未配置密钥')).toBeNull()
  })

  it('still lists models from providers with a key', () => {
    useModelStore.setState({
      providers: [
        mkProvider(),
        mkProvider({ name: 'minimax', label: 'MiniMax', models: ['MiniMax-M3'], key_configured: true }),
      ],
    })
    render(<ComposerStatusBar sessionId="s1" />)
    fireEvent.click(screen.getByTitle('选择模型'))
    expect(screen.queryByText('deepseek-chat')).toBeNull()
    expect(screen.getByText('MiniMax-M3')).toBeDefined()
  })

  it('allows selecting a model from a provider with a key', () => {
    useModelStore.setState({
      providers: [mkProvider({
        name: 'minimax', label: 'MiniMax', model: 'MiniMax-M3',
        models: ['MiniMax-M3'], base_url: '', key_var: 'MINIMAX_API_KEY', key_configured: true,
      })],
    })
    render(<ComposerStatusBar sessionId="s1" />)
    fireEvent.click(screen.getByTitle('选择模型'))
    const option = screen.getByText('MiniMax-M3').closest('button')!
    expect(option.hasAttribute('disabled')).toBe(false)
    fireEvent.click(option)
    expect(useModelStore.getState().sessionModels['s1']).toBe('minimax/MiniMax-M3')
  })

  // ---- 人设（persona）选择器并入状态行（R-014 F4）----
  it('renders the persona selector in the same row', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    expect(screen.getByTitle('选择对话智能体')).toBeDefined()
    expect(screen.getByText('通用助手')).toBeDefined()
  })

  it('opens the persona dropdown and lists personas', () => {
    usePersonaStore.setState({
      personas: [
        { id: 'chat', name: '通用助手', description: '默认聊天助手' },
        { id: 'researcher', name: '研究员', description: '深度资料调研' },
      ],
      loaded: true,
    } as never)
    render(<ComposerStatusBar sessionId="s1" />)
    fireEvent.click(screen.getByTitle('选择对话智能体'))
    expect(screen.getByText('研究员')).toBeDefined()
    expect(screen.getByText('深度资料调研')).toBeDefined()
  })

  it('switches the session persona', () => {
    const setSessionPersona = vi.fn()
    usePersonaStore.setState({
      personas: [{ id: 'researcher', name: '研究员', description: '深度资料调研' }],
      loaded: true,
      setSessionPersona,
    } as never)
    render(<ComposerStatusBar sessionId="s1" />)
    fireEvent.click(screen.getByTitle('选择对话智能体'))
    fireEvent.click(screen.getByText('研究员'))
    expect(setSessionPersona).toHaveBeenCalledWith('s1', 'researcher')
  })

  it('locks the persona on orchestrator sessions', () => {
    render(<ComposerStatusBar sessionId="dag:wf-1" />)
    const btn = screen.getByTitle('编排会话固定使用编排助手人设')
    expect(btn.hasAttribute('disabled')).toBe(false)
    expect(screen.getByText('编排助手')).toBeDefined()
  })
})
