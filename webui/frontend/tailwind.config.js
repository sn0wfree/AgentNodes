/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // 配色统一（2026-11-26 阶段 1）：primary 数字阶梯指向 CSS 变量
        // （--primary-50..950，globals.css 双主题定义），不再是写死的 sky 色板。
        // DEFAULT = --primary（dark 白 / light 深灰）。
        primary: {
          DEFAULT: 'var(--primary)',
          50: 'var(--primary-50)',
          100: 'var(--primary-100)',
          200: 'var(--primary-200)',
          300: 'var(--primary-300)',
          400: 'var(--primary-400)',
          500: 'var(--primary-500)',
          600: 'var(--primary-600)',
          700: 'var(--primary-700)',
          800: 'var(--primary-800)',
          900: 'var(--primary-900)',
          950: 'var(--primary-950)',
        },
        accent: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
          800: '#1e40af',
          900: '#1e3a8a',
        },
        // 中性色语义入口（阶段 1）：surface/text/border 指向 CSS 变量，
        // 组件迁移（阶段 2）后调色只改 globals.css 两处（dark/light）。
        surface: 'var(--surface)',
        'surface-raised': 'var(--surface-raised)',
        'text-secondary': 'var(--text-secondary)',
        'border-strong': 'var(--border-strong)',
        'border-subtle': 'var(--border-subtle)',
        // B-2：状态色语义入口，指向 globals.css 的 9-token 组（双主题自动适配）
        warning: 'var(--warning)',
        'warning-fill': 'var(--warning-fill)',
        'warning-fg': 'var(--warning-foreground)',
        // 语义色统一（C1）：success/danger/thinking/info 全量映射，
        // 组件只用语义类名，调色只改 globals.css 两处（dark/light）。
        success: 'var(--success)',
        'success-fg': 'var(--success-fg)',
        'success-fg-muted': 'var(--success-fg-muted)',
        'success-surface': 'var(--success-surface)',
        danger: 'var(--danger)',
        'danger-fg': 'var(--danger-fg)',
        'danger-fg-muted': 'var(--danger-fg-muted)',
        'danger-surface': 'var(--danger-surface)',
        thinking: 'var(--thinking)',
        'thinking-fg': 'var(--thinking-fg)',
        'thinking-fg-muted': 'var(--thinking-fg-muted)',
        'thinking-surface': 'var(--thinking-surface)',
        info: 'var(--info)',
        'info-fg': 'var(--info-fg)',
        'info-fg-muted': 'var(--info-fg-muted)',
        'info-surface': 'var(--info-surface)',
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', '"Segoe UI"', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        xl: '0.875rem',
        '2xl': '1.125rem',
      },
    },
  },
  plugins: [],
}
