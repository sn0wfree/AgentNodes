// 配色统一 · 阶段 1（设计 2026-11-26）：中性色语义 token 必须存在。
// globals.css 双主题定义变量 → tailwind.config 映射为语义类。
// 组件迁移（阶段 2）依赖这些类名，此测试是契约锚点。

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const cssPath = resolve(__dirname, '../../src/styles/globals.css')
const twPath = resolve(__dirname, '../../tailwind.config.js')
const composerPath = resolve(__dirname, '../../src/components/chat/Composer.tsx')
const statusBarPath = resolve(__dirname, '../../src/components/chat/ComposerStatusBar.tsx')

const css = readFileSync(cssPath, 'utf-8')
const tw = readFileSync(twPath, 'utf-8')
const composer = readFileSync(composerPath, 'utf-8')
const statusBar = readFileSync(statusBarPath, 'utf-8')

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

// 浮窗实底契约（回归防线）：codemod 曾把 !bg-slate-900/95 误迁为
// !bg-background/95——Tailwind 对 CSS 变量色不支持 /95 透明度修饰符，
// 类不生成 → .glass 的 4.5% 白透出 → 浮窗文字看不清。
describe('popover solid background (regression guard)', () => {
  it('defines --surface-solid in both dark and light themes', () => {
    for (const theme of ['dark', 'light'] as const) {
      expect(varsOf(theme).has('--surface-solid'), `${theme} --surface-solid`).toBe(true)
    }
  })

  it('uses the solid-token utility on all 4 popovers', () => {
    const solid = '!bg-[var(--surface-solid)]'
    const count = (s: string) => s.split(solid).length - 1
    // 7 段明细浮窗 + persona / 模型 / 思考三个下拉菜单
    expect(count(composer)).toBe(1)
    expect(count(statusBar)).toBe(3)
  })

  it('does not reintroduce variable-color opacity modifiers on popovers', () => {
    // !bg-background/95 这类写法无效（变量色 + /alpha）；一旦出现即回归
    expect(composer).not.toContain('!bg-background/')
    expect(statusBar).not.toContain('!bg-background/')
  })
})

// §9 批 2 契约：globals.css 的颜色只允许写在变量声明值域内；
// 选择器里直接写 hex/rgb(a)/hsl(a) 会让 theme.json 无法覆盖该细节。
describe('globals.css all colors come from variable declarations', () => {
  it('has no color literals outside variable declarations', () => {
    // 先去掉 CSS 注释与所有 `--var: ...;` 声明（含跨行值），再扫描剩余消费点。
    const withoutDecls = css
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/--[\w-]+\s*:\s*[^;]+;/g, '')
    const colorRe = /#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)/g
    const offenders: string[] = []
    for (const line of withoutDecls.split('\n')) {
      const trimmed = line.trim()
      if (trimmed.startsWith('//')) continue
      for (const m of line.matchAll(colorRe)) offenders.push(`${trimmed}: ${m[0]}`)
    }
    expect(offenders, `globals.css 变量声明外仍有颜色字面量: ${offenders.join(' | ')}`).toEqual([])
  })

  it('declares chrome and badge tokens in both themes', () => {
    const required = [
      '--rf-control-shadow', '--rf-control-border', '--rf-button-bg',
      '--rf-button-fg', '--rf-minimap-border', '--rf-handle-border',
      '--badge-builtin-bg', '--badge-builtin-fg', '--badge-user-bg',
      '--badge-failed-bg', '--badge-neutral-fg', '--dark-mode-card-bg',
      '--wf-card-title-fg', '--wf-minimap-bg',
    ]
    for (const theme of ['dark', 'light'] as const) {
      const vars = varsOf(theme)
      for (const token of required) {
        expect(vars.has(token), `${theme} ${token}`).toBe(true)
      }
    }
  })
})
