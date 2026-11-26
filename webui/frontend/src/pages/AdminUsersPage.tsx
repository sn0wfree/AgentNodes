import { useCallback, useEffect, useState } from 'react'
import { ShieldCheck, UserPlus, RefreshCw, KeyRound, Ban, CircleCheck } from 'lucide-react'
import { PageShell } from '../components/layout/PageShell'
import { api } from '../api/client'
import type { AdminUser } from '../api/client'
import { useAuthStore } from '../stores/auth'

export function AdminUsersPage({ embedded = false }: { embedded?: boolean }) {
  const myId = useAuthStore((s) => s.user?.id)
  const [users, setUsers] = useState<AdminUser[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // Create form
  const [showCreate, setShowCreate] = useState(false)
  const [nU, setNU] = useState('')
  const [nP, setNP] = useState('')
  const [nD, setND] = useState('')
  const [nR, setNR] = useState('user')

  // Row actions
  const [resetFor, setResetFor] = useState<AdminUser | null>(null)
  const [newPw, setNewPw] = useState('')
  const [dataFor, setDataFor] = useState<AdminUser | null>(null)
  const [dataCounts, setDataCounts] = useState<{ sessions?: number | null; studies?: number | null }>({})

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await api.adminUsers.list({ limit: 200 })
      setUsers(res.users)
      setTotal(res.total)
    } catch (e: any) {
      setError(e.message || '加载用户失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const flash = (msg: string) => {
    setNotice(msg)
    setTimeout(() => setNotice(''), 2500)
  }

  const handleCreate = async () => {
    if (!nU || !nP) return
    try {
      await api.adminUsers.create({
        username: nU,
        password: nP,
        display_name: nD || undefined,
        role: nR,
      })
      setShowCreate(false)
      setNU('')
      setNP('')
      setND('')
      flash('用户已创建')
      load()
    } catch (e: any) {
      setError(e.message || '创建失败')
    }
  }

  const toggleRole = async (u: AdminUser) => {
    const next = u.role === 'admin' ? 'user' : 'admin'
    try {
      await api.adminUsers.update(u.id, { role: next })
      flash(`${u.username} 角色 → ${next}`)
      load()
    } catch (e: any) {
      setError(e.message || '操作失败')
    }
  }

  const toggleActive = async (u: AdminUser) => {
    try {
      if (u.is_active) {
        await api.adminUsers.disable(u.id)
        flash(`${u.username} 已禁用`)
      } else {
        await api.adminUsers.enable(u.id)
        flash(`${u.username} 已启用`)
      }
      load()
    } catch (e: any) {
      setError(e.message || '操作失败')
    }
  }

  const handleReset = async () => {
    if (!resetFor || !newPw) return
    try {
      await api.adminUsers.resetPassword(resetFor.id, newPw)
      flash(`${resetFor.username} 密码已重置`)
      setResetFor(null)
      setNewPw('')
    } catch (e: any) {
      setError(e.message || '重置失败')
    }
  }

  const viewData = async (u: AdminUser) => {
    try {
      const d = await api.adminUsers.data(u.id)
      // Defensive: a proxy/gateway may return an envelope or null. Never
      // assign undefined into state that the render path dereferences.
      setDataCounts({
        sessions: d?.sessions ?? null,
        studies: d?.studies ?? null,
      })
      setDataFor(u)
    } catch (e: any) {
      setError(e.message || '查询失败')
    }
  }

  const body = (
    <div className="space-y-4">
        {notice && (
          <div className="rounded-lg border border-emerald-800 bg-emerald-900/30 px-3 py-2 text-sm text-emerald-300">
            {notice}
          </div>
        )}
        {error && (
          <div className="rounded-lg border border-red-800 bg-red-900/30 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}

        {showCreate && (
          <div className="glass-elevated space-y-3 rounded-xl p-4">
            <h3 className="text-sm font-semibold text-foreground">新建用户</h3>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
              <input value={nU} onChange={(e) => setNU(e.target.value)} placeholder="用户名"
                className="rounded-lg border border-strong bg-surface-raised px-3 py-2 text-sm text-foreground outline-none focus:border-primary-500" />
              <input value={nP} onChange={(e) => setNP(e.target.value)} type="password" placeholder="密码"
                className="rounded-lg border border-strong bg-surface-raised px-3 py-2 text-sm text-foreground outline-none focus:border-primary-500" />
              <input value={nD} onChange={(e) => setND(e.target.value)} placeholder="显示名（可选）"
                className="rounded-lg border border-strong bg-surface-raised px-3 py-2 text-sm text-foreground outline-none focus:border-primary-500" />
              <select value={nR} onChange={(e) => setNR(e.target.value)}
                className="rounded-lg border border-strong bg-surface-raised px-3 py-2 text-sm text-foreground outline-none focus:border-primary-500">
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
              <button onClick={handleCreate}
                className="rounded-lg bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700">
                创建
              </button>
            </div>
          </div>
        )}

        <div className="glass-elevated overflow-hidden rounded-xl">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-subtle bg-surface-raised text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-4 py-2.5">用户</th>
                <th className="px-4 py-2.5">角色</th>
                <th className="px-4 py-2.5">状态</th>
                <th className="px-4 py-2.5 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={4} className="px-4 py-8 text-center text-muted">加载中…</td></tr>
              )}
              {!loading && users.length === 0 && (
                <tr><td colSpan={4} className="px-4 py-8 text-center text-muted">暂无用户</td></tr>
              )}
              {users.map((u) => {
                const isSelf = u.id === myId
                return (
                  <tr key={u.id} className="border-b border-subtle/60 last:border-0 hover:bg-surface-raised/30">
                    <td className="px-4 py-2.5">
                      <div className="font-medium text-foreground">{u.display_name || u.username}</div>
                      <div className="font-mono text-xs text-muted">@{u.username}{isSelf && '（当前账号）'}</div>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`rounded px-2 py-0.5 text-xs font-medium ${u.role === 'admin' ? 'bg-primary-600/20 text-primary-300' : 'bg-surface text-muted'}`}>
                        {u.role}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`text-xs ${u.is_active ? 'text-success-fg' : 'text-danger-fg'}`}>
                        {u.is_active ? '启用' : '禁用'}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center justify-end gap-2">
                        <button onClick={() => viewData(u)} title="查看数据"
                          className="rounded-md px-2 py-1 text-xs text-muted hover:bg-surface-raised hover:text-foreground">
                          数据
                        </button>
                        {!isSelf && (
                          <button onClick={() => toggleRole(u)} disabled={isSelf}
                            title={u.role === 'admin' ? '降级为普通用户' : '提升为管理员'}
                            className="rounded-md px-2 py-1 text-xs text-muted hover:bg-surface-raised hover:text-foreground">
                            {u.role === 'admin' ? '降级' : '提升'}
                          </button>
                        )}
                        <button onClick={() => { setResetFor(u); setNewPw('') }} disabled={isSelf}
                          title="重置密码"
                          className="rounded-md px-2 py-1 text-xs text-muted hover:bg-surface-raised hover:text-foreground">
                          <KeyRound className="h-3.5 w-3.5" />
                        </button>
                        {!isSelf && (
                          <button onClick={() => toggleActive(u)}
                            title={u.is_active ? '禁用' : '启用'}
                            className={`rounded-md px-2 py-1 text-xs hover:bg-surface-raised ${u.is_active ? 'text-muted hover:text-danger-fg' : 'text-success-fg'}`}>
                            {u.is_active ? <Ban className="h-3.5 w-3.5" /> : <CircleCheck className="h-3.5 w-3.5" />}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {resetFor && (
          <div className="glass-elevated rounded-xl p-4">
            <h3 className="mb-3 text-sm font-semibold text-foreground">
              重置 {resetFor.username} 的密码
            </h3>
            <div className="flex gap-3">
              <input value={newPw} onChange={(e) => setNewPw(e.target.value)} type="password" placeholder="新密码"
                className="flex-1 rounded-lg border border-strong bg-surface-raised px-3 py-2 text-sm text-foreground outline-none focus:border-primary-500" />
              <button onClick={handleReset}
                className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700">
                确认
              </button>
              <button onClick={() => setResetFor(null)}
                className="rounded-lg border border-strong px-4 py-2 text-sm text-muted hover:bg-surface-raised">
                取消
              </button>
            </div>
          </div>
        )}

        {dataFor && (
          <div className="glass-elevated rounded-xl p-4">
            <h3 className="mb-3 text-sm font-semibold text-foreground">
              {dataFor.username} 数据概览
            </h3>
            <div className="flex gap-6 text-sm">
              <div>
                <div className="text-xs text-muted">会话 (sessions)</div>
                <div className="text-lg font-semibold text-foreground">{dataCounts.sessions ?? '—'}</div>
              </div>
              <div>
                <div className="text-xs text-muted">研究 (studies)</div>
                <div className="text-lg font-semibold text-foreground">{dataCounts.studies ?? '—'}</div>
              </div>
              <button onClick={() => setDataFor(null)}
                className="ml-auto self-end rounded-lg border border-strong px-3 py-1.5 text-xs text-muted hover:bg-surface-raised">
                关闭
              </button>
            </div>
          </div>
        )}
    </div>
  )

  if (embedded) return body
  return (
    <PageShell
      title="用户管理"
      subtitle={`共 ${total} 个账号`}
      icon={<ShieldCheck className="h-4 w-4" />}
      actions={
        <>
          <button onClick={load} title="刷新"
            className="flex items-center gap-1.5 rounded-lg border border-strong bg-surface px-2.5 py-1.5 text-xs text-muted transition-colors hover:border-strong hover:text-secondary">
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <button onClick={() => setShowCreate((v) => !v)}
            className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-primary-700">
            <UserPlus className="h-3.5 w-3.5" />
            新建用户
          </button>
        </>
      }
    >
      {body}
    </PageShell>
  )
}