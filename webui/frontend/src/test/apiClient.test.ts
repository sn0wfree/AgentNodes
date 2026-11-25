import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useAuthStore } from '../stores/auth'

// Mock fetch globally before importing client
const mockFetch = vi.fn()
;(globalThis as any).fetch = mockFetch

// Now import the client (uses fetch)
const { api } = await import('../api/client')

// Mock EventSource
class MockEventSource {
  url: string
  onopen: ((ev?: any) => void) | null = null
  onerror: ((ev?: any) => void) | null = null
  listeners = new Map<string, (ev: any) => void>()
  constructor(url: string) {
    this.url = url
  }
  addEventListener(type: string, fn: (ev: any) => void) {
    this.listeners.set(type, fn)
  }
  close() {}
  removeEventListener(type: string) {
    this.listeners.delete(type)
  }
}
;(globalThis as any).EventSource = MockEventSource

describe('APIClient', () => {
  beforeEach(() => {
    mockFetch.mockReset()
    // Reset auth store
    useAuthStore.setState({
      token: null,
      user: null,
    })
    // Mock window.location
    delete (window as any).location
    ;(window as any).location = { href: '' }
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // ──────────── auth header ────────────

  describe('Authorization header', () => {
    it('adds authentication and cursor to an SSE URL with existing query parameters', () => {
      useAuthStore.setState({ token: 'jwt-123' })
      const source = api.sse('/goal/workflow/events?goal_id=g1', '7')
      const url = new URL(source.url, 'http://localhost')
      expect(url.searchParams.get('goal_id')).toBe('g1')
      expect(url.searchParams.get('token')).toBe('jwt-123')
      expect(url.searchParams.get('Last-Event-ID')).toBe('7')
    })

    it('attaches Bearer token when present', async () => {
      useAuthStore.setState({
        token: 'jwt-123',
        user: { id: 'u1', username: 'u', display_name: 'U' },
      })

      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      })

      await api.get('/test')

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/test',
        expect.objectContaining({
          headers: expect.objectContaining({
            'Authorization': 'Bearer jwt-123',
            'Content-Type': 'application/json',
          }),
        })
      )
    })

    it('omits Authorization header when no token', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      })

      await api.get('/test')

      const headers = mockFetch.mock.calls[0][1].headers
      expect(headers['Authorization']).toBeUndefined()
      expect(headers['Content-Type']).toBe('application/json')
    })
  })

  // ──────────── HTTP methods ────────────

  describe('HTTP methods', () => {
    beforeEach(() => {
      mockFetch.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ method: 'ok' }),
      })
    })

    it('GET request', async () => {
      await api.get<{ method: string }>('/x')
      expect(mockFetch).toHaveBeenCalledWith('/api/x', expect.any(Object))
      const opts = mockFetch.mock.calls[0][1]
      expect(opts.method).toBeUndefined() // GET has no method
    })

    it('POST request with body', async () => {
      await api.post<{ method: string }>('/y', { foo: 'bar' })
      const opts = mockFetch.mock.calls[0][1]
      expect(opts.method).toBe('POST')
      expect(opts.body).toBe('{"foo":"bar"}')
    })

    it('POST without body sends undefined', async () => {
      await api.post<{ method: string }>('/y')
      const opts = mockFetch.mock.calls[0][1]
      expect(opts.body).toBeUndefined()
    })

    it('PUT request with body', async () => {
      await api.put<{ method: string }>('/z', { a: 1 })
      const opts = mockFetch.mock.calls[0][1]
      expect(opts.method).toBe('PUT')
      expect(opts.body).toBe('{"a":1}')
    })

    it('DELETE request', async () => {
      await api.delete<{ method: string }>('/q')
      const opts = mockFetch.mock.calls[0][1]
      expect(opts.method).toBe('DELETE')
      expect(opts.body).toBeUndefined()
    })
  })

  // ──────────── error handling ────────────

  describe('error handling', () => {
    it('keeps the login page open and exposes invalid credentials', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
        statusText: 'Unauthorized',
        json: async () => ({ detail: 'Invalid credentials' }),
      })

      await expect(api.post('/auth/login', { username: 'admin', password: 'bad' }))
        .rejects.toThrow('Invalid credentials')
      expect(window.location.href).toBe('')
    })

    it('401 triggers logout + redirect + throws', async () => {
      const logoutSpy = vi.fn()
      useAuthStore.setState({
        token: 'jwt',
        user: { id: 'u1', username: 'u', display_name: 'U' },
        logout: logoutSpy,
      } as any)

      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
        statusText: 'Unauthorized',
        json: async () => ({ detail: 'Token expired' }),
      })

      await expect(api.get('/secret')).rejects.toThrow('Unauthorized')

      expect(logoutSpy).toHaveBeenCalled()
      expect(window.location.href).toBe('/login')
    })

    it('non-401 error throws with detail', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 400,
        statusText: 'Bad Request',
        json: async () => ({ detail: 'Invalid input' }),
      })

      await expect(api.post('/x', {})).rejects.toThrow('Invalid input')
    })

    it('error with no JSON falls back to statusText', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: 'Server Error',
        json: async () => {
          throw new Error('parse fail')
        },
      })

      await expect(api.get('/x')).rejects.toThrow('Server Error')
    })

    it('error with empty detail falls back to default', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 400,
        statusText: 'Bad Request',
        json: async () => ({ detail: '' }),
      })

      await expect(api.get('/x')).rejects.toThrow('Request failed')
    })

    it('2xx with empty body does not throw (future 204 endpoints)', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 204,
        text: async () => '',
      })

      const result = await api.get<null>('/x')
      expect(result).toBeUndefined()
    })

    it('ApiError preserves non-string detail (e.g. FastAPI 422 array)', async () => {
      const detail = [{ loc: ['body', 'x'], msg: 'field required' }]
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 422,
        statusText: 'Unprocessable Entity',
        json: async () => ({ detail }),
      })

      try {
        await api.post('/x', {})
        expect.unreachable('should have thrown')
      } catch (err: any) {
        expect(err.status).toBe(422)
        expect(err.detail).toEqual(detail)
        expect(err.message).toBe('Request failed')
      }
    })
  })

  // ──────────── SSE connection ────────────

  describe('sse()', () => {
    it('passes token as query param when set', () => {
      useAuthStore.setState({
        token: 'jwt-abc',
        user: { id: 'u1', username: 'u', display_name: 'U' },
      } as any)

      const es = api.sse('/chat/events') as unknown as MockEventSource

      expect(es.url).toBe('/api/chat/events?token=jwt-abc')
    })

    it('passes Last-Event-ID when provided', () => {
      const es = api.sse('/chat/events', 'evt-42') as unknown as MockEventSource

      expect(es.url).toContain('Last-Event-ID=evt-42')
    })

    it('combines token + Last-Event-ID', () => {
      useAuthStore.setState({
        token: 'jwt',
        user: { id: 'u1', username: 'u', display_name: 'U' },
      } as any)

      const es = api.sse('/chat/events', 'evt-1') as unknown as MockEventSource

      expect(es.url).toContain('token=jwt')
      expect(es.url).toContain('Last-Event-ID=evt-1')
    })

    it('no params when no token and no lastEventId', () => {
      const es = api.sse('/chat/events') as unknown as MockEventSource

      expect(es.url).toBe('/api/chat/events')
    })
  })
})