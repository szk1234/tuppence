import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

// Runs after 04-ai-privacy (the fake OpenAI-compatible model answers every task through the
// oracle) and 06-statements, on the same server. The fixture is three months on one Starling
// current account: `uv run python -m evals.household --write-fixture` regenerates it.
const FIXTURE = fileURLToPath(new URL('../../tests/fixtures/statements/history/starling-3-months.csv', import.meta.url))

async function csrf(page: Page): Promise<string> {
  return (await (await page.request.get('/api/auth/session')).json()).csrf_token
}

async function ensureStarling(page: Page) {
  const headers = { 'X-CSRF-Token': await csrf(page) }
  const people = (await (await page.request.get('/api/household/people')).json()).people
  let owner = people.find((p: { role: string }) => p.role === 'adult')
  if (!owner) {
    owner = await (await page.request.post('/api/household/people', {
      headers, data: { display_name: 'Alex Example', role: 'adult' },
    })).json()
  }
  const accounts = (await (await page.request.get('/api/accounts')).json()).accounts
  if (!accounts.some((a: { provider: string }) => a.provider === 'starling')) {
    await page.request.post('/api/accounts', {
      headers, data: { provider: 'starling', kind: 'current', nickname: 'E2E Starling', owner_ids: [owner.id] },
    })
  }
}

async function waitForAnalysis(page: Page) {
  const run = page.getByRole('button', { name: 'Run analysis now' })
  await expect(run).toBeVisible({ timeout: 30_000 })
  await run.click()
  await expect.poll(async () => {
    const status = await (await page.request.get('/api/analysis')).json()
    return !status.running && !status.queued && status.last_run !== null
  }, { timeout: 90_000 }).toBe(true)
  await page.reload()
}

test('three months of statements become spending, a correction becomes a rule, and commitments appear', async ({ page }) => {
  await signIn(page)
  await ensureStarling(page)
  const nav = page.getByRole('navigation', { name: 'Main' })
  await nav.getByRole('link', { name: 'Statements' }).click()
  await page.getByLabel('Choose files').setInputFiles(FIXTURE)
  const card = page.getByRole('listitem', { name: 'starling-3-months.csv' })
  await expect(card.getByText('Imported', { exact: true })).toBeVisible({ timeout: 30_000 })

  await nav.getByRole('link', { name: 'Spending' }).click()
  await waitForAnalysis(page)
  await page.goto('/spending?on=2026-10-15')
  await expect(page.getByRole('heading', { name: 'October 2026' })).toBeVisible()

  const map = page.getByRole('group', { name: 'Spending by category' })
  await map.getByRole('button', { name: /^Other spending,/ }).click()
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' }).getByText('Other spending')).toBeVisible()
  await page.getByLabel('Category for Sunrise Bakery on 12/10/2026').selectOption('food.eating-out')
  await expect(page.getByText('Filed under Eating out.')).toBeVisible()
  await expect(page.getByText('2 other payments to Sunrise Bakery would change too.')).toBeVisible()
  await page.getByRole('button', { name: 'Apply to all from Sunrise Bakery' }).click()
  await expect(page.getByText('Rule saved: 2 more payments now follow it.')).toBeVisible()

  await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('button', { name: 'All spending' }).click()
  await map.getByRole('button', { name: /^Food & drink,/ }).click()
  await map.getByRole('button', { name: /^Eating out,/ }).click()
  await page.getByRole('button', { name: 'Why? Sunrise Bakery on 12/10/2026' }).click()
  await expect(page.getByText(/^You set this on \d\d\/\d\d\/\d{4}\.$/)).toBeVisible()

  await nav.getByRole('link', { name: 'Commitments' }).click()
  const streamly = page.getByRole('row', { name: /^Streamly/ })
  await expect(streamly).toContainText('Every month')
  await expect(streamly).toContainText('£9.99')
  await expect(streamly).toContainText('14/11/2026')

  await page.goto('/settings/rules')
  await expect(page.getByText(/Payments to Sunrise Bakery → Food & drink › Eating out/)).toBeVisible()
})
