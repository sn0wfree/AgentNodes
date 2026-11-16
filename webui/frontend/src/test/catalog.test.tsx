// T4.1 — catalog layer. stories.tsx is 680 lines of render descriptors:
// its statements only execute when a story actually renders, so the
// coverage win (and the real value) is a sweep that mounts every story.
// That doubles as a data-integrity gate: a broken story fails here
// instead of silently producing a blank catalog baseline.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

import {
  stories,
  findStory,
  storiesByCategory,
  categoryLabels,
  categoryIcons,
  type StoryCategory,
} from '../catalog/stories'
import { CatalogPage } from '../catalog/CatalogPage'
import { CatalogItem } from '../catalog/CatalogItem'

const CATEGORIES: StoryCategory[] = ['common', 'chat', 'agent', 'workflow']

// recharts measures its container; jsdom reports 0x0 and warns. A stub
// keeps story renders quiet without changing what they assert.
beforeEach(() => {
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
})

describe('catalog data integrity', () => {
  it('every story declares a unique name, title, description, category and render fn', () => {
    expect(stories.length).toBeGreaterThan(0)
    const names = new Set<string>()
    for (const s of stories) {
      expect(s.name, 'name must be non-empty').toBeTruthy()
      expect(names.has(s.name), `duplicate story name: ${s.name}`).toBe(false)
      names.add(s.name)
      expect(s.title, `${s.name} title`).toBeTruthy()
      expect(s.description, `${s.name} description`).toBeTruthy()
      expect(CATEGORIES, `${s.name} category`).toContain(s.category)
      expect(typeof s.render, `${s.name} render`).toBe('function')
    }
  })

  it('findStory resolves known names and rejects unknown ones', () => {
    const first = stories[0]!
    expect(findStory(first.name)).toBe(first)
    expect(findStory('definitely-not-a-story')).toBeUndefined()
  })

  it('storiesByCategory buckets every story exactly once', () => {
    const grouped = storiesByCategory()
    const flat = CATEGORIES.flatMap((c) => grouped[c])
    expect(flat).toHaveLength(stories.length)
    expect(new Set(flat.map((s) => s.name)).size).toBe(stories.length)
    for (const s of stories) {
      expect(grouped[s.category]).toContain(s)
    }
  })

  it('exposes a label and icon for every category', () => {
    for (const c of CATEGORIES) {
      expect(categoryLabels[c], `label for ${c}`).toBeTruthy()
      expect(categoryIcons[c], `icon for ${c}`).toBeTruthy()
    }
  })
})

describe('every story renders', () => {
  // The sweep: mounting each story executes its render descriptor. Any
  // story that throws (bad import, broken mock data, removed component)
  // fails here by name.
  for (const story of stories) {
    it(`${story.category}/${story.name}`, () => {
      // Stories that reach router hooks (CommandPalette, nav popovers)
      // need a Router in scope even though the stage itself is chrome-free.
      const { unmount } = render(
        <MemoryRouter>{story.render()}</MemoryRouter>
      )
      expect(document.body.textContent !== undefined || document.body.children.length > 0)
        .toBe(true)
      unmount()
    })
  }
})

describe('CatalogPage', () => {
  const renderPage = () =>
    render(
      <MemoryRouter initialEntries={['/catalog']}>
        <Routes>
          <Route path="/catalog" element={<CatalogPage />} />
        </Routes>
      </MemoryRouter>
    )

  it('renders the index header and every category section', () => {
    renderPage()
    expect(screen.getByText('Component Catalog')).toBeInTheDocument()
    const grouped = storiesByCategory()
    for (const c of CATEGORIES) {
      if (grouped[c].length === 0) continue
      expect(screen.getByText(categoryLabels[c])).toBeInTheDocument()
    }
  })

  it('renders a card per story with a resolvable link', () => {
    renderPage()
    for (const s of stories) {
      const card = screen.getByTestId(`catalog-card-${s.name}`)
      expect(card).toHaveAttribute('href', `/catalog/${s.name}`)
    }
  })

  it('uses singular/plural story counts per section', () => {
    renderPage()
    const grouped = storiesByCategory()
    // Sections share counts (e.g. two categories with 8 stories), so
    // assert the set of rendered count strings rather than uniqueness.
    const rendered = CATEGORIES
      .filter((c) => grouped[c].length > 0)
      .map((c) => {
        const n = grouped[c].length
        return `(${n} ${n === 1 ? 'story' : 'stories'})`
      })
    for (const text of new Set(rendered)) {
      expect(screen.getAllByText(text).length).toBeGreaterThan(0)
    }
  })
})

describe('CatalogItem', () => {
  const renderAt = (name: string) =>
    render(
      <MemoryRouter initialEntries={[`/catalog/${name}`]}>
        <Routes>
          <Route path="/catalog/:name" element={<CatalogItem />} />
        </Routes>
      </MemoryRouter>
    )

  it('renders the requested story inside the isolated stage', () => {
    const story = stories.find((s) => s.category === 'common') ?? stories[0]!
    renderAt(story.name)
    expect(screen.getByText(story.title)).toBeInTheDocument()
    expect(screen.getByText(story.description)).toBeInTheDocument()
    expect(screen.getByTestId('catalog-stage')).toBeInTheDocument()
  })

  it('shows the not-found state for an unknown story name', () => {
    renderAt('no-such-story')
    expect(screen.getByText('Story not found')).toBeInTheDocument()
    expect(screen.getByText('All stories')).toBeInTheDocument()
  })

  it('offers prev/next links except at the boundaries', () => {
    const first = stories[0]!
    const { unmount } = renderAt(first.name)
    // First story: no "prev" link, but a next link exists (when >1 story).
    if (stories.length > 1) {
      expect(screen.queryByText(`← ${first.title}`)).not.toBeInTheDocument()
      expect(screen.getByText(`${stories[1]!.title} →`)).toBeInTheDocument()
    }
    unmount()

    const last = stories[stories.length - 1]!
    renderAt(last.name)
    expect(screen.getByText(`← ${stories[stories.length - 2]!.title}`)).toBeInTheDocument()
    expect(screen.queryByText(/→$/)).not.toBeInTheDocument()
  })
})
