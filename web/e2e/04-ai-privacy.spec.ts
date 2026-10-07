import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

const FAKE = process.env.FAKE_LLM_URL ?? ''

async function goTo(page: Page, link: string) {
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: link, exact: true }).click()
}

async function setToggle(page: Page, label: string, on: boolean) {
  const box = page.getByLabel(label, { exact: true })
  if ((await box.isChecked()) === on) return
  const saved = page.waitForResponse((r) => r.url().includes('/api/settings/') && r.request().method() === 'PATCH')
  await box.setChecked(on)
  expect((await saved).ok()).toBeTruthy()
  if (on) await expect(box).toBeChecked()
  else await expect(box).not.toBeChecked()
}

async function tryIt(page: Page, text: string) {
  await page.getByLabel('Message').fill(text)
  await page.getByRole('button', { name: 'Send' }).click()
}

async function lastBody(page: Page): Promise<string> {
  const r = await page.request.get(`${FAKE}/_last`)
  expect(r.ok()).toBeTruthy()
  return JSON.stringify(await r.json())
}

test('connect a cloud-style AI, confirm the notice, pseudonymise and block with Local only', async ({ page }) => {
  expect(FAKE, 'FAKE_LLM_URL must be set (scripts/e2e.sh does this)').not.toBe('')
  await signIn(page)
  await page.request.post(`${FAKE}/_reset`)

  // 1. The household has Alex Example.
  await goTo(page, 'Household')
  if ((await page.getByRole('listitem').filter({ hasText: 'Alex Example' }).count()) === 0) {
    await page.getByLabel('Name').fill('Alex Example')
    await page.getByRole('button', { name: 'Add person' }).click()
    await expect(page.getByText('Alex Example added.')).toBeVisible()
  }

  // 2. Add an OpenAI connection pointed at the fake server (cloud presets always count as cloud).
  await goTo(page, 'AI')
  await expect(page.getByRole('heading', { name: 'AI', level: 1 })).toBeVisible()
  await page.getByLabel('Provider').selectOption('openai')
  await page.getByLabel('Base URL', { exact: true }).fill(`${FAKE}/v1`)
  await page.getByLabel('API key', { exact: true }).fill('sk-e2e')
  await page.getByRole('button', { name: 'Save connection' }).click()
  const card = page.getByRole('region', { name: 'OpenAI' })
  await expect(card.getByText('Internet')).toBeVisible()
  await expect(card.getByRole('button', { name: "Review what's sent" })).toBeVisible()

  // 3. Test finds both fake models.
  await card.getByRole('button', { name: 'Test' }).click()
  await expect(card.getByText('2 models found')).toBeVisible()

  // 4. Use fake-small for everything.
  const saved = page.waitForResponse((r) => r.url().includes('/api/settings/') && r.request().method() === 'PATCH')
  await page.getByLabel('Model for everything').selectOption({ label: 'OpenAI — fake-small' })
  expect((await saved).ok()).toBeTruthy()

  // 5. Trying it before the notice is acknowledged asks for confirmation.
  await tryIt(page, 'Hello from Alex Example')
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('will see the statement text Tuppence sends to it')).toBeVisible()
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.locator('p.reply')).toHaveCount(0)

  // 6. Review what's sent, then accept.
  await card.getByRole('button', { name: "Review what's sent" }).click()
  await expect(dialog.getByText('will see the statement text Tuppence sends to it')).toBeVisible()
  await dialog.getByRole('button', { name: 'I understand' }).click()
  await expect(dialog).toBeHidden()

  // 7. Now it answers.
  await tryIt(page, 'Hello from Alex Example')
  await expect(page.locator('p.reply')).toHaveText('Echo: Hello from Alex Example')

  // 8. The privacy log shows the call.
  await goTo(page, 'Privacy')
  await expect(page.getByRole('heading', { name: 'Privacy', level: 1 })).toBeVisible()
  const sent = page.getByRole('row').filter({ hasText: '127.0.0.1' }).filter({ hasText: 'sent' })
  await expect(sent.first()).toBeVisible()

  // 9. Pseudonymise: the model sees "Adult A", the reply reads naturally again.
  await setToggle(page, 'Pseudonymise before cloud AI', true)
  await goTo(page, 'AI')
  await tryIt(page, 'Hello from Alex Example')
  await expect(page.locator('p.reply')).toHaveText('Echo: Hello from Alex Example')
  const afterPseudonym = await lastBody(page)
  expect(afterPseudonym).toContain('Adult A')
  expect(afterPseudonym).not.toContain('Alex Example')

  // 10. Local only blocks the cloud model before anything is sent.
  await goTo(page, 'Privacy')
  await setToggle(page, 'Local only', true)
  await goTo(page, 'AI')
  await tryIt(page, 'Hello again')
  await expect(page.getByRole('alert')).toContainText('Local only is on')
  expect(await lastBody(page)).toBe(afterPseudonym)
  await goTo(page, 'Privacy')
  await expect(page.getByRole('row').nth(1)).toContainText('blocked')

  // 11. Leave the settings tidy.
  await setToggle(page, 'Local only', false)
  await setToggle(page, 'Pseudonymise before cloud AI', false)
})
