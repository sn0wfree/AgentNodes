import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useLayoutStore } from '../stores/layout'

describe('useLayoutStore', () => {
  beforeEach(() => {
    localStorage.clear()
    useLayoutStore.setState({
      navWidth: 64,
      rightPanelVisible: true,
      workMode: 'monitor',
      rightRatio: 0.30,
      density: 'comfortable',
      settingsOpen: false,
      chatLayout: 'bubble',
    })
  })

  it('toggles right panel visibility', () => {
    const initial = useLayoutStore.getState().rightPanelVisible
    useLayoutStore.getState().toggleRightPanel()
    expect(useLayoutStore.getState().rightPanelVisible).toBe(!initial)
  })

  it('persists right panel visibility to localStorage', () => {
    useLayoutStore.getState().toggleRightPanel()
    expect(localStorage.getItem('sr-right-visible')).toBe('false')
    useLayoutStore.getState().toggleRightPanel()
    expect(localStorage.getItem('sr-right-visible')).toBe('true')
  })

  it('keeps desktop panel choices when opening and closing mobile drawers', () => {
    useLayoutStore.getState().setSidebarOpen(true)
    useLayoutStore.getState().setNarrowViewport(true)
    expect(useLayoutStore.getState().sidebarOpen).toBe(false)
    expect(useLayoutStore.getState().rightPanelVisible).toBe(false)

    useLayoutStore.getState().toggleSidebar()
    expect(useLayoutStore.getState().sidebarOpen).toBe(true)
    useLayoutStore.getState().toggleRightPanel()
    expect(useLayoutStore.getState().sidebarOpen).toBe(false)
    expect(useLayoutStore.getState().rightPanelVisible).toBe(true)
    expect(localStorage.getItem('sr-sidebar-open')).toBe('true')
    expect(localStorage.getItem('sr-right-visible')).toBeNull()

    useLayoutStore.getState().setNarrowViewport(false)
    expect(useLayoutStore.getState().sidebarOpen).toBe(true)
    expect(useLayoutStore.getState().rightPanelVisible).toBe(true)
  })

  it('loads right panel visibility from localStorage', async () => {
    localStorage.setItem('sr-right-visible', 'false')
    vi.resetModules()
    const { useLayoutStore: fresh } = await import('../stores/layout')
    expect(fresh.getState().rightPanelVisible).toBe(false)
    localStorage.setItem('sr-right-visible', 'true')
    vi.resetModules()
    const { useLayoutStore: fresh2 } = await import('../stores/layout')
    expect(fresh2.getState().rightPanelVisible).toBe(true)
  })

  it('switches work mode', () => {
    useLayoutStore.getState().setWorkMode('chat')
    expect(useLayoutStore.getState().workMode).toBe('chat')
    expect(useLayoutStore.getState().rightPanelVisible).toBe(false)
  })

  it('setWorkMode("monitor") forces rightPanelVisible=true', () => {
    useLayoutStore.setState({ rightPanelVisible: false })
    useLayoutStore.getState().setWorkMode('monitor')
    expect(useLayoutStore.getState().workMode).toBe('monitor')
    expect(useLayoutStore.getState().rightPanelVisible).toBe(true)
  })

  it('defaults rightRatio to comfortable preset (0.30)', () => {
    expect(useLayoutStore.getState().rightRatio).toBe(0.30)
  })

  it('clamps rightRatio to [0.25, 0.55] and persists', () => {
    useLayoutStore.getState().setRightRatio(0.9)
    expect(useLayoutStore.getState().rightRatio).toBe(0.55)

    useLayoutStore.getState().setRightRatio(0.1)
    expect(useLayoutStore.getState().rightRatio).toBe(0.25)

    useLayoutStore.getState().setRightRatio(0.4)
    expect(useLayoutStore.getState().rightRatio).toBe(0.4)
    expect(localStorage.getItem('sr-right-ratio')).toBe('0.4')
  })

  it('defaults chatLayout to bubble', () => {
    expect(useLayoutStore.getState().chatLayout).toBe('bubble')
  })

  it('switches chatLayout to flat and persists to localStorage', () => {
    useLayoutStore.getState().setChatLayout('flat')
    expect(useLayoutStore.getState().chatLayout).toBe('flat')
    expect(localStorage.getItem('sr-chat-layout')).toBe('flat')
  })

  it('switches chatLayout back to bubble and updates localStorage', () => {
    useLayoutStore.getState().setChatLayout('flat')
    useLayoutStore.getState().setChatLayout('bubble')
    expect(useLayoutStore.getState().chatLayout).toBe('bubble')
    expect(localStorage.getItem('sr-chat-layout')).toBe('bubble')
  })

  it('defaults density to comfortable', () => {
    expect(useLayoutStore.getState().density).toBe('comfortable')
  })

  it('setDensity applies preset width and persists', () => {
    useLayoutStore.getState().setDensity('compact')
    expect(useLayoutStore.getState().density).toBe('compact')
    expect(useLayoutStore.getState().rightRatio).toBe(0.28)
    expect(localStorage.getItem('sr-density')).toBe('compact')
  })

  it('setDensity("spacious") applies spacious preset', () => {
    useLayoutStore.getState().setDensity('spacious')
    expect(useLayoutStore.getState().density).toBe('spacious')
    expect(useLayoutStore.getState().rightRatio).toBe(0.34)
  })

  it('setDensity overwrites manual ratio override', () => {
    useLayoutStore.getState().setRightRatio(0.5)
    useLayoutStore.getState().setDensity('compact')
    expect(useLayoutStore.getState().rightRatio).toBe(0.28)
    expect(localStorage.getItem('sr-right-ratio')).toBeNull()
  })
})
