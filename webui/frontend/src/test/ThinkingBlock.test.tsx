import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ThinkingBlock } from '../components/chat/ThinkingBlock'
import { chatUiConfig } from '../components/chat/chatUiConfig'
import { useThinkingPrefStore } from '../stores/thinkingPref'

// Mock navigator.clipboard
const writeText = vi.fn().mockResolvedValue(undefined)
Object.assign(navigator, { clipboard: { writeText } })

describe('ThinkingBlock', () => {
  beforeEach(() => {
    // Reset the store to default values between tests so the
    // enabled/disabled toggle has a deterministic starting point.
    useThinkingPrefStore.setState({
      collapsed: true,
      enabled: true,
    })
    writeText.mockClear()
  })

  it('renders single-line header when collapsed', () => {
    render(<ThinkingBlock text="reasoning content here" collapsed />)
    expect(screen.getByText(/Thought/)).toBeTruthy()
    // Content not visible until expanded
    expect(screen.queryByText('reasoning content here')).toBeNull()
  })

  it('shows content when expanded', () => {
    render(<ThinkingBlock text="reasoning content here" collapsed={false} />)
    expect(screen.getByText('reasoning content here')).toBeTruthy()
  })

  it('shows "Thinking…" while streaming without startTime', () => {
    render(<ThinkingBlock text="thinking" streaming />)
    // Either "Thinking…" or "Thinking for Xs" — both are valid.
    const el = screen.getByText(/Thinking/)
    expect(el).toBeTruthy()
  })

  it('shows "Thought for Xs" when both startTime and endTime provided', () => {
    const start = 1000
    const end = 3500
    render(<ThinkingBlock text="t" startTime={start} endTime={end} />)
    expect(screen.getByText(/Thought for 2\.5s/)).toBeTruthy()
  })

  it('does not render when text is empty', () => {
    const { container } = render(<ThinkingBlock text="" />)
    expect(container.firstChild).toBeNull()
  })

  it('copies text to clipboard when copy icon clicked', async () => {
    writeText.mockClear()
    render(<ThinkingBlock text="hello to copy" collapsed={false} />)
    const copyBtn = screen.getByTitle('复制')
    fireEvent.click(copyBtn)
    expect(writeText).toHaveBeenCalledWith('hello to copy')
  })

  // ── P21: global kill-switch ──

  it('renders nothing when global enabled=false', () => {
    useThinkingPrefStore.setState({ enabled: false })
    const { container } = render(
      <ThinkingBlock text="should not render" collapsed={false} />
    )
    expect(container.firstChild).toBeNull()
  })

  it('global enabled=false suppresses streaming blocks too', () => {
    useThinkingPrefStore.setState({ enabled: false })
    const { container } = render(<ThinkingBlock text="live" streaming />)
    expect(container.firstChild).toBeNull()
  })

  it('toggling enabled restores rendering', () => {
    useThinkingPrefStore.setState({ enabled: false })
    const { container, rerender } = render(
      <ThinkingBlock text="should appear" collapsed={false} />
    )
    expect(container.firstChild).toBeNull()
    useThinkingPrefStore.setState({ enabled: true })
    rerender(<ThinkingBlock text="should appear" collapsed={false} />)
    expect(screen.getByText('should appear')).toBeTruthy()
  })
})

// ── P0-3: thinking config drives labels / copy / char-count ──
// The config is a shared singleton; mutations are restored per test.

const thinkingBackup = JSON.parse(JSON.stringify(chatUiConfig.thinking))
afterEach(() => {
  // Fresh clone each time: a direct re-assign would alias the backup to the
  // singleton after the first restore, letting mutations leak across tests.
  chatUiConfig.thinking = JSON.parse(JSON.stringify(thinkingBackup))
})

describe('ThinkingBlock — P0-3 config wiring', () => {
  it('showCopy=false hides the copy button', () => {
    chatUiConfig.thinking.visibility.showCopy = false
    const { queryByTitle } = render(<ThinkingBlock text="no copy here" collapsed={false} />)
    expect(queryByTitle('复制')).toBeNull()
  })

  it('showCharCount=false hides the character count even for long text', () => {
    chatUiConfig.thinking.visibility.showCharCount = false
    const { container } = render(<ThinkingBlock text={'x'.repeat(80)} collapsed />)
    expect(container.textContent).not.toContain('字')
  })

  it('sizing.charThreshold drives when the char count appears', () => {
    chatUiConfig.thinking.sizing.charThreshold = 5
    render(<ThinkingBlock text="abcdef" collapsed />)
    expect(screen.getByText(/6字|6 字/)).toBeTruthy()
  })

  it('labels.streamingFormat drives the streaming header', () => {
    chatUiConfig.thinking.labels.streamingFormat = '思考中 {duration}'
    render(<ThinkingBlock text="live" streaming startTime={Date.now() - 4000} />)
    expect(screen.getByText(/思考中 4/)).toBeTruthy()
    expect(screen.queryByText(/Thinking for/)).toBeNull()
  })

  it('labels.doneFormat drives the sealed header', () => {
    chatUiConfig.thinking.labels.doneFormat = '思考了 {duration}'
    render(<ThinkingBlock text="t" startTime={1000} endTime={3500} />)
    expect(screen.getByText(/思考了 2\.5s/)).toBeTruthy()
    expect(screen.queryByText(/Thought for/)).toBeNull()
  })

  it('sizing.maxHeight drives the expanded content scroll cap', () => {
    chatUiConfig.thinking.sizing.maxHeight = 120
    const { container } = render(<ThinkingBlock text="long body" collapsed={false} />)
    const body = container.querySelector('[style*="max-height"]') as HTMLElement | null
    expect(body).toBeTruthy()
    expect(body!.style.maxHeight).toBe('120px')
  })
})