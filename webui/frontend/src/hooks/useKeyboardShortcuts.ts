import { useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useCommandPaletteStore } from '../stores/commandPalette'
import { useLayoutStore } from '../stores/layout'
import { useSessionStore } from '../stores/session'

type KeyBinding = {
  key: string
  meta?: boolean
  shift?: boolean
  action: () => void
  ignoreInputs?: boolean
}

export function useKeyboardShortcuts() {
  const navigate = useNavigate()
  const togglePalette = useCommandPaletteStore((s) => s.toggle)
  const toggleRightPanel = useLayoutStore((s) => s.toggleRightPanel)
  const setSearchOpen = useSessionStore((s) => s.setSearchOpen)
  const createNewSession = useSessionStore((s) => s.createNewSession)
  const closeSession = useSessionStore((s) => s.closeSession)
  const switchSession = useSessionStore((s) => s.switchSession)
  const openSessionIds = useSessionStore((s) => s.openSessionIds)
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const toggleSidebar = useLayoutStore((s) => s.toggleSidebar)

  const bindings: KeyBinding[] = [
    // Search
    { key: 'k', meta: true, action: () => setSearchOpen(true) },
    // Command palette (opencode-style ⌘P; ⌘⇧P also accepted)
    { key: 'p', meta: true, action: togglePalette },
    { key: 'p', meta: true, shift: true, action: togglePalette },
    // Panel toggles
    { key: 'b', meta: true, action: toggleRightPanel },
    { key: 'w', meta: true, action: () => navigate('/trace') },
    // Tabs
    { key: '1', meta: true, action: () => openSessionIds[0] && void switchSession(openSessionIds[0]) },
    { key: '2', meta: true, action: () => openSessionIds[1] && void switchSession(openSessionIds[1]) },
    { key: '3', meta: true, action: () => openSessionIds[2] && void switchSession(openSessionIds[2]) },
    { key: '4', meta: true, action: () => openSessionIds[3] && void switchSession(openSessionIds[3]) },
    { key: '5', meta: true, action: () => openSessionIds[4] && void switchSession(openSessionIds[4]) },
    { key: '6', meta: true, action: () => openSessionIds[5] && void switchSession(openSessionIds[5]) },
    { key: '7', meta: true, action: () => openSessionIds[6] && void switchSession(openSessionIds[6]) },
    { key: '8', meta: true, action: () => openSessionIds[7] && void switchSession(openSessionIds[7]) },
    { key: '9', meta: true, action: () => openSessionIds[8] && void switchSession(openSessionIds[8]) },
    // New tab (⌘T) — jump into the chat workspace
    { key: 't', meta: true, action: () => { void createNewSession('新会话'); navigate('/chat'); toggleSidebar() } },
    // Close current tab (⌘W opens Trace — overlap, ignore ⌘W conflict)
    { key: 'w', meta: true, shift: true, action: () => currentSessionId && closeSession(currentSessionId) },
  ]

  const handler = useCallback(
    (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      const isInput = target && (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable
      )
      const isMeta = e.metaKey || e.ctrlKey

      for (const b of bindings) {
        const matchKey = b.key === e.key.toLowerCase()
        const matchMeta = (b.meta ?? false) === isMeta
        const matchShift = (b.shift ?? false) === e.shiftKey
        if (!matchKey || !matchMeta || !matchShift) continue
        // For palette/search (Cmd+K), still trigger even when in input
        const skipIfInput = isInput && !['k', 't', 'p'].includes(b.key)
        if (skipIfInput) continue
        e.preventDefault()
        b.action()
        return
      }
    },
    [
      togglePalette,
      toggleRightPanel,
      setSearchOpen,
      createNewSession,
      closeSession,
      switchSession,
      openSessionIds,
      currentSessionId,
      toggleSidebar,
    ]
  )

  useEffect(() => {
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handler])
}