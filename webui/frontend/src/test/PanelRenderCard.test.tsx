// PanelRenderCard — agent-driven performance card (show_chart /
// show_report renderables) + extractLatestPanelItem util.

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PanelRenderCard } from '../components/layout/PanelRenderCard'
import { extractLatestPanelItem } from '../utils/equityCurve'
import type { Message } from '../stores/chat'

function msg(
  id: string,
  parts: Message['parts'],
  createdAt: number,
  sessionId = 'sess-1',
): Message {
  return { id, session_id: sessionId, role: 'assistant', parts, created_at: createdAt }
}

const chartPart = {
  type: 'chart' as const,
  id: 'chart-1',
  chart_type: 'line' as const,
  title: '净值曲线',
  data: [
    { date: '2024-01-01', nav: 1.0 },
    { date: '2024-01-02', nav: 1.05 },
  ],
}

const htmlPart = {
  type: 'html' as const,
  id: 'report-1',
  title: '回测报告',
  content: '<html><body>报告内容</body></html>',
}

describe('extractLatestPanelItem', () => {
  it('picks the most recent chart part', () => {
    const messages = [
      msg('m1', [{ type: 'text', id: 't', text: '旧' }], 100),
      msg('m2', [chartPart], 200),
      msg('m3', [{ type: 'text', id: 't2', text: '新' }], 300),
    ]
    const item = extractLatestPanelItem(messages)
    expect(item?.kind).toBe('chart')
    expect(item?.message_id).toBe('m2')
    expect(item?.chart?.title).toBe('净值曲线')
  })

  it('html beats older charts (most recent renderable)', () => {
    const messages = [
      msg('m1', [chartPart], 100),
      msg('m2', [htmlPart], 200),
    ]
    const item = extractLatestPanelItem(messages)
    expect(item?.kind).toBe('html')
    expect(item?.message_id).toBe('m2')
    expect(item?.html?.content).toContain('报告内容')
  })

  it('returns null when nothing renderable', () => {
    expect(extractLatestPanelItem([msg('m1', [{ type: 'text', id: 't', text: 'x' }], 1)])).toBeNull()
  })

  it('filters by session in the consumer (multi-session map)', () => {
    const other = msg('m-other', [htmlPart], 300, 'sess-other')
    const mine = msg('m-mine', [chartPart], 200)
    const item = extractLatestPanelItem([mine, other])
    // util itself does not filter; the caller filters. Take both, expect
    // the latest overall.
    expect(item?.kind).toBe('html')
  })
})

describe('PanelRenderCard', () => {
  it('renders a recharts chart for kind=chart', () => {
    render(
      <PanelRenderCard
        item={{
          kind: 'chart',
          chart: { title: '净值曲线', chart_type: 'line', data: chartPart.data },
          message_id: 'm1',
          timestamp: 1,
        }}
        metrics={null}
      />,
    )
    expect(screen.getByText('净值曲线')).toBeTruthy()
  })

  it('renders a sandboxed iframe for kind=html', () => {
    render(
      <PanelRenderCard
        item={{
          kind: 'html',
          html: { title: '回测报告', content: '<html>x</html>' },
          message_id: 'm1',
          timestamp: 1,
        }}
        metrics={null}
      />,
    )
    const iframe = document.querySelector('iframe')
    expect(iframe).not.toBeNull()
    expect(iframe?.getAttribute('sandbox')).not.toBeNull()
    expect(iframe?.getAttribute('srcdoc')).toBe('<html>x</html>')
  })

  it('falls back to metrics row when no renderable', () => {
    render(
      <PanelRenderCard
        item={null}
        metrics={{ total_return: 0.12, sharpe: 1.5, max_drawdown: -0.08, timestamp: 1 }}
      />,
    )
    expect(screen.getByText('+12.00%')).toBeTruthy()
  })

  it('renders nothing when item/metrics/curve are all empty (P6 空态诚实)', () => {
    const { container } = render(
      <PanelRenderCard item={null} metrics={null} curve={null} />,
    )
    expect(container.textContent).toBe('')
  })
})
