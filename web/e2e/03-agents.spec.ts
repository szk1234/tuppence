import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('tune an agent, reset it, and switch preset', async ({ page }) => {
  await signIn(page)
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Agents' }).click()
  const researcher = page.getByRole('region', { name: 'researcher' })
  await researcher.getByLabel('max_merchants_per_run').fill('12')
  await researcher.getByRole('button', { name: 'Save researcher' }).click()
  await expect(researcher.getByText('Changed by you')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('region', { name: 'researcher' }).getByLabel('max_merchants_per_run')).toHaveValue('12')

  await page.getByRole('region', { name: 'researcher' }).getByRole('button', { name: 'Reset' }).click()
  await expect(page.getByRole('region', { name: 'researcher' }).getByLabel('max_merchants_per_run')).toHaveValue('20')

  await page.getByLabel('Preset').selectOption('frugal')
  await expect(page.getByText('Preset changed to frugal.')).toBeVisible()
  await expect(page.getByRole('region', { name: 'researcher' }).getByLabel('Enabled')).not.toBeChecked()
  await page.getByLabel('Preset').selectOption('balanced')
})
