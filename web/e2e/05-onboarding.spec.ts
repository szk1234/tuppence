import { expect, test, type Page } from '@playwright/test'
import { firstRunSetup, isoFromToday, signIn, ukDate } from './helpers'

// Runs only against the fresh server scripts/e2e.sh starts for @fresh specs (no admin, no household, no AI model).
test.describe.configure({ mode: 'serial' })

async function resetOnboarding(page: Page) {
  const session = await (await page.request.get('/api/auth/session')).json()
  const res = await page.request.post('/api/onboarding/reset', { headers: { 'X-CSRF-Token': session.csrf_token } })
  expect(res.ok()).toBeTruthy()
}

const step = (page: Page, n: number) => expect(page.getByText(`Step ${n} of 9`)).toBeVisible()
const heading = (page: Page, name: string) => expect(page.getByRole('heading', { name, level: 1 })).toBeVisible()
const next = (page: Page) => page.getByRole('button', { name: 'Continue' }).click()

test.describe('onboarding wizard @fresh', () => {
  test('family path from first run to a populated Home', async ({ page }) => {
    // First run: create the admin; a household that hasn't started onboarding lands on the wizard.
    await firstRunSetup(page)
    await heading(page, 'Welcome')
    await step(page, 1)
    await expect(page.getByText('Tuppence gives insights and guidance, not regulated financial advice.')).toBeVisible()
    await page.getByLabel('Nation').selectOption('wales')
    await page.getByLabel('Postcode district').fill('CF10')
    await next(page)

    // Household: the signed-in adult first (as "You", renamed), then a partner and a child.
    await heading(page, "Who's in your household")
    await expect(page.getByLabel('Your name')).toHaveValue('You')
    await page.getByLabel('Your name').fill('Alex Example')
    await page.getByRole('button', { name: 'Add me' }).click()
    await expect(page.getByText('Alex Example added.')).toBeVisible()
    await page.getByLabel('Family', { exact: true }).check()
    await page.getByLabel("Partner's name").fill('Sam Example')
    await page.getByRole('button', { name: 'Add partner' }).click()
    await expect(page.getByText('Sam Example added.')).toBeVisible()
    await page.getByLabel('Name', { exact: true }).fill('Kid A')
    await page.getByLabel('Role', { exact: true }).selectOption('child')
    await page.getByLabel('Birth year (children)').fill('2019')
    await page.getByRole('button', { name: 'Add dependant' }).click()
    await expect(page.getByText('Kid A added.')).toBeVisible()
    await next(page)

    // Work and income: Alex is employed; salary on the last working day, account chosen later.
    await heading(page, 'Work and income')
    await page.getByLabel('Work status for Alex Example').selectOption('employed')
    await expect(page.getByText('Alex Example: work status saved.')).toBeVisible()
    await page.getByLabel('Income band for Alex Example (optional)').selectOption('12570_50270')
    await expect(page.getByText('Alex Example: income band saved.')).toBeVisible()
    await page.getByLabel('Who receives this income?').selectOption({ label: 'Alex Example' })
    await page.getByLabel('Income name').fill('Acme Payroll')
    await page.getByLabel("Take-home amount each time you're paid").fill('2345.67')
    await page.getByLabel('How often are you paid?').selectOption({ label: 'Last working day of the month' })
    await page.getByRole('button', { name: 'Add income' }).click()
    await expect(page.getByText('Acme Payroll added.')).toBeVisible()
    await next(page)

    // Home: renting in Wales, so council tax bands run A to I.
    await heading(page, 'Your home')
    await page.getByLabel('Housing', { exact: true }).selectOption('renting')
    await page.getByLabel('Monthly housing cost').fill('950')
    await page.getByLabel('Bedrooms', { exact: true }).fill('2')
    await expect(page.getByLabel('Council tax band').getByRole('option', { name: 'Band I' })).toHaveCount(1)
    await page.getByLabel('Council tax band').selectOption('C')
    // No separate save: Continue saves what is typed.
    await next(page)

    // Accounts: a joint Monzo (which the salary is then paid into), and a Barclaycard with a 0% promotion.
    await heading(page, 'Accounts and cards')
    await page.getByLabel('Account type').selectOption('current')
    await page.getByLabel('Provider', { exact: true }).selectOption({ label: 'Monzo' })
    await page.getByLabel('Nickname', { exact: true }).fill('Joint Monzo')
    await page.getByLabel('Last 4 digits (optional)').fill('1234')
    await page.getByRole('checkbox', { name: 'Alex Example' }).check()
    await page.getByRole('checkbox', { name: 'Sam Example' }).check()
    await page.getByRole('button', { name: 'Add account' }).click()
    await expect(page.getByText('Joint Monzo added.')).toBeVisible()

    await page.getByLabel('Which account is Acme Payroll paid into?').selectOption({ label: 'Joint Monzo' })
    await expect(page.getByText('Acme Payroll will be paid into Joint Monzo.')).toBeVisible()
    await expect(page.getByLabel('Which account is Acme Payroll paid into?')).toHaveCount(0)

    await page.getByLabel('Account type').selectOption('credit_card')
    await page.getByLabel('Provider', { exact: true }).selectOption({ label: 'Barclaycard' })
    await page.getByLabel('Nickname', { exact: true }).fill('Barclaycard')
    await page.getByRole('checkbox', { name: 'Alex Example' }).check()
    await page.getByLabel('Credit limit').fill('2500')
    await page.getByLabel('Purchase APR (%)').fill('24.9')
    await page.getByLabel('Promotional APR (%)').fill('0')
    await page.getByLabel('Promotional rate ends').fill(isoFromToday(180))
    await next(page)

    // Debts: a PCP arranged through a broker, in the redress window.
    await heading(page, 'Loans and debts')
    await page.getByLabel('Type of debt').selectOption('car_finance_pcp')
    await page.getByLabel('Lender', { exact: true }).fill('Example Motor Finance')
    await page.getByLabel('Current balance').fill('8000')
    await page.getByLabel('Agreement start date').fill('2019-03-01')
    await page.getByLabel('Arranged through a broker or dealer').selectOption('yes')
    await page.getByRole('button', { name: 'Add debt' }).click()
    await expect(page.getByText('Example Motor Finance added.')).toBeVisible()
    await expect(page.getByText('may fall in the car finance redress window')).toBeVisible()
    await next(page)

    // Goals: accept the emergency fund suggestion, then a house deposit.
    await heading(page, "What you're saving for")
    await page.getByRole('button', { name: 'Add emergency fund goal' }).click()
    await expect(page.getByText('Emergency fund added.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Add emergency fund goal' })).toHaveCount(0)
    await page.getByLabel('Goal name').fill('House deposit')
    await page.getByLabel('Type of goal').selectOption('house_deposit')
    await page.getByLabel('Target amount (optional)').fill('20000')
    await page.getByLabel('Target date (optional)').fill(isoFromToday(2 * 365))
    await next(page)

    // AI: skipped (no model is chosen, so Home still asks for one).
    await heading(page, 'Choose your AI')
    await expect(page.getByLabel('Agent preset')).toHaveValue('balanced')
    await expect(page.getByLabel(/Research lookups \(recommended: on\)/)).toBeChecked()
    await page.getByRole('button', { name: 'Skip this step' }).click()

    await heading(page, 'Your first statements')
    await step(page, 9)
    await expect(page.getByText('Statement import arrives in the next update. You can skip this for now.')).toBeVisible()
    await page.getByRole('button', { name: 'Finish' }).click()

    // Home shows how complete the profile is, and still asks for an AI model.
    await expect(page.getByRole('heading', { name: 'Tuppence', level: 1 })).toBeVisible()
    const title = page.getByRole('heading', { name: /Your profile is \d+% complete/ })
    await expect(title).toBeVisible()
    const pct = Number(/(\d+)%/.exec((await title.textContent()) ?? '')![1])
    expect(pct).toBeGreaterThanOrEqual(70)
    await expect(page.getByRole('link', { name: 'Choose an AI model' })).toBeVisible()

    // What Continue saved (no explicit Add/Save press) is really stored.
    const accounts = await (await page.request.get('/api/accounts')).json()
    expect(accounts.accounts.map((a: { nickname: string }) => a.nickname).sort()).toEqual(['Barclaycard', 'Joint Monzo'])
    type StoredAccount = { id: string; nickname: string; credit_limit: string | null; purchase_apr: number | null; promo_apr: number | null; promo_end: string | null }
    const card = accounts.accounts.find((a: StoredAccount) => a.nickname === 'Barclaycard') as StoredAccount
    expect(card).toMatchObject({ credit_limit: '2500.00', purchase_apr: 24.9, promo_apr: 0, promo_end: isoFromToday(180) })
    const monzo = accounts.accounts.find((a: StoredAccount) => a.nickname === 'Joint Monzo') as StoredAccount
    const income = await (await page.request.get('/api/income')).json()
    expect(income.income).toEqual([expect.objectContaining({ name: 'Acme Payroll', account_id: monzo.id, needs_account: false })])
    const goals = await (await page.request.get('/api/goals')).json()
    expect(goals.goals.map((g: { name: string }) => g.name).sort()).toEqual(['Emergency fund', 'House deposit'])
    const hh = await (await page.request.get('/api/household/timeline?subject_type=household&subject_id=1')).json()
    expect(hh.entries.map((e: { attribute: string }) => e.attribute)).toContain('council_tax_band')
  })

  test('resume: a new wizard reopens at the next step, household intact', async ({ page }) => {
    await signIn(page)
    await resetOnboarding(page)
    await page.goto('/welcome')
    await heading(page, 'Welcome')
    await next(page)
    await heading(page, "Who's in your household")
    await expect(page.getByText('Alex Example')).toBeVisible()
    await next(page)
    await heading(page, 'Work and income')

    await page.reload()
    await heading(page, 'Work and income')
    await step(page, 3)
    await page.getByRole('button', { name: 'Back' }).click()
    await heading(page, "Who's in your household")
    await expect(page.getByText('Alex Example')).toBeVisible()
    await expect(page.getByText('Sam Example')).toBeVisible()
    await expect(page.getByText('Kid A')).toBeVisible()
    await expect(page.getByLabel('Your name')).toHaveCount(0)
  })

  test('timeline: a later work-status change is listed after "employed"', async ({ page }) => {
    await signIn(page)
    const from = isoFromToday(90)
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Timeline', exact: true }).click()
    await heading(page, 'Timeline')
    await page.getByLabel('Who is this about?').selectOption({ label: 'Alex Example' })
    await page.getByLabel('What changed?').selectOption({ label: 'Work status' })
    await page.getByLabel('New value').selectOption({ label: 'Not working' })
    await page.getByLabel('From', { exact: true }).fill(from)
    await page.getByRole('button', { name: 'Add change' }).click()
    await expect(page.getByText(`Work status updated from ${ukDate(from)}.`)).toBeVisible()

    const card = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Alex Example', level: 2 }) })
    const rows = card.getByRole('listitem').filter({ hasText: 'Work status' })
    await expect(rows).toHaveCount(2)
    await expect(rows.nth(0)).toContainText('Employed')
    await expect(rows.nth(0)).toContainText(`until ${ukDate(from)}`)
    await expect(rows.nth(1)).toContainText('Not working')
    await expect(rows.nth(1)).toContainText(`from ${ukDate(from)}`)
  })
})
