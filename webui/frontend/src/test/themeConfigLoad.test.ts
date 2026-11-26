// 主题配置加载链路 · 单元测试（§9 批 9）
//
// 覆盖 loadThemeConfig：
// - 成功拉取 → 注入 CSS 变量
// - 拉取失败 → 静默降级（保留 globals.css 默认主题），不抛错、不阻断启动
// - 返回空配置 → 不产生空 style 节点
//
// themeConfig.ts 此前只有 injectThemeStyle 被测，loadThemeConfig 函数覆盖 0%。

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'

const getMock = vi.fn()

vi.mock('../api/client', () => ({
  api: {
    get: (...args: unknown[]) => getMock(...args),
  },
}))

import { loadThemeConfig } from '../utils/themeConfig'

function styleText(): string {
  return document.querySelector('style[data-theme-config]')?.textContent ?? ''
}

describe('loadThemeConfig — 启动时拉取并注入', () => {
  beforeEach(() => {
    getMock.mockReset()
    document.querySelectorAll('style[data-theme-config]').forEach((e) => e.remove())
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('requests /system/theme and injects the returned variables', async () => {
    getMock.mockResolvedValue({
      dark: { '--success-emerald-400-rgb': 'var(--green-400-rgb)' },
      light: { '--success-emerald-400-rgb': 'var(--emerald-400-rgb)' },
    })

    await loadThemeConfig()

    expect(getMock).toHaveBeenCalledWith('/system/theme')
    const text = styleText()
    expect(text).toContain(":root[data-theme='dark'] {")
    expect(text).toContain("--success-emerald-400-rgb: var(--green-400-rgb);")
    expect(text).toContain(":root[data-theme='light'] {")
  })

  it('silently keeps the built-in theme when the request fails', async () => {
    getMock.mockRejectedValue(new Error('network down'))

    // 不应抛出 —— 启动链路不能因配置不可用而中断
    await expect(loadThemeConfig()).resolves.toBeUndefined()
    expect(document.querySelector('style[data-theme-config]')).toBeNull()
  })

  it('silently keeps the built-in theme when the response is malformed', async () => {
    getMock.mockResolvedValue(null)

    await expect(loadThemeConfig()).resolves.toBeUndefined()
  })

  it('does not create an empty style element for an empty config', async () => {
    getMock.mockResolvedValue({ dark: {}, light: {} })

    await loadThemeConfig()

    expect(document.querySelector('style[data-theme-config]')).toBeNull()
  })

  it('injects slot variables that reference other variables verbatim', async () => {
    getMock.mockResolvedValue({
      dark: { '--danger-red-400-rgb': 'var(--red-400-rgb)' },
      light: {},
    })

    await loadThemeConfig()

    expect(styleText()).toContain('--danger-red-400-rgb: var(--red-400-rgb);')
  })
})
