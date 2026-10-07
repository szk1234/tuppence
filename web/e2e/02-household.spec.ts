import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('add people, set district, reject a full postcode, remove a person', async ({ page }) => {
  await signIn(page)
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Household' }).click()
  await expect(page.getByRole('heading', { name: 'Household', level: 1 })).toBeVisible()

  await page.getByLabel('Nation').selectOption('england')
  await page.getByLabel('Postcode district').fill('LS6 2AB')
  await page.getByRole('button', { name: 'Save household' }).click()
  await expect(page.getByRole('alert')).toContainText('first part of your postcode')
  await page.getByLabel('Postcode district').fill('ls6')
  await page.getByRole('button', { name: 'Save household' }).click()
  await expect(page.getByText('Household saved.')).toBeVisible()
  await expect(page.getByLabel('Postcode district')).toHaveValue('LS6')

  await page.getByLabel('Name').fill('Alex Example')
  await page.getByRole('button', { name: 'Add person' }).click()
  await page.getByLabel('Name').fill('Kid A')
  await page.getByLabel('Role').selectOption('child')
  await page.getByLabel('Birth year (children)').fill('2019')
  await page.getByRole('button', { name: 'Add person' }).click()
  await expect(page.getByText('born 2019')).toBeVisible()

  await page.reload()
  await expect(page.getByText('Alex Example')).toBeVisible()
  await page.getByRole('button', { name: 'Remove Kid A' }).click()
  await expect(page.getByRole('listitem').filter({ hasText: 'Kid A' })).toHaveCount(0)
  await expect(page.getByRole('listitem').filter({ hasText: 'Alex Example' })).toHaveCount(1)
})

test('a stale edit from a second tab gets the conflict message', async ({ browser }) => {
  const ctx = await browser.newContext()
  const a = await ctx.newPage()
  const b = await ctx.newPage()
  await signIn(a)
  await a.goto('/settings/household')
  await b.goto('/settings/household')
  await a.getByLabel('Postcode district').fill('LS7')
  await a.getByRole('button', { name: 'Save household' }).click()
  await expect(a.getByText('Household saved.')).toBeVisible()
  await b.getByLabel('Postcode district').fill('LS8')
  await b.getByRole('button', { name: 'Save household' }).click()
  await expect(b.getByRole('alert')).toHaveText('This was changed somewhere else. Reload and try again.')
  await ctx.close()
})
