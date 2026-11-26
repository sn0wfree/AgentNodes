/**
 * 主题配置注入（配色统一阶段 3）。
 *
 * 后端 GET /api/system/theme 读取 ~/.agentnodes/theme.json：
 *   {"theme": {"dark": {"--accent": "<任意合法 CSS 颜色值>", ...}, "light": {...}}}
 * 启动时拉取，把变量注入 document.documentElement.style（覆盖 globals.css 默认值）。
 * 主题切换（data-theme）由 theme store 负责，注入的变量对两套主题同时生效。
 */
import { api } from '../api/client'

export interface ThemeConfig {
  dark: Record<string, string>
  light: Record<string, string>
}

let styleEl: HTMLStyleElement | null = null

/** 生成一条 :root[data-theme='dark'/'light'] 规则并插入 <head>。 */
export function injectThemeStyle(theme: ThemeConfig): void {
  if (typeof document === 'undefined') return
  const rules = (['dark', 'light'] as const)
    .map((mode) => {
      const decls = Object.entries(theme[mode] ?? {})
        .filter(([k, v]) => k.startsWith('--') && typeof v === 'string')
        .map(([k, v]) => `${k}: ${v};`)
        .join(' ')
      return decls ? `:root[data-theme='${mode}'] { ${decls} }` : ''
    })
    .filter(Boolean)
    .join('\n')
  if (!rules) return
  // 缓存节点若被外部移除（HMR / 测试清理），重建而非复用游离引用
  if (!styleEl || !styleEl.isConnected) {
    styleEl = document.createElement('style')
    styleEl.dataset.themeConfig = 'true'
    document.head.appendChild(styleEl)
  }
  styleEl.textContent = rules
}

/** 启动时拉取配置并注入；失败静默（保留 globals.css 默认主题）。 */
export async function loadThemeConfig(): Promise<void> {
  try {
    const theme = await api.get<ThemeConfig>('/system/theme')
    injectThemeStyle(theme)
  } catch {
    // 配置不可用 → 保留内置默认，不影响启动
  }
}
