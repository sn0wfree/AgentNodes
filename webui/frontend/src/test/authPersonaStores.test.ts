// T1.6a — auth + personas store branches.
// auth uses zustand/persist (localStorage hydration); personas caches the
// catalog and per-session selection. Both had uncovered branches.

import { describe, it, expect, beforeEach, vi } from 'vitest'

beforeEach(() => {
  localStorage.clear()
  vi.resetModules()
})

describe('auth store', () => {
  it('starts logged out', async () => {
    const { useAuthStore } = await import('../stores/auth')
    expect(useAuthStore.getState().token).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('setAuth stores token + user', async () => {
    const { useAuthStore } = await import('../stores/auth')
    useAuthStore.getState().setAuth('jwt-1', {
      id: 'u1', username: 'u', display_name: 'U',
    })
    expect(useAuthStore.getState().token).toBe('jwt-1')
    expect(useAuthStore.getState().user?.username).toBe('u')
  })

  it('setUser replaces only the user (token preserved)', async () => {
    const { useAuthStore } = await import('../stores/auth')
    useAuthStore.getState().setAuth('jwt-1', { id: 'u1', username: 'u', display_name: 'U' })
    useAuthStore.getState().setUser({ id: 'u1', username: 'u', display_name: 'Renamed' })
    expect(useAuthStore.getState().token).toBe('jwt-1')
    expect(useAuthStore.getState().user?.display_name).toBe('Renamed')
  })

  it('logout clears both', async () => {
    const { useAuthStore } = await import('../stores/auth')
    useAuthStore.getState().setAuth('jwt-1', { id: 'u1', username: 'u', display_name: 'U' })
    useAuthStore.getState().logout()
    expect(useAuthStore.getState().token).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('persists auth state to localStorage', async () => {
    const { useAuthStore } = await import('../stores/auth')
    useAuthStore.getState().setAuth('jwt-p', { id: 'u1', username: 'u', display_name: 'U' })
    const raw = localStorage.getItem('sr-auth')
    expect(raw).toBeTruthy()
    expect(JSON.parse(raw!).state.token).toBe('jwt-p')
  })

  it('refreshMe is a no-op without a token', async () => {
    const { useAuthStore } = await import('../stores/auth')
    const { api } = await import('../api/client')
    const spy = vi.spyOn(api, 'get')
    await useAuthStore.getState().refreshMe()
    expect(spy).not.toHaveBeenCalled()
    spy.mockRestore()
  })

  it('refreshMe updates the user from /auth/me', async () => {
    const { useAuthStore } = await import('../stores/auth')
    const { api } = await import('../api/client')
    useAuthStore.getState().setAuth('jwt-1', { id: 'u1', username: 'old', display_name: 'Old' })
    const spy = vi.spyOn(api, 'get').mockResolvedValueOnce({
      id: 'u1', username: 'new', display_name: 'New', role: 'admin', is_active: true,
    })
    await useAuthStore.getState().refreshMe()
    expect(useAuthStore.getState().user?.display_name).toBe('New')
    expect(useAuthStore.getState().user?.role).toBe('admin')
    spy.mockRestore()
  })

  it('refreshMe keeps the last-known user on failure', async () => {
    const { useAuthStore } = await import('../stores/auth')
    const { api } = await import('../api/client')
    useAuthStore.getState().setAuth('jwt-1', { id: 'u1', username: 'stay', display_name: 'Stay' })
    const spy = vi.spyOn(api, 'get').mockRejectedValueOnce(new Error('down'))
    await expect(useAuthStore.getState().refreshMe()).resolves.toBeUndefined()
    expect(useAuthStore.getState().user?.display_name).toBe('Stay')
    spy.mockRestore()
  })
})

describe('persona store', () => {
  it('starts empty and unloaded', async () => {
    const { usePersonaStore } = await import('../stores/personas')
    expect(usePersonaStore.getState().personas).toEqual([])
    expect(usePersonaStore.getState().loaded).toBe(false)
  })

  it('loadPersonas fetches once and marks loaded', async () => {
    const { usePersonaStore } = await import('../stores/personas')
    const { api } = await import('../api/client')
    const spy = vi.spyOn(api.personas, 'list').mockResolvedValueOnce({
      personas: [{ id: 'chat', name: 'Chat', description: 'd' }],
    } as never)
    await usePersonaStore.getState().loadPersonas()
    expect(usePersonaStore.getState().personas).toHaveLength(1)
    expect(usePersonaStore.getState().loaded).toBe(true)
    // Second call short-circuits on the loaded flag.
    await usePersonaStore.getState().loadPersonas()
    expect(spy).toHaveBeenCalledTimes(1)
    spy.mockRestore()
  })

  it('loadPersonas marks loaded even when the backend fails', async () => {
    const { usePersonaStore } = await import('../stores/personas')
    const { api } = await import('../api/client')
    const spy = vi.spyOn(api.personas, 'list').mockRejectedValueOnce(new Error('down'))
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    await usePersonaStore.getState().loadPersonas()
    expect(usePersonaStore.getState().loaded).toBe(true)
    expect(usePersonaStore.getState().personas).toEqual([])
    errSpy.mockRestore()
    spy.mockRestore()
  })

  it('loadPersonas tolerates a payload without personas', async () => {
    const { usePersonaStore } = await import('../stores/personas')
    const { api } = await import('../api/client')
    const spy = vi.spyOn(api.personas, 'list').mockResolvedValueOnce({} as never)
    await usePersonaStore.getState().loadPersonas()
    expect(usePersonaStore.getState().personas).toEqual([])
    spy.mockRestore()
  })

  it('getSessionPersona defaults to "chat"', async () => {
    const { usePersonaStore } = await import('../stores/personas')
    expect(usePersonaStore.getState().getSessionPersona('sess-x')).toBe('chat')
  })

  it('setSessionPersona persists and reads back', async () => {
    const { usePersonaStore } = await import('../stores/personas')
    usePersonaStore.getState().setSessionPersona('sess-1', 'analyst')
    expect(usePersonaStore.getState().getSessionPersona('sess-1')).toBe('analyst')
    expect(localStorage.getItem('sr-persona-sess-1')).toBe('analyst')
  })

  it('reads a previously persisted persona before any set', async () => {
    localStorage.setItem('sr-persona-sess-9', 'researcher')
    const { usePersonaStore } = await import('../stores/personas')
    expect(usePersonaStore.getState().getSessionPersona('sess-9')).toBe('researcher')
  })

  it('setSessionPersona survives a localStorage quota error', async () => {
    const { usePersonaStore } = await import('../stores/personas')
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceeded')
    })
    expect(() =>
      usePersonaStore.getState().setSessionPersona('sess-1', 'analyst')
    ).not.toThrow()
    expect(usePersonaStore.getState().getSessionPersona('sess-1')).toBe('analyst')
    spy.mockRestore()
  })
})
