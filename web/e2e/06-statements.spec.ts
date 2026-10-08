import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

// Runs after 04-ai-privacy (which leaves the fake OpenAI-compatible connection as the model
// for everything, with Local only off) and 05-onboarding, on the same fresh server.
const FIXTURES = fileURLToPath(new URL('../../tests/fixtures/statements/', import.meta.url))
const FAKE = process.env.FAKE_LLM_URL!

type Account = { id: string; provider: string; kind: string; nickname: string }

async function csrf(page: Page): Promise<string> {
  return (await (await page.request.get('/api/auth/session')).json()).csrf_token
}

async function aiCalls(page: Page): Promise<number> {
  return (await (await page.request.get(`${FAKE}/_calls`)).json()).count
}

async function ensureAccounts(page: Page) {
  const headers = { 'X-CSRF-Token': await csrf(page) }
  const people = (await (await page.request.get('/api/household/people')).json()).people
  let owner = people.find((p: { role: string }) => p.role === 'adult')
  if (!owner) {
    owner = await (await page.request.post('/api/household/people', {
      headers, data: { display_name: 'Alex Example', role: 'adult' },
    })).json()
  }
  const accounts: Account[] = (await (await page.request.get('/api/accounts')).json()).accounts
  const monzo = accounts.filter((a) => a.provider === 'monzo' && a.kind === 'current')
  expect(monzo.length, 'these tests need at most one Monzo current account').toBeLessThanOrEqual(1)
  if (monzo.length === 0) {
    await page.request.post('/api/accounts', {
      headers, data: { provider: 'monzo', kind: 'current', nickname: 'E2E Monzo', owner_ids: [owner.id] },
    })
  }
  if (!accounts.some((a) => a.nickname === 'E2E Savings')) {
    await page.request.post('/api/accounts', {
      headers, data: { provider: 'nationwide', kind: 'savings', nickname: 'E2E Savings', owner_ids: [owner.id] },
    })
  }
}

async function openStatements(page: Page) {
  await signIn(page)
  await ensureAccounts(page)
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Statements' }).click()
  await expect(page.getByRole('heading', { name: 'Statements', level: 1 })).toBeVisible()
}

test('a Monzo export is recognised and imported without any questions', async ({ page }) => {
  await openStatements(page)
  await page.getByLabel('Choose files').setInputFiles(`${FIXTURES}csv/monzo.csv`)
  const card = page.getByRole('listitem', { name: 'monzo.csv' })
  await expect(card.getByText('Imported', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect(card.getByText(/^9 new transactions/)).toBeVisible()
  await card.getByRole('link', { name: 'View transactions' }).click()
  await expect(page.getByRole('cell', { name: 'Greenbasket Stores' })).toBeVisible()
  await expect(page.getByRole('cell', { name: '01/10/2026' }).first()).toBeVisible()
  await expect(page.getByRole('cell', { name: '-£42.18' })).toBeVisible()
})

test('an unfamiliar CSV layout is learned once and then remembered', async ({ page }) => {
  await openStatements(page)
  const before = await aiCalls(page)
  await page.getByLabel('Choose files').setInputFiles(`${FIXTURES}csv-unknown/credit-union.csv`)
  const card = page.getByRole('listitem', { name: 'credit-union.csv' })
  await expect(card.getByRole('heading', { name: 'Which account is this?' })).toBeVisible({ timeout: 30_000 })
  await card.getByLabel('+ New account').check()
  await card.getByLabel('Bank or card provider').selectOption('other')
  await card.getByLabel('Provider name').fill('Example Credit Union')
  await card.getByLabel('Account type').selectOption('current')
  await card.getByLabel('Nickname').fill('Credit union')
  await card.getByRole('button', { name: 'Use this account' }).click()
  await expect(card.getByText('Imported', { exact: true })).toBeVisible({ timeout: 60_000 })
  const afterFirst = await aiCalls(page)
  expect(afterFirst).toBe(before + 1) // the column layout was proposed once
  await page.getByLabel('Choose files').setInputFiles(`${FIXTURES}csv-unknown/credit-union-nov.csv`)
  const november = page.getByRole('listitem', { name: 'credit-union-nov.csv' })
  // The layout is remembered, but a file with no bank evidence still asks which account it is
  // (memory only pre-selects): the answer is already chosen, and no AI call is needed.
  await expect(november.getByRole('heading', { name: 'Which account is this?' })).toBeVisible({ timeout: 30_000 })
  await expect(november.getByRole('radio', { name: /^Credit union/ })).toBeChecked()
  await november.getByRole('button', { name: 'Use this account' }).click()
  await expect(november.getByText('Imported', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect(november.getByText(/^Credit union/)).toBeVisible()
  expect(await aiCalls(page)).toBe(afterFirst) // the layout was remembered: no second AI call
})

test('a file that cannot say which account it is from asks, and uses the answer', async ({ page }) => {
  await openStatements(page)
  await page.getByLabel('Choose files').setInputFiles(`${FIXTURES}qif/bank.qif`)
  const card = page.getByRole('listitem', { name: 'bank.qif' })
  await expect(card.getByRole('heading', { name: 'Which account is this?' })).toBeVisible({ timeout: 30_000 })
  await card.getByRole('radio', { name: /^E2E Savings/ }).check()
  await card.getByRole('button', { name: 'Use this account' }).click()
  await expect(card.getByText('Imported', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect(card.getByText(/^E2E Savings \(Nationwide\)/)).toBeVisible()
  await page.reload()
  await expect(page.getByRole('listitem', { name: 'bank.qif' }).getByText('Imported', { exact: true })).toBeVisible()
})

test('a renamed program and a HEIC photo are refused with a reason', async ({ page }) => {
  await openStatements(page)
  await page.getByLabel('Choose files').setInputFiles([
    { name: 'statement.csv', mimeType: 'text/csv', buffer: Buffer.from('MZ\u0090\u0000\u0003\u0000\u0000\u0000') },
    { name: 'photo.heic', mimeType: 'image/heic', buffer: Buffer.concat([Buffer.from([0, 0, 0, 24]), Buffer.from('ftypheic')]) },
  ])
  const alert = page.getByRole('alert').filter({ hasText: "These files weren't added" })
  await expect(alert).toContainText("statement.csv: This doesn't look like a statement file.")
  await expect(alert).toContainText('photo.heic: HEIC photos')
})
