// 设计定稿 2026-11-25：上下文用量环从状态栏移到 Composer 卡片右上角，
// hover 向上弹七段明细；发送按钮绝对定位卡片右下角。

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { enableMapSet } from 'immer'

enableMapSet()

vi.mock('../api/client', () => ({ api: { post: vi.fn(), get: vi.fn() } }))
const sessionMock = vi.hoisted(() => ({ id: 'sess-1' as string | null }))
vi.mock('../contexts/ChatSessionContext', () => ({ useChatSessionId: () => sessionMock.id }))
vi.mock('../components/chat/ComposerStatusBar', () => ({ ComposerStatusBar: () => <div data-testid="statusbar" /> }))
vi.mock('../components/chat/SlashCommandMenu', () => ({ SlashCommandMenu: () => null }))

import { Composer } from '../components/chat/Composer'
import { useChatStore } from '../stores/chat'
import { useSystemStore } from '../stores/system'
import { useModeStore } from '../stores/mode'
import { useModelStore } from '../stores/model'
import { useThinkingPrefStore } from '../stores/thinkingPref'
import { usePersonaStore } from '../stores/personas'

beforeEach(() => {
  sessionMock.id = 'sess-1'
  localStorage.clear()
  useChatStore.setState({
    messages: new Map(), streamingMessageId: null, activeAttemptId: null,
    tokensUsed: new Map(), contextBreakdown: new Map(),
  } as never)
  useSystemStore.setState({ modelInfo: { model: 'MiniMax-M3.1-Flash', context_tokens: 1_000_000, max_output_tokens: 128000 } as any })
  useModeStore.setState({ mode: 'build' } as never)
  useModelStore.setState({ sessionModels: {}, providers: [] } as never)
  useThinkingPrefStore.setState({ thinkingMode: 'auto' } as never)
  usePersonaStore.setState({ sessionPersona: {}, personas: [], loaded: true } as never)
})

describe('Composer · context usage ring at card top-right (design 2026-11-25)', () => {
  it('renders the bounded progress ring with hover breakdown outside the status bar', () => {
    useChatStore.setState({
      contextBreakdown: new Map([['sess-1', { system_prompt: 2374, messages: 2 }]]),
    } as never)
    render(<Composer />)
    const ring = screen.getByTestId('composer-context-ring')
    expect(ring).toBeDefined()
    // 与状态栏分离：状态栏是 mock 的独立节点，环不在其中
    expect(screen.getByTestId('statusbar').contains(ring)).toBe(false)
    // 环有 hover 明细，含数值与分项
    const popover = screen.getByTestId('context-breakdown-popover')
    expect(popover.textContent).toContain('2.4K')
    expect(popover.textContent).toContain('系统提示')
  })

  // 2026-11-27 回退契约：小环保持单色 tone（不用 7 段 viz 分色）。
  // 20px 下 7 段读不出用量状态且 system_prompt 恒主导 → 环恒紫、丢失
  // 绿/黄/红状态语义（真机评估结论）；分段明细只在 hover 弹层呈现。
  it('keeps the small ring single-tone (no segment arcs at 20px; reverted §9 A)', () => {
    useChatStore.setState({
      contextBreakdown: new Map([['sess-1', { system_prompt: 2374, messages: 2 }]]),
    } as never)
    render(<Composer />)
    const ring = screen.getByTestId('composer-context-ring')
    // 有分段数据也不画分段弧
    expect(ring.querySelector('[data-testid^="context-ring-seg-"]')).toBeNull()
    // 单色 tone 弧在场
    expect(ring.querySelector('[data-testid="context-ring-single"]')).not.toBeNull()
  })

  it('renders ring only when the model exposes a context limit', () => {
    useSystemStore.setState({ modelInfo: null } as any)
    render(<Composer />)
    expect(screen.queryByTestId('composer-context-ring')).toBeNull()
  })

  it('marks the ring aria-hidden (visual only; numbers live in the hover popover)', () => {
    render(<Composer />)
    const ring = screen.getByTestId('composer-context-ring')
    expect(ring.getAttribute('aria-hidden')).toBe('true')
  })

  it('keeps the stale-model warning next to the ring (regression from status-bar migration)', () => {
    useSystemStore.setState({ modelInfo: {
      model: 'stale-model', context_tokens: 1000000, max_output_tokens: 128000,
      source: 'fallback',
    } as any })
    render(<Composer />)
    expect(screen.getByTestId('composer-context-stale')).toBeDefined()
  })

  it('pins the send button to the composer card bottom-right', () => {
    render(<Composer />)
    const send = screen.getByTestId('composer-send')
    expect(send.className).toContain('absolute')
    expect(send.className).toContain('bottom-3')
    expect(send.className).toContain('right-4')
  })
})
