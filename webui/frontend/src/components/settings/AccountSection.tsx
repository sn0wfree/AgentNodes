import { useState } from 'react'
import { User } from 'lucide-react'
import { Section, PasswordInput } from './shared'
import { api } from '../../api/client'

interface AccountSectionProps {
  username: string
  onOpenReset?: () => void
}

export function AccountSection({ username }: AccountSectionProps) {
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [pwLoading, setPwLoading] = useState(false)
  const [pwMsg, setPwMsg] = useState('')
  const [pwError, setPwError] = useState('')
  const [showOldPw, setShowOldPw] = useState(false)
  const [showNewPw, setShowNewPw] = useState(false)

  const handleChangePassword = async () => {
    setPwMsg('')
    setPwError('')
    if (!oldPassword || !newPassword) {
      setPwError('请填写所有字段')
      return
    }
    if (newPassword !== confirmPassword) {
      setPwError('两次密码不一致')
      return
    }
    if (newPassword.length < 4) {
      setPwError('密码至少 4 位')
      return
    }
    setPwLoading(true)
    try {
      await api.post('/auth/change-password', {
        old_password: oldPassword,
        new_password: newPassword,
      })
      setPwMsg('密码修改成功')
      setOldPassword('')
      setNewPassword('')
      setConfirmPassword('')
    } catch (err: any) {
      setPwError(err.message || '修改失败')
    } finally {
      setPwLoading(false)
    }
  }

  return (
    <Section icon={User} title="个人账号">
      <div className="space-y-3">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted">当前用户</span>
          <span className="text-foreground">{username || '—'}</span>
        </div>
        <div className="space-y-2">
          <PasswordInput
            placeholder="当前密码"
            value={oldPassword}
            onChange={setOldPassword}
            show={showOldPw}
            onToggle={() => setShowOldPw(!showOldPw)}
          />
          <PasswordInput
            placeholder="新密码"
            value={newPassword}
            onChange={setNewPassword}
            show={showNewPw}
            onToggle={() => setShowNewPw(!showNewPw)}
          />
          <input
            type="password"
            placeholder="确认新密码"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="w-full rounded-lg border border-strong bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary-500"
          />
          {pwError && <p className="text-xs text-danger-fg">{pwError}</p>}
          {pwMsg && <p className="text-xs text-success-fg">{pwMsg}</p>}
          <button
            onClick={handleChangePassword}
            disabled={pwLoading}
            className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          >
            {pwLoading ? '修改中...' : '修改密码'}
          </button>
        </div>
      </div>
    </Section>
  )
}