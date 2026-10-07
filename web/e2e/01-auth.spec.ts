import { expect, test } from '@playwright/test'
import { ADMIN, signIn } from './helpers'

test('first run asks for an admin account, then signs in', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Set up Tuppence' })).toBeVisible()
  await page.getByLabel('Username').fill(ADMIN.username)
  await page.getByLabel('Password').fill('short')
  await page.getByRole('button', { name: 'Create account' }).click()
  // browser-side minlength blocks submission; type a real password
  await page.getByLabel('Password').fill(ADMIN.password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Tuppence', level: 1 })).toBeVisible()
  await expect(page.getByText(/Connected · v.* · server/)).toBeVisible()
})

test('sign out, wrong password, then sign in', async ({ page }) => {
  await signIn(page)
  await page.getByRole('button', { name: /Sign out/ }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.getByLabel('Username').fill(ADMIN.username)
  await page.getByLabel('Password').fill('wrong-password-xx')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toHaveText('Wrong username or password.')
  await page.getByLabel('Password').fill(ADMIN.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
})

test('session cookie is HttpOnly and SameSite=Strict', async ({ page, context }) => {
  await signIn(page)
  const cookie = (await context.cookies()).find((c) => c.name === 'tuppence_session')!
  expect(cookie.httpOnly).toBe(true)
  expect(cookie.sameSite).toBe('Strict')
})
