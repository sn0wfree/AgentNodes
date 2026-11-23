// TopBar 重命名信号消费（入口合并：侧栏 hover → 顶栏编辑）
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { TopBar } from '../components/layout/TopBar'
import { useSessionStore } from '../stores/session'
import { useLayoutStore } from '../stores/layout'

vi.mock('../stores/theme', () => ({
  useThemeStore: (sel: any) => sel({ theme: 'dark', toggleTheme: vi.fn() }),
}))
vi.mock('../stores/thinkingPref', () => ({
  useThinkingPrefStore: (sel: any) => sel({ collapsed: true, setCollapsed: vi.fn() }),
}))
vi.mock('../stores/model', () => ({
  useModelStore: (sel: any) => sel({
    providers: [], sessionModels: {}, setSessionModel: vi.fn(),
    clearSessionModel: vi.fn(), loadProviders: vi.fn().mockResolvedValue(undefined),
  }),
}))
vi.mock('../stores/system', () => ({
  useSystemStore: (sel: any) => sel({ modelInfo: { model: 'test', context_tokens: 1000 } }),
}))
vi.mock('../components/common/SSEStatus', () => ({ SSEStatus: () => null }))

describe('TopBar rename signal', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [{ id: 's1', title: 'Alpha', created_at: 1, updated_at: 1,
                   starred: false, tags: [], message_count: 0, archived: false }],
      currentSessionId: 's1',
      updateSessionMeta: vi.fn().mockResolvedValue(undefined),
      setSearchOpen: vi.fn(),
    } as never)
    useLayoutStore.setState({
      renameRequestId: null, rightPanelVisible: true, density: 'comfortable',
      toggleRightPanel: vi.fn(), setDensity: vi.fn(),
    } as never)
  })

  it('enters edit mode when the rename signal matches the current session', async () => {
    render(<MemoryRouter><TopBar /></MemoryRouter>)

    fireEvent.click(screen.getByTitle('重命名'))
    expect(screen.getByDisplayValue('Alpha')).toBeTruthy()

    fireEvent.change(screen.getByDisplayValue('Alpha'), { target: { value: 'Renamed' } })
    fireEvent.keyDown(screen.getByDisplayValue('Renamed'), { key: 'Enter' })

    await waitFor(() =>
      expect(useSessionStore.getState().updateSessionMeta).toHaveBeenCalledWith('s1', { title: 'Renamed' })
    )
  })

  it('consumes the signal from the sidebar and clears it', async () => {
    render(<MemoryRouter><TopBar /></MemoryRouter>)
    useLayoutStore.getState().requestRename('s1')

    await waitFor(() => expect(screen.getByDisplayValue('Alpha')).toBeTruthy())
    expect(useLayoutStore.getState().renameRequestId).toBeNull()
  })

  it('ignores a signal for a different session', () => {
    render(<MemoryRouter><TopBar /></MemoryRouter>)
    useLayoutStore.getState().requestRename('other')
    expect(screen.queryByDisplayValue('Alpha')).toBeNull()
  })
})
