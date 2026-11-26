import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useAuthStore, type User } from '../../stores/auth'
import { api } from '../../api/client'

export function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const setAuth = useAuthStore((s) => s.setAuth)
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await api.post<{ access_token: string; user: User }>('/auth/login', {
        username,
        password,
      })
      setAuth(res.access_token, res.user)
      navigate('/chat')
    } catch (err: unknown) {
      setError(err instanceof Error && err.message ? err.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-app px-4 py-8">
      <div className="w-full max-w-sm rounded-2xl border border-subtle bg-surface p-6 shadow-soft sm:p-8">
        <div className="mb-8">
          <div className="mb-5 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-surface-raised text-sm font-bold text-foreground">
            AN
          </div>
          <h1 className="text-xl font-semibold text-foreground">AgentNodes</h1>
          <p className="mt-1 text-sm text-muted">登录以进入会话工作台</p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="login-username" className="mb-1 block text-sm text-muted">用户名</label>
            <input
              id="login-username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg border border-strong bg-slate-900 px-3 py-2 text-sm text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500"
              required
            />
          </div>
          <div>
            <label htmlFor="login-password" className="mb-1 block text-sm text-muted">密码</label>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-strong bg-slate-900 px-3 py-2 text-sm text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500"
              required
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-danger-fg">{error}</p>
          )}
          <button
            type="submit"
            disabled={loading}
            className="ua-primary-action w-full rounded-lg py-2.5 text-sm font-medium disabled:opacity-50"
          >
            {loading ? '登录中...' : '登录'}
          </button>
        </form>
        <p className="mt-4 text-center text-sm text-muted">
          没有账号？{' '}
          <Link to="/register" className="text-sky-500 hover:underline">
            注册
          </Link>
        </p>
      </div>
    </div>
  )
}
