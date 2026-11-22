import { create } from 'zustand'

export type WorkMode = 'chat' | 'monitor' | 'focus'
export type ChatLayout = 'bubble' | 'flat'
export type Density = 'compact' | 'comfortable' | 'spacious'

/**
 * Per-density presets for the two-column layout. The left chat column
 * is `flex-1` (absorbs all leftover width), so only the right panel
 * carries a fixed ratio of the parent flex container.
 */
const DENSITY_PRESETS: Record<Density, { rightRatio: number }> = {
  compact:    { rightRatio: 0.28 },
  comfortable: { rightRatio: 0.30 },
  spacious:   { rightRatio: 0.34 },
}

const CHAT_LAYOUT_KEY = 'sr-chat-layout'
const SIDEBAR_KEY = 'sr-sidebar-open'
const RIGHT_RATIO_KEY = 'sr-right-ratio'
const DENSITY_KEY = 'sr-density'
const RIGHT_VISIBLE_KEY = 'sr-right-visible'

const RIGHT_RATIO_MIN = 0.25
const RIGHT_RATIO_MAX = 0.55

function loadInitialLayout(): ChatLayout {
  if (typeof window === 'undefined') return 'bubble'
  return localStorage.getItem(CHAT_LAYOUT_KEY) === 'flat' ? 'flat' : 'bubble'
}

function loadInitialSidebar(): boolean {
  if (typeof window === 'undefined') return true
  return localStorage.getItem(SIDEBAR_KEY) !== 'false'
}

function loadInitialRightVisible(): boolean {
  if (typeof window === 'undefined') return true
  return localStorage.getItem(RIGHT_VISIBLE_KEY) !== 'false'
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function loadInitialDensity(): Density {
  if (typeof window === 'undefined') return 'comfortable'
  const raw = localStorage.getItem(DENSITY_KEY)
  if (raw === 'compact' || raw === 'comfortable' || raw === 'spacious') return raw
  return 'comfortable'
}

function loadInitialRightRatio(density: Density): number {
  if (typeof window === 'undefined') return DENSITY_PRESETS[density].rightRatio
  // getItem returns null when absent; Number(null) === 0 which would be
  // clamped up — treat missing as the preset default.
  const raw = localStorage.getItem(RIGHT_RATIO_KEY)
  if (raw === null) return DENSITY_PRESETS[density].rightRatio
  const n = Number(raw)
  if (!Number.isFinite(n)) return DENSITY_PRESETS[density].rightRatio
  return clamp(n, RIGHT_RATIO_MIN, RIGHT_RATIO_MAX)
}

interface LayoutState {
  navWidth: number
  sidebarOpen: boolean
  rightPanelVisible: boolean
  narrowViewport: boolean
  desktopSidebarOpen: boolean
  desktopRightPanelVisible: boolean
  workMode: WorkMode
  rightRatio: number
  density: Density
  settingsOpen: boolean
  chatLayout: ChatLayout
  setNavWidth: (w: number) => void
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
  toggleRightPanel: () => void
  setNarrowViewport: (narrow: boolean) => void
  setWorkMode: (mode: WorkMode) => void
  setRightRatio: (r: number) => void
  setDensity: (d: Density) => void
  setSettingsOpen: (open: boolean) => void
  setChatLayout: (layout: ChatLayout) => void
}

export const useLayoutStore = create<LayoutState>()((set) => {
  const density = loadInitialDensity()
  const sidebarOpen = loadInitialSidebar()
  const rightPanelVisible = loadInitialRightVisible()
  return {
    navWidth: 64,
    sidebarOpen,
    rightPanelVisible,
    narrowViewport: false,
    desktopSidebarOpen: sidebarOpen,
    desktopRightPanelVisible: rightPanelVisible,
    workMode: 'monitor',
    rightRatio: loadInitialRightRatio(density),
    density,
    settingsOpen: false,
    chatLayout: loadInitialLayout(),
    setNavWidth: (w) => set({ navWidth: w }),
    toggleSidebar: () =>
      set((s) => {
        const next = !s.sidebarOpen
        if (s.narrowViewport) {
          return { sidebarOpen: next, rightPanelVisible: next ? false : s.rightPanelVisible }
        }
        if (typeof window !== 'undefined') {
          localStorage.setItem(SIDEBAR_KEY, String(next))
        }
        return { sidebarOpen: next }
      }),
    setSidebarOpen: (open) =>
      set((s) => {
        if (s.narrowViewport) {
          return { sidebarOpen: open, rightPanelVisible: open ? false : s.rightPanelVisible }
        }
        if (typeof window !== 'undefined') localStorage.setItem(SIDEBAR_KEY, String(open))
        return { sidebarOpen: open }
      }),
    toggleRightPanel: () =>
      set((s) => {
        const next = !s.rightPanelVisible
        if (s.narrowViewport) {
          return { rightPanelVisible: next, sidebarOpen: next ? false : s.sidebarOpen }
        }
        if (typeof window !== 'undefined') {
          localStorage.setItem(RIGHT_VISIBLE_KEY, String(next))
        }
        return { rightPanelVisible: next }
      }),
    setNarrowViewport: (narrow) =>
      set((s) => {
        if (s.narrowViewport === narrow) return s
        if (narrow) {
          return {
            narrowViewport: true,
            desktopSidebarOpen: s.sidebarOpen,
            desktopRightPanelVisible: s.rightPanelVisible,
            sidebarOpen: false,
            rightPanelVisible: false,
          }
        }
        return {
          narrowViewport: false,
          sidebarOpen: s.desktopSidebarOpen,
          rightPanelVisible: s.desktopRightPanelVisible,
        }
      }),
    setWorkMode: (mode) =>
      set((s) => s.narrowViewport
        ? { workMode: mode, desktopRightPanelVisible: mode === 'monitor' }
        : { workMode: mode, rightPanelVisible: mode === 'monitor' }),
    setRightRatio: (r) => {
      const clamped = clamp(r, RIGHT_RATIO_MIN, RIGHT_RATIO_MAX)
      if (typeof window !== 'undefined') {
        localStorage.setItem(RIGHT_RATIO_KEY, String(clamped))
      }
      set({ rightRatio: clamped })
    },
    setDensity: (d) => {
      const preset = DENSITY_PRESETS[d]
      if (typeof window !== 'undefined') {
        localStorage.setItem(DENSITY_KEY, d)
        // Switching density restores its preset width (user's manual
        // overrides are stored separately under their own key).
        localStorage.removeItem(RIGHT_RATIO_KEY)
      }
      set({
        density: d,
        rightRatio: preset.rightRatio,
      })
    },
    setSettingsOpen: (open) => set({ settingsOpen: open }),
    setChatLayout: (layout) => {
      if (typeof window !== 'undefined') {
        localStorage.setItem(CHAT_LAYOUT_KEY, layout)
      }
      set({ chatLayout: layout })
    },
  }
})
