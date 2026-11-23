// AccountPage：设置并入用户管理页后的分区可见性（R-014）
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AccountPage } from '../pages/AccountPage'

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => vi.fn() }
})
vi.mock('../api/client', () => ({
  api: {
    get: vi.fn().mockImplementation((url: string) =>
      url === '/system/info'
        ? Promise.resolve({ workspace_path: '/ws', user_count: 2 })
        : Promise.resolve({ provider: '', model: '', providers: [] })),
    adminUsers: { list: vi.fn().mockResolvedValue({ users: [], total: 0 }) },
  },
}))
vi.mock('../components/layout/PageShell', () => ({
  PageShell: ({ title, children }: any) => <div><h1>{title}</h1>{children}</div>,
}))
vi.mock('../components/settings/LLMSection', () => ({ LLMSection: () => <div>LLM 配置</div> }))
vi.mock('../components/settings/AccountSection', () => ({ AccountSection: () => <div>个人账号</div> }))
vi.mock('../components/settings/WorkspaceSection', () => ({ WorkspaceSection: () => <div>工作区</div> }))
vi.mock('../components/settings/ChatLayoutSection', () => ({ ChatLayoutSection: () => <div>聊天布局</div> }))
vi.mock('../components/settings/AppearanceSection', () => ({ AppearanceSection: () => <div>外观</div> }))
vi.mock('../pages/AdminUsersPage', () => ({ AdminUsersPage: () => <div data-testid="admin-users">用户管理表格</div> }))

import { useAuthStore } from '../stores/auth'

function renderPage(role: 'admin' | 'user') {
  useAuthStore.setState({ user: { id: 'u1', username: 'tester', role } } as never)
  return render(<MemoryRouter><AccountPage /></MemoryRouter>)
}

describe('AccountPage', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows all settings sections to every user', async () => {
    renderPage('user')
    expect(await screen.findByText('账户与设置')).toBeTruthy()
    for (const label of ['LLM 配置', '个人账号', '工作区', '聊天布局', '外观']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    }
  })

  it('hides the user-management section from non-admin users', async () => {
    renderPage('user')
    await waitFor(() => expect(screen.queryByTestId('admin-users')).toBeNull())
  })

  it('shows the user-management section to admins', async () => {
    renderPage('admin')
    expect(await screen.findByTestId('admin-users')).toBeTruthy()
  })
})
