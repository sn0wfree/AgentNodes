// 配色统一 · 阶段 1（设计 2026-11-26）：中性色语义 token 必须存在。
// globals.css 双主题定义变量 → tailwind.config 映射为语义类。
// 组件迁移（阶段 2）依赖这些类名，此测试是契约锚点。

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const cssPath = resolve(__dirname, '../../src/styles/globals.css')
const twPath = resolve(__dirname, '../../tailwind.config.js')

const css = readFileSync(cssPath, 'utf-8')
const tw = readFileSync(twPath, 'utf-8')

// 从 CSS 抽取指定主题块的变量名集合
function varsOf(themeBlock: string): Set<string> {
  const start = css.indexOf(`[data-theme='${themeBlock}']`)
  const next = css.indexOf('[data-theme=', start + 1)
  const block = css.slice(start, next === -1 ? css.length : next)
  return new Set([...block.matchAll(/--[\w-]+(?=\s*:)/g)].map((m) => m[0]))
}

describe('semantic color tokens (stage 1 of palette unification)', () => {
  it('defines surface tokens in both dark and light themes', () => {
    for (const theme of ['dark', 'light'] as const) {
      const vars = varsOf(theme)
      expect(vars.has('--surface'), `${theme} --surface`).toBe(true)
      expect(vars.has('--surface-raised'), `${theme} --surface-raised`).toBe(true)
    }
  })

  it('defines text hierarchy tokens in both themes', () => {
    for (const theme of ['dark', 'light'] as const) {
      const vars = varsOf(theme)
      expect(vars.has('--foreground'), `${theme} --foreground`).toBe(true)
      expect(vars.has('--text-secondary'), `${theme} --text-secondary`).toBe(true)
      expect(vars.has('--muted-foreground'), `${theme} --muted-foreground`).toBe(true)
    }
  })

  it('defines border strength tokens in both themes', () => {
    for (const theme of ['dark', 'light'] as const) {
      const vars = varsOf(theme)
      expect(vars.has('--border'), `${theme} --border`).toBe(true)
      expect(vars.has('--border-strong'), `${theme} --border-strong`).toBe(true)
    }
  })

  it('maps surface/text/border semantic classes in tailwind config', () => {
    expect(tw).toMatch(/['"]?surface['"]?\s*:\s*['"]var\(--surface\)/)
    expect(tw).toMatch(/['"]?surface-raised['"]?\s*:\s*['"]var\(--surface-raised\)/)
    expect(tw).toMatch(/['"]?text-secondary['"]?\s*:\s*['"]var\(--text-secondary\)/)
    expect(tw).toMatch(/['"]?border-strong['"]?\s*:\s*['"]var\(--border-strong\)/)
    expect(tw).toMatch(/['"]?border-subtle['"]?\s*:\s*['"]var\(--border-subtle\)/)
  })

  it('derives the primary numeric ladder from CSS variables (not hardcoded sky)', () => {
    // 阶段 1 关键一致性修复：primary 色板曾是写死的 sky 蓝（写死在 config），
    // 与 --primary（dark 白 / light 深灰）矛盾。现在每一档都指向
    // globals.css 双主题的 --primary-NN 变量，可由配置/主题统一管理。
    for (const shade of [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950]) {
      expect(
        tw,
        `primary-${shade}`,
      ).toMatch(new RegExp(`${shade}:\\s*['"]var\\(--primary-${shade}\\)`))
    }
    expect(tw).toMatch(/DEFAULT:\s*'var\(--primary\)'/)
  })

  it('defines --primary-50..950 in both themes', () => {
    for (const theme of ['dark', 'light'] as const) {
      const vars = varsOf(theme)
      for (const shade of [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950]) {
        expect(vars.has(`--primary-${shade}`), `${theme} --primary-${shade}`).toBe(true)
      }
    }
  })
})
