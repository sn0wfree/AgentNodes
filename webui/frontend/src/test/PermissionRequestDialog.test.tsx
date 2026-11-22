import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { PermissionRequestDialog } from '../components/chat/PermissionRequestDialog'
import type { PermissionRequest } from '../hooks/sse/permissionHandlers'

const baseRequest: PermissionRequest = {
  tool_call_id: 'tc-1',
  tool_name: 'write_file',
  args: { path: '/tmp/x.py', content: 'print("hi")' },
  pattern: '*.py',
  target: '/tmp/x.py',
}

describe('PermissionRequestDialog', () => {
  let onRespond: ReturnType<typeof vi.fn>

  beforeEach(() => {
    onRespond = vi.fn()
  })

  it('renders nothing when request is null', () => {
    const { container } = render(
      <PermissionRequestDialog request={null} onRespond={onRespond} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders tool name and pattern header', () => {
    render(
      <PermissionRequestDialog request={baseRequest} onRespond={onRespond} />,
    )
    expect(screen.getByText(/允许写入文件吗？/)).toBeTruthy()
    expect(screen.getByText('write_file')).toBeTruthy()
    expect(screen.getByText('*.py')).toBeTruthy()
  })

  it('falls back to raw tool name when no label mapping exists', () => {
    render(
      <PermissionRequestDialog
        request={{ ...baseRequest, tool_name: 'exotic_tool' }}
        onRespond={onRespond}
      />,
    )
    expect(screen.getByText(/允许exotic_tool吗？/)).toBeTruthy()
  })

  it('shows every kwarg except internal __ markers', () => {
    render(
      <PermissionRequestDialog
        request={{
          ...baseRequest,
          args: {
            path: '/tmp/x.py',
            __session_id__: 'should-not-render',
            visible: 'yes',
          },
        }}
        onRespond={onRespond}
      />,
    )
    expect(screen.getByText('/tmp/x.py')).toBeTruthy()
    expect(screen.getByText('yes')).toBeTruthy()
    expect(screen.queryByText('should-not-render')).toBeNull()
    expect(screen.queryByText('__session_id__')).toBeNull()
  })

  it('renders "(无可显示参数)" when args is empty', () => {
    render(
      <PermissionRequestDialog
        request={{ ...baseRequest, args: {} }}
        onRespond={onRespond}
      />,
    )
    expect(screen.getByText(/无可显示参数/)).toBeTruthy()
  })

  it('formats scalar values directly', () => {
    render(
      <PermissionRequestDialog
        request={{ ...baseRequest, args: { n: 42, b: true, s: 'hello' } }}
        onRespond={onRespond}
      />,
    )
    expect(screen.getByText('42')).toBeTruthy()
    expect(screen.getByText('true')).toBeTruthy()
    expect(screen.getByText('hello')).toBeTruthy()
  })

  it('formats complex values via JSON.stringify', () => {
    render(
      <PermissionRequestDialog
        request={{
          ...baseRequest,
          args: { list: [1, 2, 3], obj: { a: 1 } },
        }}
        onRespond={onRespond}
      />,
    )
    expect(screen.getByText('[1,2,3]')).toBeTruthy()
    expect(screen.getByText('{"a":1}')).toBeTruthy()
  })

  it('formats null and undefined as em-dash', () => {
    render(
      <PermissionRequestDialog
        request={{ ...baseRequest, args: { a: null, b: undefined } }}
        onRespond={onRespond}
      />,
    )
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBe(2)
  })

  it('renders all 4 action buttons (opencode 4-way gate)', () => {
    render(
      <PermissionRequestDialog request={baseRequest} onRespond={onRespond} />,
    )
    expect(screen.getByTestId('permission-allow-once')).toBeTruthy()
    expect(screen.getByTestId('permission-allow-always')).toBeTruthy()
    expect(screen.getByTestId('permission-deny-once')).toBeTruthy()
    expect(screen.getByTestId('permission-deny-always')).toBeTruthy()
  })

  it('clicking "允许本次" calls onRespond(allow, false)', () => {
    render(
      <PermissionRequestDialog request={baseRequest} onRespond={onRespond} />,
    )
    fireEvent.click(screen.getByTestId('permission-allow-once'))
    expect(onRespond).toHaveBeenCalledWith('allow', false)
  })

  it('clicking "始终允许" calls onRespond(allow, true)', () => {
    render(
      <PermissionRequestDialog request={baseRequest} onRespond={onRespond} />,
    )
    fireEvent.click(screen.getByTestId('permission-allow-always'))
    expect(onRespond).toHaveBeenCalledWith('allow', true)
  })

  it('clicking "拒绝本次" calls onRespond(deny, false)', () => {
    render(
      <PermissionRequestDialog request={baseRequest} onRespond={onRespond} />,
    )
    fireEvent.click(screen.getByTestId('permission-deny-once'))
    expect(onRespond).toHaveBeenCalledWith('deny', false)
  })

  it('clicking "始终拒绝" calls onRespond(deny, true)', () => {
    render(
      <PermissionRequestDialog request={baseRequest} onRespond={onRespond} />,
    )
    fireEvent.click(screen.getByTestId('permission-deny-always'))
    expect(onRespond).toHaveBeenCalledWith('deny', true)
  })

  it('close X icon triggers a one-shot deny', () => {
    render(
      <PermissionRequestDialog request={baseRequest} onRespond={onRespond} />,
    )
    const closeBtn = screen.getByLabelText('关闭')
    fireEvent.click(closeBtn)
    expect(onRespond).toHaveBeenCalledWith('deny', false)
  })

  it('reason input accepts text', () => {
    render(
      <PermissionRequestDialog request={baseRequest} onRespond={onRespond} />,
    )
    const input = screen.getByTestId('permission-reason-input') as HTMLInputElement
    fireEvent.change(input, { target: { value: '会覆盖现有策略' } })
    expect(input.value).toBe('会覆盖现有策略')
  })

  it('includes the reason when rejecting a request', () => {
    render(
      <PermissionRequestDialog request={baseRequest} onRespond={onRespond} />,
    )
    fireEvent.change(screen.getByTestId('permission-reason-input'), {
      target: { value: '会覆盖现有策略' },
    })
    fireEvent.click(screen.getByTestId('permission-deny-once'))
    expect(onRespond).toHaveBeenCalledWith('deny', false, '会覆盖现有策略')
  })

  it('dialog has correct ARIA attributes for a11y', () => {
    render(
      <PermissionRequestDialog request={baseRequest} onRespond={onRespond} />,
    )
    const dialog = screen.getByRole('dialog')
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(dialog.getAttribute('aria-labelledby')).toBe('permission-title')
  })
})