// T1.4 — preference store branches (theme / mode / thinkingPref / model).
// None of these four had a dedicated test. Focus: localStorage
// round-trips, initial-value loading, toggle boundaries, and the
// defensive branches (invalid persisted values, clearing a missing key).

import { describe, it, expect, beforeEach, vi } from 'vitest'

// Stores read localStorage at module-eval time, so each block re-imports
// the module after seeding. vi.resetModules() keeps them independent.
beforeEach(() => {
  localStorage.clear()
  vi.resetModules()
  delete document.documentElement.dataset.theme
})

describe('theme store', () => {
  it('defaults to dark with no persisted value', async () => {
    const { useThemeStore } = await import('../stores/theme')
    expect(useThemeStore.getState().theme).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('loads persisted light + applies it on import', async () => {
    localStorage.setItem('sr-theme', 'light')
    const { useThemeStore } = await import('../stores/theme')
    expect(useThemeStore.getState().theme).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('treats an invalid persisted value as dark', async () => {
    localStorage.setItem('sr-theme', 'sepia')
    const { useThemeStore } = await import('../stores/theme')
    expect(useThemeStore.getState().theme).toBe('dark')
  })

  it('setTheme applies + persists', async () => {
    const { useThemeStore } = await import('../stores/theme')
    useThemeStore.getState().setTheme('light')
    expect(useThemeStore.getState().theme).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(localStorage.getItem('sr-theme')).toBe('light')
  })

  it('toggleTheme flips both ways and persists', async () => {
    const { useThemeStore } = await import('../stores/theme')
    useThemeStore.getState().toggleTheme()
    expect(useThemeStore.getState().theme).toBe('light')
    expect(localStorage.getItem('sr-theme')).toBe('light')
    useThemeStore.getState().toggleTheme()
    expect(useThemeStore.getState().theme).toBe('dark')
    expect(localStorage.getItem('sr-theme')).toBe('dark')
  })
})

describe('mode store', () => {
  it('defaults to build', async () => {
    const { useModeStore } = await import('../stores/mode')
    expect(useModeStore.getState().mode).toBe('build')
  })

  it('setMode + toggleMode', async () => {
    const { useModeStore } = await import('../stores/mode')
    useModeStore.getState().setMode('plan')
    expect(useModeStore.getState().mode).toBe('plan')
    useModeStore.getState().toggleMode()
    expect(useModeStore.getState().mode).toBe('build')
    useModeStore.getState().toggleMode()
    expect(useModeStore.getState().mode).toBe('plan')
  })

  it('loadSessionMode reads the per-session key', async () => {
    localStorage.setItem('sr-mode-sess-1', 'plan')
    const { useModeStore } = await import('../stores/mode')
    useModeStore.getState().loadSessionMode('sess-1')
    expect(useModeStore.getState().mode).toBe('plan')
  })

  it('loadSessionMode falls back to build for an unknown session', async () => {
    const { useModeStore } = await import('../stores/mode')
    useModeStore.getState().setMode('plan')
    useModeStore.getState().loadSessionMode('sess-unknown')
    expect(useModeStore.getState().mode).toBe('build')
  })

  it('ignores invalid persisted mode values', async () => {
    localStorage.setItem('sr-mode-sess-2', 'yolo')
    const { useModeStore } = await import('../stores/mode')
    useModeStore.getState().loadSessionMode('sess-2')
    expect(useModeStore.getState().mode).toBe('build')
  })

  it('persists mode changes once a session id is registered', async () => {
    const { useModeStore } = await import('../stores/mode')
    ;(window as unknown as { __srModeSession: (id: string) => void }).__srModeSession('sess-9')
    useModeStore.getState().setMode('plan')
    expect(localStorage.getItem('sr-mode-sess-9')).toBe('plan')
  })

  it('does not persist before any session id is registered', async () => {
    localStorage.clear()
    const { useModeStore } = await import('../stores/mode')
    useModeStore.getState().setMode('plan')
    expect(Object.keys(localStorage).filter((k) => k.startsWith('sr-mode-'))).toHaveLength(0)
  })
})

describe('thinkingPref store', () => {
  it('defaults: collapsed true / enabled true / mode auto', async () => {
    const { useThinkingPrefStore } = await import('../stores/thinkingPref')
    const s = useThinkingPrefStore.getState()
    expect(s.collapsed).toBe(true)
    expect(s.enabled).toBe(true)
    expect(s.thinkingMode).toBe('auto')
  })

  it('loads persisted false values', async () => {
    localStorage.setItem('sr-thinking-collapsed', 'false')
    localStorage.setItem('sr-thinking-enabled', 'false')
    localStorage.setItem('sr-thinking-mode', 'off')
    const { useThinkingPrefStore } = await import('../stores/thinkingPref')
    const s = useThinkingPrefStore.getState()
    expect(s.collapsed).toBe(false)
    expect(s.enabled).toBe(false)
    expect(s.thinkingMode).toBe('off')
  })

  it('treats any non-"false" persisted collapse/disable value as true', async () => {
    localStorage.setItem('sr-thinking-collapsed', 'garbage')
    localStorage.setItem('sr-thinking-enabled', 'garbage')
    const { useThinkingPrefStore } = await import('../stores/thinkingPref')
    expect(useThinkingPrefStore.getState().collapsed).toBe(true)
    expect(useThinkingPrefStore.getState().enabled).toBe(true)
  })

  it('rejects an invalid persisted thinking mode', async () => {
    localStorage.setItem('sr-thinking-mode', 'sometimes')
    const { useThinkingPrefStore } = await import('../stores/thinkingPref')
    expect(useThinkingPrefStore.getState().thinkingMode).toBe('auto')
  })

  it('set/toggle collapsed persist each time', async () => {
    const { useThinkingPrefStore } = await import('../stores/thinkingPref')
    useThinkingPrefStore.getState().setCollapsed(false)
    expect(localStorage.getItem('sr-thinking-collapsed')).toBe('false')
    useThinkingPrefStore.getState().toggle()
    expect(useThinkingPrefStore.getState().collapsed).toBe(true)
    expect(localStorage.getItem('sr-thinking-collapsed')).toBe('true')
  })

  it('set/toggle enabled persist each time', async () => {
    const { useThinkingPrefStore } = await import('../stores/thinkingPref')
    useThinkingPrefStore.getState().setEnabled(false)
    expect(localStorage.getItem('sr-thinking-enabled')).toBe('false')
    useThinkingPrefStore.getState().toggleEnabled()
    expect(localStorage.getItem('sr-thinking-enabled')).toBe('true')
  })

  it('setThinkingMode persists all three modes', async () => {
    const { useThinkingPrefStore } = await import('../stores/thinkingPref')
    for (const m of ['off', 'on', 'auto'] as const) {
      useThinkingPrefStore.getState().setThinkingMode(m)
      expect(useThinkingPrefStore.getState().thinkingMode).toBe(m)
      expect(localStorage.getItem('sr-thinking-mode')).toBe(m)
    }
  })
})

describe('model store', () => {
  it('starts with no session overrides + empty providers', async () => {
    const { useModelStore } = await import('../stores/model')
    expect(useModelStore.getState().sessionModels).toEqual({})
    expect(useModelStore.getState().providers).toEqual([])
  })

  it('loads persisted session overrides', async () => {
    localStorage.setItem('sr-session-models', JSON.stringify({ 'sess-1': 'p/m1' }))
    const { useModelStore } = await import('../stores/model')
    expect(useModelStore.getState().getModelForSession('sess-1')).toBe('p/m1')
  })

  it('survives corrupted persisted JSON', async () => {
    localStorage.setItem('sr-session-models', '{not json')
    const { useModelStore } = await import('../stores/model')
    expect(useModelStore.getState().sessionModels).toEqual({})
  })

  it('setSessionModel persists and overrides', async () => {
    const { useModelStore } = await import('../stores/model')
    useModelStore.getState().setSessionModel('sess-1', 'p/m1')
    expect(useModelStore.getState().getModelForSession('sess-1')).toBe('p/m1')
    useModelStore.getState().setSessionModel('sess-1', 'p/m2')
    expect(localStorage.getItem('sr-session-models')).toBe(
      JSON.stringify({ 'sess-1': 'p/m2' })
    )
  })

  it('getModelForSession returns null for unknown sessions', async () => {
    const { useModelStore } = await import('../stores/model')
    expect(useModelStore.getState().getModelForSession('nope')).toBeNull()
  })

  it('clearSessionModel removes + persists, and clearing a missing key is safe', async () => {
    const { useModelStore } = await import('../stores/model')
    useModelStore.getState().setSessionModel('sess-1', 'p/m1')
    useModelStore.getState().clearSessionModel('sess-1')
    expect(useModelStore.getState().getModelForSession('sess-1')).toBeNull()
    expect(localStorage.getItem('sr-session-models')).toBe('{}')
    useModelStore.getState().clearSessionModel('never-existed')
    expect(useModelStore.getState().sessionModels).toEqual({})
  })

  it('loadProviders stores the provider catalog', async () => {
    const { useModelStore } = await import('../stores/model')
    const { api } = await import('../api/client')
    const spy = vi.spyOn(api, 'get').mockResolvedValueOnce({
      providers: [
        {
          name: 'p1', label: 'P1', model: 'm1', models: ['m1'],
          base_url: 'u', key_var: 'K', key_configured: true,
        },
      ],
    })
    await useModelStore.getState().loadProviders()
    expect(useModelStore.getState().providers).toHaveLength(1)
    expect(useModelStore.getState().providers[0]!.name).toBe('p1')
    spy.mockRestore()
  })

  it('loadProviders swallows backend failure and keeps providers empty', async () => {
    const { useModelStore } = await import('../stores/model')
    const { api } = await import('../api/client')
    const spy = vi.spyOn(api, 'get').mockRejectedValueOnce(new Error('down'))
    await expect(useModelStore.getState().loadProviders()).resolves.toBeUndefined()
    expect(useModelStore.getState().providers).toEqual([])
    spy.mockRestore()
  })

  it('loadProviders ignores a payload without providers key', async () => {
    const { useModelStore } = await import('../stores/model')
    const { api } = await import('../api/client')
    const spy = vi.spyOn(api, 'get').mockResolvedValueOnce({})
    await useModelStore.getState().loadProviders()
    expect(useModelStore.getState().providers).toEqual([])
    spy.mockRestore()
  })
})
