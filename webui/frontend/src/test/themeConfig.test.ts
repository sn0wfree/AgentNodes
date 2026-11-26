// 主题外置（阶段 3）：CSS 变量注入契约测试

import { describe, it, expect, beforeEach } from 'vitest'
import { injectThemeStyle } from '../utils/themeConfig'

describe('themeConfig injection', () => {
  beforeEach(() => {
    document.querySelectorAll('style[data-theme-config]').forEach((e) => e.remove())
  })

  it('generates scoped rules for both themes with only -- variables', () => {
    injectThemeStyle({
      dark: { '--accent': '#ff0000', 'evil': 'body{display:none}' },
      light: { '--accent': '#00ff00' },
    })
    const el = document.querySelector('style[data-theme-config]') as HTMLStyleElement
    expect(el).not.toBeNull()
    expect(el.textContent).toContain(":root[data-theme='dark']")
    expect(el.textContent).toContain(":root[data-theme='light']")
    expect(el.textContent).toContain('--accent: #ff0000;')
    expect(el.textContent).not.toContain('evil')
  })

  it('reuses a single style element on repeated injection', () => {
    injectThemeStyle({ dark: { '--accent': '#111111' }, light: {} })
    injectThemeStyle({ dark: { '--accent': '#222222' }, light: {} })
    expect(document.querySelectorAll('style[data-theme-config]').length).toBe(1)
    expect(document.querySelector('style[data-theme-config]')!.textContent).toContain('#222222')
  })

  it('does nothing when both themes are empty', () => {
    injectThemeStyle({ dark: {}, light: {} })
    expect(document.querySelector('style[data-theme-config]')).toBeNull()
  })
})
