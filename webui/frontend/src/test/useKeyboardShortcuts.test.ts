import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, fireEvent } from '@testing-library/react'

// Hoisted mocks (vitest moves these above imports).
const mocks = vi.hoisted(() => ({
  togglePalette: vi.fn(),
  toggleRightPanel: vi.fn(),
  setSearchOpen: vi.fn(),
  createNewSession: vi.fn(),
  closeSession: vi.fn(),
  switchSession: vi.fn(),
  navigate: vi.fn(),
  toggleSidebar: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('../stores/commandPalette', () => ({
  useCommandPaletteStore: (sel: (s: any) => any) =>
    sel({ toggle: mocks.togglePalette }),
}))

vi.mock('../stores/layout', () => ({
  useLayoutStore: (sel: (s: any) => any) => {
    const state = {
      toggleRightPanel: mocks.toggleRightPanel,
      toggleSidebar: mocks.toggleSidebar,
    }
    return sel(state)
  },
}))

vi.mock('../stores/session', () => ({
  useSessionStore: (sel: (s: any) => any) => {
    const state = {
      setSearchOpen: mocks.setSearchOpen,
      createNewSession: mocks.createNewSession,
      closeSession: mocks.closeSession,
      switchSession: mocks.switchSession,
      currentSessionId: 's-active',
      openSessionIds: ['s-active', 's-other'],
    }
    return sel(state)
  },
}))

// Import after mocks so the hook picks them up.
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts'

function pressKey(key: string, init: Partial<KeyboardEventInit> = {}) {
  fireEvent.keyDown(document, {
    key,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    ...init,
  })
}

describe('useKeyboardShortcuts', () => {
  beforeEach(() => {
    Object.values(mocks).forEach((m) => m.mockClear())
  })

  // ── P43: Command palette on ⌘P / ⌘⇧P ──

  it('Cmd+P opens the command palette', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('p', { metaKey: true })
    expect(mocks.togglePalette).toHaveBeenCalledTimes(1)
  })

  it('Cmd+Shift+P also opens the command palette', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('P', { metaKey: true, shiftKey: true })
    expect(mocks.togglePalette).toHaveBeenCalledTimes(1)
  })

  it('Ctrl+P also opens the command palette (Linux/Windows parity)', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('p', { ctrlKey: true })
    expect(mocks.togglePalette).toHaveBeenCalledTimes(1)
  })

  it('Cmd+P inside an INPUT still triggers the palette', () => {
    // The shortcut is added to the skipIfInput allow-list alongside
    // ⌘K (search) and ⌘T (new tab), so typing in the composer must
    // not eat the shortcut.
    renderHook(() => useKeyboardShortcuts())
    const input = document.createElement('input')
    document.body.appendChild(input)
    fireEvent.keyDown(input, { key: 'p', metaKey: true })
    expect(mocks.togglePalette).toHaveBeenCalledTimes(1)
    document.body.removeChild(input)
  })

  it('plain "p" (no modifier) does NOT open the palette', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('p')
    expect(mocks.togglePalette).not.toHaveBeenCalled()
  })

  // ── pre-existing bindings still wired ──

  it('Cmd+K opens the search modal', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('k', { metaKey: true })
    expect(mocks.setSearchOpen).toHaveBeenCalledWith(true)
  })

  it('Cmd+B toggles the right panel', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('b', { metaKey: true })
    expect(mocks.toggleRightPanel).toHaveBeenCalledTimes(1)
  })

  it('Cmd+T creates a new session and navigates to /chat', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('t', { metaKey: true })
    expect(mocks.createNewSession).toHaveBeenCalledWith('新会话')
    expect(mocks.navigate).toHaveBeenCalledWith('/chat')
    expect(mocks.toggleSidebar).toHaveBeenCalledTimes(1)
  })

  // ── T1.5 additions: remaining inline bindings + input guards ──

  it('Cmd+W navigates to /trace', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('w', { metaKey: true })
    expect(mocks.navigate).toHaveBeenCalledWith('/trace')
    expect(mocks.closeSession).not.toHaveBeenCalled()
  })

  it('Cmd+Shift+W closes the active session (and does not navigate)', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('W', { metaKey: true, shiftKey: true })
    expect(mocks.closeSession).toHaveBeenCalledWith('s-active')
    expect(mocks.navigate).not.toHaveBeenCalled()
  })

  it.each([
    ['1', 's-active'],
    ['2', 's-other'],
  ])('Cmd+%s switches to the matching open tab', (key, expected) => {
    renderHook(() => useKeyboardShortcuts())
    pressKey(key, { metaKey: true })
    expect(mocks.switchSession).toHaveBeenCalledWith(expected)
  })

  it('Cmd+3 (no third open tab) is a no-op, not an error', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('3', { metaKey: true })
    expect(mocks.switchSession).not.toHaveBeenCalled()
  })

  it('Cmd+B inside a TEXTAREA is ignored (input guard)', () => {
    renderHook(() => useKeyboardShortcuts())
    const ta = document.createElement('textarea')
    document.body.appendChild(ta)
    fireEvent.keyDown(ta, { key: 'b', metaKey: true })
    expect(mocks.toggleRightPanel).not.toHaveBeenCalled()
    document.body.removeChild(ta)
  })

  it('Cmd+K inside a contentEditable is still allowed (allow-list)', () => {
    renderHook(() => useKeyboardShortcuts())
    const div = document.createElement('div')
    div.contentEditable = 'true'
    document.body.appendChild(div)
    fireEvent.keyDown(div, { key: 'k', metaKey: true })
    expect(mocks.setSearchOpen).toHaveBeenCalledWith(true)
    document.body.removeChild(div)
  })

  it('unmount removes the keydown listener', () => {
    const { unmount } = renderHook(() => useKeyboardShortcuts())
    unmount()
    pressKey('k', { metaKey: true })
    expect(mocks.setSearchOpen).not.toHaveBeenCalled()
  })
})