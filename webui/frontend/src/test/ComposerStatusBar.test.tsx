import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ComposerStatusBar } from '../components/chat/ComposerStatusBar'
import { useModeStore } from '../stores/mode'
import { useModelStore } from '../stores/model'
import { useThinkingPrefStore } from '../stores/thinkingPref'
import { useSystemStore } from '../stores/system'

beforeEach(() => {
  // Reset stores
  useModeStore.setState({ mode: 'build' })
  useModelStore.setState({ providers: [], sessionModels: {} })
  useThinkingPrefStore.setState({ thinkingMode: 'auto' })
  useSystemStore.setState({ modelInfo: { model: 'test-model', context_tokens: 128000 } as any })
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

  it('renders context tokens', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    expect(screen.getByText('128K')).toBeDefined()
  })

  // ---- 模型下拉密钥门禁（model-picker-key-gating-20261124）----
  const mkProvider = (over: Record<string, unknown> = {}) => ({
    name: 'deepseek', label: 'deepseek', model: 'deepseek-chat',
    models: ['deepseek-chat'], base_url: '', key_var: 'DEEPSEEK_API_KEY',
    key_configured: false, ...over,
  })

  it('disables and annotates models from providers without a key', () => {
    useModelStore.setState({ providers: [mkProvider()] })
    render(<ComposerStatusBar sessionId="s1" />)
    fireEvent.click(screen.getByTitle('选择模型'))
    const option = screen.getByText('deepseek-chat').closest('button')!
    expect(option.hasAttribute('disabled')).toBe(true)
    expect(screen.getByText('未配置密钥')).toBeDefined()
    expect(screen.getByTitle('未配置密钥（DEEPSEEK_API_KEY）')).toBeDefined()
  })

  it('does not select a disabled no-key model on click', () => {
    useModelStore.setState({ providers: [mkProvider()] })
    render(<ComposerStatusBar sessionId="s1" />)
    fireEvent.click(screen.getByTitle('选择模型'))
    fireEvent.click(screen.getByText('deepseek-chat'))
    expect(useModelStore.getState().sessionModels['s1']).toBeUndefined()
  })

  it('allows selecting a model from a provider with a key', () => {
    useModelStore.setState({
      providers: [mkProvider({
        name: 'minimax', label: 'MiniMax', model: 'MiniMax-M3',
        models: ['MiniMax-M3'], key_var: 'MINIMAX_API_KEY', key_configured: true,
      })],
    })
    render(<ComposerStatusBar sessionId="s1" />)
    fireEvent.click(screen.getByTitle('选择模型'))
    const option = screen.getByText('MiniMax-M3').closest('button')!
    expect(option.hasAttribute('disabled')).toBe(false)
    fireEvent.click(option)
    expect(useModelStore.getState().sessionModels['s1']).toBe('minimax/MiniMax-M3')
  })
})
