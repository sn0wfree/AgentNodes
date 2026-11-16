import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    // forks pool: the threads pool keeps a jsdom worker alive after the
    // suite finishes (open-handle teardown hang), so `npm test` never exits.
    pool: 'forks',
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'json-summary'],
      // 失败时也要出覆盖率报告（默认 false：测试失败 → coverage/ 只剩 .tmp 残片）
      reportOnFailure: true,
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**', 'src/api/types.gen.ts'],
    },
  },
})