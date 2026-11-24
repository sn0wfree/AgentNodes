import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useAuthStore } from '../../stores/auth'
import { api } from '../../api/client'

export function RegisterPage() {
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
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
      const res = await api.post<{ access_token: string; user: any }>('/auth/register', {
        username,
        display_name: displayName || username,
        password,
      })
      setAuth(res.access_token, res.user)
      navigate('/')
    } catch (err: any) {
      setError(err.message || '注册失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-app">
      <div>
        <div className="grid-layer" />
        <div />
        <div className="vignette-layer" />
        <div className="grain-layer" />
      </div>
      <div className="glass-elevated relative z-10 w-full max-w-sm rounded-2xl p-8">
        <h1 className="mb-6 text-center text-xl font-semibold text-slate-100">
          创建账号
        </h1>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="reg-username" className="mb-1 block text-sm text-slate-400">用户名</label>
            <input
              id="reg-username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 outline-none focus:border-primary-500"
              required
            />
          </div>
          <div>
            <label htmlFor="reg-display" className="mb-1 block text-sm text-slate-400">显示名称</label>
            <input
              id="reg-display"
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 outline-none focus:border-primary-500"
              placeholder="可选"
            />
          </div>
          <div>
            <label htmlFor="reg-password" className="mb-1 block text-sm text-slate-400">密码</label>
            <input
              id="reg-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 outline-none focus:border-primary-500"
              required
            />
          </div>
          {error && (
            <p className="text-sm text-danger-fg">{error}</p>
          )}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-primary-600 py-2.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          >
            {loading ? '注册中...' : '注册'}
          </button>
        </form>
        <p className="mt-4 text-center text-sm text-slate-500">
          已有账号？{' '}
          <Link to="/login" className="text-primary-400 hover:underline">
            登录
          </Link>
        </p>
      </div>
    </div>
  )
}
