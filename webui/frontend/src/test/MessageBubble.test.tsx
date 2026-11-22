import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MessageBubble } from '../components/chat/MessageBubble'
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