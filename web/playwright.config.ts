import { defineConfig, devices } from '@playwright/test'

// `chromium` runs specs 01-04 against the shared server (TUPPENCE_URL). Specs tagged @fresh need a server with no data
// at all, so they run only in the `fresh` project, against TUPPENCE_FRESH_URL (scripts/e2e.sh starts that second server).
const freshUrl = process.env.TUPPENCE_FRESH_URL
const base = { trace: 'retain-on-failure' as const }

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { ...base, baseURL: process.env.TUPPENCE_URL ?? 'http://127.0.0.1:18080' },
  projects: [
    { name: 'chromium', grepInvert: /@fresh/, use: { ...devices['Desktop Chrome'] } },
    ...(freshUrl ? [{ name: 'fresh', grep: /@fresh/, use: { ...devices['Desktop Chrome'], baseURL: freshUrl } }] : []),
  ],
})
