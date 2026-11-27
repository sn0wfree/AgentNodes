import { describe, it, expect, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MessageBubble } from '../components/chat/MessageBubble'
import { chatUiConfig } from '../components/chat/chatUiConfig'
import type { Message } from '../stores/chat'

const baseMsg: Message = {
  id: 'm1',
  session_id: 's1',
  role: 'user',
  parts: [{ type: 'text', id: 'test-1', text: '你好' }],
  created_at: 1700000000,
}

describe('MessageBubble', () => {
  it('renders right-aligned bubble in bubble mode', () => {
    const { container } = render(<MessageBubble message={baseMsg} layout="bubble" />)
    const wrapper = container.firstChild as HTMLElement
    // Outer wrapper: flex justify-end
    expect(wrapper.className).toContain('justify-end')
    expect(container.querySelector('.user-message-bubble')).toBeTruthy()
    expect(screen.getByText('你好')).toBeTruthy()
  })

  it('renders left-aligned flat layout in flat mode', () => {
    const { container } = render(<MessageBubble message={baseMsg} layout="flat" />)
    const wrapper = container.firstChild as HTMLElement
    // No justify-end in flat mode
    expect(wrapper.className).not.toContain('justify-end')
    // "You" label visible
    expect(screen.getByText('You')).toBeTruthy()
    // Text content visible
    expect(screen.getByText('你好')).toBeTruthy()
  })
})

// ── P0-3: userBubble config drives labels + edit visibility ──
// The config is a shared singleton; mutations are restored per test.

const userBubbleBackup = JSON.parse(JSON.stringify(chatUiConfig.userBubble))
afterEach(() => {
  // Fresh clone each time: a direct re-assign would alias the backup to the
  // singleton after the first restore, letting mutations leak across tests.
  chatUiConfig.userBubble = JSON.parse(JSON.stringify(userBubbleBackup))
})

describe('MessageBubble — P0-3 config wiring', () => {
  it('allowEdit=false hides the edit button in both layouts', () => {
    chatUiConfig.userBubble.visibility.allowEdit = false
    const bubble = render(<MessageBubble message={baseMsg} layout="bubble" />)
    expect(bubble.queryByTitle('编辑')).toBeNull()
    bubble.unmount()
    const flat = render(<MessageBubble message={baseMsg} layout="flat" />)
    expect(flat.queryByTitle('编辑')).toBeNull()
  })

  it('allowEdit=true (default) keeps the edit button', () => {
    chatUiConfig.userBubble.visibility.allowEdit = true
    const { queryByTitle } = render(<MessageBubble message={baseMsg} layout="bubble" />)
    expect(queryByTitle('编辑')).toBeTruthy()
  })

  it('labels.userLabel drives the flat-mode author label', () => {
    chatUiConfig.userBubble.labels.userLabel = '测试者'
    render(<MessageBubble message={baseMsg} layout="flat" />)
    expect(screen.getByText('测试者')).toBeTruthy()
    expect(screen.queryByText('You')).toBeNull()
  })

  it('labels.editLabel drives the edit button title', () => {
    chatUiConfig.userBubble.labels.editLabel = '改这条'
    const { getByTitle } = render(<MessageBubble message={baseMsg} layout="bubble" />)
    expect(getByTitle('改这条')).toBeTruthy()
    expect(document.querySelector('[title="编辑"]')).toBeNull()
  })
})