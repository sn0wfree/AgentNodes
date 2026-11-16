// T3.1 — AdminUsersPage integration: the whole admin surface (load →
// list render → create / role toggle / active toggle / password reset /
// data peek) against a mocked api client. Pins the self-account guard
// (a superuser must not be able to demote / disable / reset their own
// account) and the loading / empty / error states.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render as rtlRender, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const USER_ME = {
  id: 'u-me',
  username: 'root',
  display_name: 'Root',
  role: 'admin',
  is_active: true,
}
const USER_OTHER = {
  id: 'u-2',
  username: 'alice',
  display_name: 'Alice',
  role: 'user',
  is_active: true,
}

vi.mock('../api/client', () => ({
  api: {
    adminUsers: {
      list: vi.fn(async () => ({ users: [], total: 0 })),
      create: vi.fn(async () => ({})),
      update: vi.fn(async () => ({})),
      resetPassword: vi.fn(async () => ({ message: 'ok' })),
      disable: vi.fn(async () => ({ message: 'ok' })),
      enable: vi.fn(async () => ({ message: 'ok' })),
      data: vi.fn(async () => ({ user_id: 'u-2', sessions: 7, studies: 3 })),
    },
  },
}))

import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { AdminUsersPage } from '../pages/AdminUsersPage'

const asMe = () =>
  useAuthStore.setState({
    token: 'jwt',
    user: USER_ME as never,
  })

// PageShell mounts IconNav (useNavigate/useLocation), so every render
// needs a Router in scope. Keeping the wrapper local means no test body
// has to know about it.
const render = (ui: import('react').ReactElement) =>
  rtlRender(<MemoryRouter>{ui}</MemoryRouter>)

beforeEach(() => {
  useAuthStore.setState({ token: null, user: null })
  vi.mocked(api.adminUsers.list).mockReset()
  vi.mocked(api.adminUsers.list).mockResolvedValue({ users: [], total: 0 } as never)
  vi.mocked(api.adminUsers.create).mockReset()
  vi.mocked(api.adminUsers.update).mockReset()
  vi.mocked(api.adminUsers.resetPassword).mockReset()
  vi.mocked(api.adminUsers.disable).mockReset()
  vi.mocked(api.adminUsers.enable).mockReset()
  vi.mocked(api.adminUsers.data).mockReset()
  vi.mocked(api.adminUsers.data).mockResolvedValue({
    user_id: 'u-2', sessions: 7, studies: 3,
  } as never)
})

describe('AdminUsersPage', () => {
  it('shows the loading row, then the empty state', async () => {
    asMe()
    render(<AdminUsersPage />)
    expect(screen.getByText('加载中…')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('暂无用户')).toBeInTheDocument())
    expect(screen.getByText('共 0 个账号')).toBeInTheDocument()
  })

  it('renders users with display name, @username, role and status', async () => {
    asMe()
    vi.mocked(api.adminUsers.list).mockResolvedValue({
      users: [USER_ME, USER_OTHER], total: 2,
    } as never)
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument())

    expect(screen.getByText('@alice')).toBeInTheDocument()
    // The signed-in account is labelled.
    expect(screen.getByText(/@root（当前账号）/)).toBeInTheDocument()
    expect(screen.getByText('admin')).toBeInTheDocument()
    expect(screen.getAllByText('启用')).toHaveLength(2)
    expect(screen.getByText('共 2 个账号')).toBeInTheDocument()
  })

  it('hides role/active actions for the current account (self-guard)', async () => {
    asMe()
    vi.mocked(api.adminUsers.list).mockResolvedValue({
      users: [USER_ME], total: 1,
    } as never)
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText('Root')).toBeInTheDocument())

    // Own row keeps only the "数据" action.
    expect(screen.queryByText('降级')).not.toBeInTheDocument()
    expect(screen.queryByText('提升')).not.toBeInTheDocument()
    expect(screen.queryByTitle('禁用')).not.toBeInTheDocument()
    // Reset button exists but is disabled for self.
    expect(screen.getByTitle('重置密码')).toBeDisabled()
    expect(screen.getByText('数据')).toBeEnabled()
  })

  it('shows role/active actions for other accounts', async () => {
    asMe()
    vi.mocked(api.adminUsers.list).mockResolvedValue({
      users: [USER_OTHER], total: 1,
    } as never)
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument())
    expect(screen.getByText('提升')).toBeInTheDocument()
    expect(screen.getByTitle('禁用')).toBeInTheDocument()
    expect(screen.getByTitle('重置密码')).toBeEnabled()
  })

  it('toggles a role from user → admin via the API', async () => {
    asMe()
    vi.mocked(api.adminUsers.list).mockResolvedValue({ users: [USER_OTHER], total: 1 } as never)
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText('提升')).toBeInTheDocument())

    fireEvent.click(screen.getByText('提升'))
    await waitFor(() =>
      expect(api.adminUsers.update).toHaveBeenCalledWith('u-2', { role: 'admin' })
    )
  })

  it('toggles a role from admin → user', async () => {
    asMe()
    const admin = { ...USER_OTHER, role: 'admin' }
    vi.mocked(api.adminUsers.list).mockResolvedValue({ users: [admin], total: 1 } as never)
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText('降级')).toBeInTheDocument())
    fireEvent.click(screen.getByText('降级'))
    await waitFor(() =>
      expect(api.adminUsers.update).toHaveBeenCalledWith('u-2', { role: 'user' })
    )
  })

  it('disables an active account and enables an inactive one', async () => {
    asMe()
    vi.mocked(api.adminUsers.list).mockResolvedValue({ users: [USER_OTHER], total: 1 } as never)
    const { unmount } = render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByTitle('禁用')).toBeInTheDocument())
    fireEvent.click(screen.getByTitle('禁用'))
    await waitFor(() => expect(api.adminUsers.disable).toHaveBeenCalledWith('u-2'))
    unmount()

    const inactive = { ...USER_OTHER, is_active: false }
    vi.mocked(api.adminUsers.list).mockResolvedValue({ users: [inactive], total: 1 } as never)
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByTitle('启用')).toBeInTheDocument())
    fireEvent.click(screen.getByTitle('启用'))
    await waitFor(() => expect(api.adminUsers.enable).toHaveBeenCalledWith('u-2'))
  })

  it('creates a user through the create form, then resets the form', async () => {
    asMe()
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText('暂无用户')).toBeInTheDocument())

    fireEvent.click(screen.getByText('新建用户'))
    fireEvent.change(screen.getByPlaceholderText('用户名'), { target: { value: 'bob' } })
    fireEvent.change(screen.getByPlaceholderText('密码'), { target: { value: 'pw' } })
    fireEvent.change(screen.getByPlaceholderText('显示名（可选）'), { target: { value: 'Bob' } })
    fireEvent.click(screen.getByText('创建'))

    await waitFor(() =>
      expect(api.adminUsers.create).toHaveBeenCalledWith({
        username: 'bob',
        password: 'pw',
        display_name: 'Bob',
        role: 'user',
      })
    )
    // The form closes and clears after a successful create.
    await waitFor(() => expect(screen.getByText('用户已创建')).toBeInTheDocument())
    expect(screen.queryByPlaceholderText('用户名')).not.toBeInTheDocument()
  })

  it('refuses to create without username/password (no API call)', async () => {
    asMe()
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText('暂无用户')).toBeInTheDocument())
    fireEvent.click(screen.getByText('新建用户'))
    fireEvent.click(screen.getByText('创建'))
    expect(api.adminUsers.create).not.toHaveBeenCalled()
  })

  it('resets a password through the reset panel', async () => {
    asMe()
    vi.mocked(api.adminUsers.list).mockResolvedValue({ users: [USER_OTHER], total: 1 } as never)
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByTitle('重置密码')).toBeInTheDocument())

    fireEvent.click(screen.getByTitle('重置密码'))
    expect(screen.getByText('重置 alice 的密码')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('新密码'), { target: { value: 'newpw' } })
    fireEvent.click(screen.getByText('确认'))

    await waitFor(() =>
      expect(api.adminUsers.resetPassword).toHaveBeenCalledWith('u-2', 'newpw')
    )
    await waitFor(() => expect(screen.queryByText('重置 alice 的密码')).not.toBeInTheDocument())
  })

  it('cancels the reset panel without calling the API', async () => {
    asMe()
    vi.mocked(api.adminUsers.list).mockResolvedValue({ users: [USER_OTHER], total: 1 } as never)
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByTitle('重置密码')).toBeInTheDocument())
    fireEvent.click(screen.getByTitle('重置密码'))
    fireEvent.click(screen.getByText('取消'))
    expect(screen.queryByText('重置 alice 的密码')).not.toBeInTheDocument()
    expect(api.adminUsers.resetPassword).not.toHaveBeenCalled()
  })

  it('opens the data panel with session/study counts', async () => {
    asMe()
    vi.mocked(api.adminUsers.list).mockResolvedValue({ users: [USER_OTHER], total: 1 } as never)
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText('数据')).toBeInTheDocument())

    fireEvent.click(screen.getByText('数据'))
    await waitFor(() => expect(screen.getByText('alice 数据概览')).toBeInTheDocument())
    const panel = screen.getByText('alice 数据概览').closest('div')!.parentElement!
    expect(within(panel).getByText('7')).toBeInTheDocument()
    expect(within(panel).getByText('3')).toBeInTheDocument()

    fireEvent.click(within(panel).getByText('关闭'))
    expect(screen.queryByText('alice 数据概览')).not.toBeInTheDocument()
  })

  it('surfaces a load failure in the error banner', async () => {
    asMe()
    vi.mocked(api.adminUsers.list).mockRejectedValueOnce(new Error('backend down'))
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText('backend down')).toBeInTheDocument())
    // The table still renders its empty branch rather than hanging.
    expect(screen.getByText('暂无用户')).toBeInTheDocument()
  })

  it('surfaces an action failure in the error banner', async () => {
    asMe()
    vi.mocked(api.adminUsers.list).mockResolvedValue({ users: [USER_OTHER], total: 1 } as never)
    vi.mocked(api.adminUsers.update).mockRejectedValueOnce(new Error('forbidden'))
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText('提升')).toBeInTheDocument())
    fireEvent.click(screen.getByText('提升'))
    await waitFor(() => expect(screen.getByText('forbidden')).toBeInTheDocument())
  })

  it('refresh button reloads the list', async () => {
    asMe()
    render(<AdminUsersPage />)
    await waitFor(() => expect(screen.getByText('暂无用户')).toBeInTheDocument())
    const callsBefore = vi.mocked(api.adminUsers.list).mock.calls.length
    fireEvent.click(screen.getByTitle('刷新'))
    await waitFor(() =>
      expect(vi.mocked(api.adminUsers.list).mock.calls.length).toBeGreaterThan(callsBefore)
    )
  })
})
