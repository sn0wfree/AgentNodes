import { Palette } from 'lucide-react'
import { Section, ThemeBtn, SizeBtn } from './shared'
import { useThemeStore, type Theme } from '../../stores/theme'

/**
 * Appearance settings.
 *
 * Theme is wired to the real theme store (the same one the top-bar toggle
 * uses), so choosing 暗色/亮色 here takes effect immediately and persists.
 *
 * Font size has no backing store yet — every size in the app is a literal
 * px value in component styles, so there is nothing to bind to. The
 * buttons are rendered disabled with an explicit hint rather than
 * pretending to be interactive (they used to be silent no-ops, which read
 * as a bug).
 */
export function AppearanceSection() {
  const theme = useThemeStore((s) => s.theme)
  const setTheme = useThemeStore((s) => s.setTheme)

  const themeOptions: Array<{ label: string; value: Theme }> = [
    { label: '暗色', value: 'dark' },
    { label: '亮色', value: 'light' },
  ]

  return (
    <Section icon={Palette} title="外观设置">
      <div className="space-y-3 text-sm">
        <div>
          <p className="mb-2 text-muted">主题</p>
          <div className="flex gap-2">
            {themeOptions.map((opt) => (
              <ThemeBtn
                key={opt.value}
                label={opt.label}
                active={theme === opt.value}
                onClick={() => setTheme(opt.value)}
              />
            ))}
          </div>
        </div>
        <div>
          <p className="mb-2 text-muted">
            字体大小
            <span className="ml-2 text-xs text-muted">即将支持</span>
          </p>
          <div className="flex gap-2">
            <SizeBtn label="小" disabled />
            <SizeBtn label="中" active disabled />
            <SizeBtn label="大" disabled />
          </div>
        </div>
      </div>
    </Section>
  )
}
