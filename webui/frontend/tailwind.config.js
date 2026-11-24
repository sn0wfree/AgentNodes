/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#f0f9ff',
          100: '#e0f2fe',
          200: '#bae6fd',
          300: '#7dd3fc',
          400: '#38bdf8',
          500: '#0ea5e9',
          600: '#0284c7',
          700: '#0369a1',
          800: '#075985',
          900: '#0c4a6e',
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
