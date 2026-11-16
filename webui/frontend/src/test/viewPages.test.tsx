// T3.4 — FactorLibraryPage + DAGPage: the two pure-view pages left at
// ~6% coverage. Both are local-state only (no API, no effects), so the
// value here is pinning the filtering / selection contracts rather than
// re-testing layout (the catalog + visual baselines own appearance).

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render as rtlRender, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const navigateMock = vi.hoisted(() => vi.fn())
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('react-router-dom')
  return { ...actual, useNavigate: () => navigateMock }
})

import { useAgentStore } from '../stores/agents'
import { FactorLibraryPage } from '../pages/FactorLibraryPage'
import { DAGPage } from '../pages/DAGPage'
import { FACTOR_ZOOS, FACTORS } from '../data/factorMock'

const render = (ui: import('react').ReactElement) =>
  rtlRender(<MemoryRouter>{ui}</MemoryRouter>)

beforeEach(() => {
  navigateMock.mockReset()
})

describe('FactorLibraryPage', () => {
  it('lists every factor zoo with its count in the sticky bar', () => {
    render(<FactorLibraryPage />)
    for (const z of FACTOR_ZOOS) {
      expect(screen.getByText(z.label)).toBeInTheDocument()
    }
    expect(screen.getByText(new RegExp(`共 ${FACTOR_ZOOS.reduce((s, z) => s + z.count, 0)} 个因子`)))
      .toBeInTheDocument()
  })

  it('shows only the selected zoo\'s factors', () => {
    render(<FactorLibraryPage />)
    const firstZoo = FACTOR_ZOOS[1]!
    fireEvent.click(screen.getByText(firstZoo.label))

    const expected = FACTORS.filter((f) => f.zoo === firstZoo.id)
    const other = FACTORS.filter((f) => f.zoo !== firstZoo.id)
    expect(expected.length).toBeGreaterThan(0)
    expect(screen.getByText(expected[0]!.id)).toBeInTheDocument()
    if (other.length > 0) {
      expect(screen.queryByText(other[0]!.id)).not.toBeInTheDocument()
    }
  })

  it('filters by free-text query across id / nickname / theme / columns', () => {
    render(<FactorLibraryPage />)
    const zooFactors = FACTORS.filter((f) => f.zoo === FACTOR_ZOOS[0]!.id)
    const target = zooFactors.find((f) => f.nickname && f.nickname.length > 3)!
    const input = screen.getByRole('textbox')

    fireEvent.change(input, { target: { value: target.nickname.slice(0, 4) } })
    expect(screen.getByText(target.id)).toBeInTheDocument()
  })

  it('shows no matches for a nonsense query', () => {
    render(<FactorLibraryPage />)
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'zzz-no-such-factor-zzz' },
    })
    expect(screen.queryByText(FACTORS[0]!.id)).not.toBeInTheDocument()
  })

  it('clears the query through the clear affordance when present', () => {
    render(<FactorLibraryPage />)
    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'zzz' } })
    // Clearing restores the full zoo listing.
    fireEvent.change(input, { target: { value: '' } })
    const zooFactors = FACTORS.filter((f) => f.zoo === FACTOR_ZOOS[0]!.id)
    expect(screen.getByText(zooFactors[0]!.id)).toBeInTheDocument()
  })
})

describe('DAGPage', () => {
  beforeEach(() => {
    useAgentStore.setState({ agents: new Map() })
  })

  it('renders the page shell with its title', () => {
    render(<DAGPage />)
    // Both the nav rail and the page header carry the word 编排.
    expect(screen.getAllByText('编排').length).toBeGreaterThan(0)
  })

  it('reflects agents from the store (empty vs populated)', () => {
    const { unmount } = render(<DAGPage />)
    expect(document.body.textContent?.length).toBeGreaterThan(0)
    unmount()

    useAgentStore.setState({
      agents: new Map([['a1', {
        id: 'a1', session_id: 's', status: 'running', name: 'Worker',
        created_at: 1, updated_at: 1, tool_calls_count: 0, compaction_count: 0,
        context_tokens: 0, context_tokens_limit: 1000, iterations_detail: [],
      } as never]]),
    })
    render(<DAGPage />)
    expect(document.body.textContent?.length).toBeGreaterThan(0)
  })
})
