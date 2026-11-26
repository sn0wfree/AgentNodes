// 主题配置注入 · 单元测试（§9 批 6/7）
//
// 覆盖 injectThemeStyle 的注入语义：
// - 只注入 -- 前缀变量（防止非法 CSS 注入）
// - 双主题各自作用域（:root[data-theme='dark'|'light']）
// - 重复注入复用同一节点（避免样式节点堆积）
// - 槽变量（var(--x) 引用）与材料变量（RGB 通道）都要能注入
// - 非法值/非法键被过滤

import { describe, it, expect, beforeEach } from 'vitest'
import { injectThemeStyle } from '../utils/themeConfig'

function styleText(): string {
  return document.querySelector('style[data-theme-config]')?.textContent ?? ''
}

describe('injectThemeStyle — §9 批 6/7 注入语义', () => {
  beforeEach(() => {
    document.querySelectorAll('style[data-theme-config]').forEach((e) => e.remove())
  })

  it('injects dark and light into separate scoped rules', () => {
    injectThemeStyle({
      dark: { '--success-emerald-400-rgb': 'var(--green-400-rgb)' },
      light: { '--success-emerald-400-rgb': 'var(--emerald-400-rgb)' },
    })
    const text = styleText()
    expect(text).toContain(":root[data-theme='dark'] {")
    expect(text).toContain(":root[data-theme='light'] {")
    // 两个主题的值不能串台
    const darkBlock = text.split(":root[data-theme='dark'] {")[1].split('}')[0]
    const lightBlock = text.split(":root[data-theme='light'] {")[1].split('}')[0]
    expect(darkBlock).toContain('var(--green-400-rgb)')
    expect(darkBlock).not.toContain('var(--emerald-400-rgb)')
    expect(lightBlock).toContain('var(--emerald-400-rgb)')
  })

  it('injects slot variables referencing other variables verbatim', () => {
    // 槽值是 var(--x) 引用，注入时不能被改写或转义
    injectThemeStyle({ dark: { '--danger-red-400-rgb': 'var(--red-400-rgb)' }, light: {} })
    expect(styleText()).toContain('--danger-red-400-rgb: var(--red-400-rgb);')
  })

  it('injects RGB channel material values', () => {
    injectThemeStyle({ dark: { '--emerald-400-rgb': '52 211 153' }, light: {} })
    expect(styleText()).toContain('--emerald-400-rgb: 52 211 153;')
  })

  it('drops keys that do not start with --', () => {
    injectThemeStyle({
      dark: { '--ok': '#fff', 'evil': 'body{display:none}', 'x': 'y' } as Record<string, string>,
      light: {},
    })
    const text = styleText()
    expect(text).toContain('--ok: #fff;')
    expect(text).not.toContain('evil')
    expect(text).not.toContain('body{display:none}')
  })

  it('drops non-string values', () => {
    injectThemeStyle({
      dark: { '--ok': '#fff', '--bad': 123 } as unknown as Record<string, string>,
      light: {},
    })
    const text = styleText()
    expect(text).toContain('--ok: #fff;')
    expect(text).not.toContain('--bad')
  })

  it('reuses one style element across repeated injections', () => {
    injectThemeStyle({ dark: { '--a': '#111' }, light: {} })
    injectThemeStyle({ dark: { '--a': '#222' }, light: {} })
    expect(document.querySelectorAll('style[data-theme-config]').length).toBe(1)
    expect(styleText()).toContain('#222')
    expect(styleText()).not.toContain('#111')
  })

  it('recreates the style element if it was removed from the DOM', () => {
    injectThemeStyle({ dark: { '--a': '#111' }, light: {} })
    document.querySelectorAll('style[data-theme-config]').forEach((e) => e.remove())
    injectThemeStyle({ dark: { '--a': '#222' }, light: {} })
    expect(document.querySelectorAll('style[data-theme-config]').length).toBe(1)
    expect(styleText()).toContain('#222')
  })

  it('does nothing when both themes are empty', () => {
    injectThemeStyle({ dark: {}, light: {} })
    expect(document.querySelector('style[data-theme-config]')).toBeNull()
  })

  it('injects only the non-empty theme when one side is empty', () => {
    injectThemeStyle({ dark: { '--a': '#111' }, light: {} })
    const text = styleText()
    expect(text).toContain(":root[data-theme='dark'] {")
    expect(text).not.toContain(":root[data-theme='light'] {")
  })
})
