// LoginPage + RegisterPage unit tests — form submit, api calls,
// auth store update, navigation, error surfaces, and the a11y
// label→input association added during the route-gap fix.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { useAuthStore } from '../stores/auth'

vi.mock('../api/client', async () => {
  return { api: { post: vi.fn() }, ApiError: class extends Error {} }
})

import { api } from '../api/client'
import { LoginPage } from '../components/auth/LoginPage'
import { RegisterPage } from '../components/auth/RegisterPage'

const mockPost = vi.mocked(api.post)

const okAuth = {
  access_token: 'tok-123',
  user: { id: 'u1', username: 'tester', display_name: 'Tester' },
}

function Home() {
  return <div>首页已跳转</div>
}

function loginUi() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/chat" element={<div>会话工作台已打开</div>} />
        <Route path="/" element={<Home />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({ token: null, user: null })
  })

  it('submits credentials and stores auth on success', async () => {
    mockPost.mockResolvedValue(okAuth as never)
    loginUi()
    fireEvent.change(screen.getByLabelText('用户名'), {
      target: { value: 'tester' },
    })
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret' },
    })
    fireEvent.click(screen.getByRole('button', { name: '登录' }))

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/auth/login', {
        username: 'tester',
        password: 'secret',
      })
    )
    expect(useAuthStore.getState().token).toBe('tok-123')
    expect(useAuthStore.getState().user?.username).toBe('tester')
    expect(await screen.findByText('会话工作台已打开')).toBeInTheDocument()
  })

  it('surfaces the API error message on failure', async () => {
    const err = new Error('用户名或密码错误')
    mockPost.mockRejectedValue(err as never)
    loginUi()
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'x' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'y' } })
    fireEvent.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByText('用户名或密码错误')).toBeInTheDocument()
    expect(useAuthStore.getState().token).toBeNull()
  })

  it('shows the register link pointing at /register', () => {
    loginUi()
    const link = screen.getByRole('link', { name: '注册' })
    expect(link).toHaveAttribute('href', '/register')
  })

  it('associates labels with inputs via htmlFor', () => {
    loginUi()
    expect(screen.getByLabelText('用户名')).toHaveAttribute('id', 'login-username')
    expect(screen.getByLabelText('密码')).toHaveAttribute('id', 'login-password')
  })
})

describe('RegisterPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({ token: null, user: null })
  })

  it('registers and navigates home with fallback display name', async () => {
    mockPost.mockResolvedValue(okAuth as never)
    loginUi()
    fireEvent.click(screen.getByRole('link', { name: '注册' }))
    fireEvent.change(await screen.findByLabelText('用户名'), {
      target: { value: 'newbie' },
    })
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'pw' },
    })
    fireEvent.click(screen.getByRole('button', { name: '注册' }))

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/auth/register', {
        username: 'newbie',
        display_name: 'newbie',
        password: 'pw',
      })
    )
    expect(await screen.findByText('首页已跳转')).toBeInTheDocument()
  })

  it('uses the explicit display name when provided', async () => {
    mockPost.mockResolvedValue(okAuth as never)
    loginUi()
    fireEvent.click(screen.getByRole('link', { name: '注册' }))
    fireEvent.change(await screen.findByLabelText('用户名'), {
      target: { value: 'newbie' },
    })
    fireEvent.change(screen.getByLabelText('显示名称'), {
      target: { value: '研究员甲' },
    })
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'pw' },
    })
    fireEvent.click(screen.getByRole('button', { name: '注册' }))

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/auth/register', {
        username: 'newbie',
        display_name: '研究员甲',
        password: 'pw',
      })
    )
  })

  it('shows fallback error text when the error has no message', async () => {
    mockPost.mockRejectedValue(new Error() as never)
    loginUi()
    fireEvent.click(screen.getByRole('link', { name: '注册' }))
    fireEvent.change(await screen.findByLabelText('用户名'), {
      target: { value: 'a' },
    })
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'b' },
    })
    fireEvent.click(screen.getByRole('button', { name: '注册' }))

    expect(await screen.findByText('注册失败')).toBeInTheDocument()
  })
})
