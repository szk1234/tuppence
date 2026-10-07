import { expect, type Page } from '@playwright/test'

export const ADMIN = { username: 'alex', password: 'a-long-test-passphrase' }

export async function signIn(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.getByLabel('Username').fill(ADMIN.username)
  await page.getByLabel('Password').fill(ADMIN.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
}

/** The first-run "Set up Tuppence" screen of a server with no data: creates the admin and signs in. */
export async function firstRunSetup(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Set up Tuppence' })).toBeVisible()
  await page.getByLabel('Username').fill(ADMIN.username)
  await page.getByLabel('Password').fill(ADMIN.password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
}

/** YYYY-MM-DD for today plus `days`, in the machine's own timezone (as the app computes "today"). */
export function isoFromToday(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** "2027-01-05" -> "5 Jan 2027", as the app's ukDate shows it. */
export function ukDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(y, m - 1, d))
}
