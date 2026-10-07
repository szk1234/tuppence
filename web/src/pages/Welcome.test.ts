import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import { json, stubApi, type Call } from '../components/forms/helpers'
import { router } from '../lib/router.svelte'
import { session } from '../lib/session.svelte'
import Welcome from './Welcome.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  session.mode = 'local'; session.user = null
})

const TITLES: Record<string, string> = {
  welcome: 'Welcome', household: "Who's in your household", work_income: 'Work and income', home: 'Your home', accounts: 'Accounts and cards',
  debts: 'Loans and debts', goals: "What you're saving for", ai: 'Choose your AI', first_upload: 'Your first statements',
}
function state(next: string | null, statuses: Record<string, string> = {}) {
  const steps = Object.entries(TITLES).map(([id, title]) => ({ id, title, status: statuses[id] ?? 'todo' }))
  return { steps, next_step: next, started: Object.keys(statuses).length > 0, finished: next === null, completeness: 10, prompts: [] }
}
const alex = { id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 }
const household = (nation: string | null = null) => ({ nation, postcode_district: null, version: 1 })

/** Routes shared by every step; `extra` wins. */
function api(next: string | null, extra: (url: string, method: string, body: any) => Response | undefined = () => undefined, opts: { nation?: string | null; people?: unknown[]; researchVersion?: number } = {}) {
  let current = state(next)
  const calls = stubApi((url, method, body) => {
    const custom = extra(url, method, body)
    if (custom) return custom
    if (url === '/api/onboarding') return json(current)
    const m = /^\/api\/onboarding\/steps\/(\w+)$/.exec(url)
    if (m && method === 'POST') {
      current = state(null, { [m[1]]: body.status })
      const ids = Object.keys(TITLES)
      current.next_step = ids[ids.indexOf(m[1]) + 1] ?? null
      return json(current)
    }
    if (url === '/api/household' && method === 'GET') return json(household(opts.nation ?? null))
    if (url === '/api/household/people') return json({ people: opts.people ?? [alex] })
    if (url.startsWith('/api/household/timeline')) return json({ entries: [] })
    if (url === '/api/accounts') return json({ accounts: [] })
    if (url === '/api/income') return json({ income: [] })
    if (url === '/api/debts') return json({ debts: [] })
    if (url === '/api/goals') return json({ goals: [] })
    if (url === '/api/goals/suggestions') return json({ emergency_fund: true })
    if (url === '/api/llm/presets') return json({ presets: [] })
    if (url === '/api/llm/connections') return json({ connections: [] })
    if (url === '/api/llm/models') return json({ models: [] })
    if (url === '/api/llm/routing') return json({ mode: 'simple', mode_version: 0, simple_model: null, simple_version: 0, tasks: {} })
    if (url === '/api/config/presets') return json({ presets: ['frugal', 'balanced', 'thorough'] })
    if (url === '/api/settings') return json({ settings: [{ key: 'privacy.research_lookups', value: false, version: opts.researchVersion ?? 0, description: '' }, { key: 'config.preset', value: 'balanced', version: 2, description: '' }] })
  })
  return calls
}
const posted = (calls: Call[], step: string) => calls.find((c) => c.url === `/api/onboarding/steps/${step}`)

it('resumes at the step the server says is next', async () => {
  api('accounts')
  render(Welcome)
  expect(await screen.findByRole('heading', { level: 1, name: 'Accounts and cards' })).toBeInTheDocument()
  expect(screen.getByText('Step 5 of 9')).toBeInTheDocument()
})

it('opens the first step when the wizard is already finished', async () => {
  api(null)
  render(Welcome)
  expect(await screen.findByRole('heading', { level: 1, name: 'Welcome' })).toBeInTheDocument()
  expect(screen.getByText('Step 1 of 9')).toBeInTheDocument()
})

it('Skip records the step as skipped and shows the next one', async () => {
  const calls = api('home')
  render(Welcome)
  await screen.findByRole('heading', { level: 1, name: 'Your home' })
  await fireEvent.click(screen.getByRole('button', { name: 'Skip this step' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Accounts and cards' })).toBeInTheDocument()
  expect(posted(calls, 'home')?.body).toEqual({ status: 'skipped' })
})

it('Continue saves the welcome step through the household API, then records it done', async () => {
  const calls = api('welcome', (url, method) => {
    if (url === '/api/household' && method === 'PATCH') return json({ ...household('wales'), postcode_district: 'CF10', version: 2 })
  })
  render(Welcome)
  expect(await screen.findByText(/not regulated financial advice\. For debt help, MoneyHelper, StepChange and Citizens Advice are free\./)).toBeInTheDocument()
  await waitFor(() => expect(screen.getByLabelText('Nation')).toBeEnabled())
  await fireEvent.change(screen.getByLabelText('Nation'), { target: { value: 'wales' } })
  await fireEvent.input(screen.getByLabelText('Postcode district'), { target: { value: 'CF10' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('heading', { level: 1, name: "Who's in your household" })).toBeInTheDocument()
  const patch = calls.find((c) => c.method === 'PATCH')!
  expect(patch.body).toEqual({ changes: { nation: 'wales', postcode_district: 'CF10' }, expected_version: 1 })
  expect(posted(calls, 'welcome')?.body).toEqual({ status: 'done' })
})

it('stays on the welcome step and shows the API message when the postcode is refused', async () => {
  const calls = api('welcome', (url, method) => {
    if (url === '/api/household' && method === 'PATCH') return json({ detail: 'Enter just the first part of your postcode, like LS6.' }, 422)
  })
  render(Welcome)
  await waitFor(() => expect(screen.getByLabelText('Postcode district')).toBeEnabled())
  await fireEvent.input(screen.getByLabelText('Postcode district'), { target: { value: 'LS6 2AB' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByText(/first part of your postcode/)).toBeInTheDocument()
  expect(posted(calls, 'welcome')).toBeUndefined()
  expect(screen.getByRole('heading', { level: 1, name: 'Welcome' })).toBeInTheDocument()
})

it('Back returns to the previous step without recording anything', async () => {
  const calls = api('household')
  render(Welcome)
  await screen.findByRole('heading', { level: 1, name: "Who's in your household" })
  await screen.findByText('Alex Example')
  await fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Welcome' })).toBeInTheDocument()
  expect(calls.some((c) => c.method === 'POST')).toBe(false)
})

it('Finish on the last step records it and goes Home', async () => {
  const calls = api('first_upload')
  router.path = '/welcome'
  render(Welcome)
  expect(await screen.findByText('Statement import arrives in the next update. You can skip this for now.')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Finish' }))
  await waitFor(() => expect(router.path).toBe('/'))
  expect(posted(calls, 'first_upload')?.body).toEqual({ status: 'done' })
})

it('hides council tax for Northern Ireland and explains why', async () => {
  api('home', undefined, { nation: 'northern_ireland' })
  render(Welcome)
  expect(await screen.findByText(/Northern Ireland uses domestic rates/)).toBeInTheDocument()
  expect(screen.queryByLabelText('Council tax band')).toBeNull()
})

it.each([['scotland', 8], ['england', 8], ['wales', 9]])('offers the right council tax bands in %s', async (nation, count) => {
  api('home', undefined, { nation })
  render(Welcome)
  const select = await screen.findByLabelText('Council tax band')
  await waitFor(() => expect(select.querySelectorAll('option')).toHaveLength(count + 1))
  const labels = Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
  expect(labels.includes('Band I')).toBe(nation === 'wales')
})

it('shows existing adults on the household step and does not offer a second "You"', async () => {
  api('household')
  render(Welcome)
  expect(await screen.findByText('Alex Example')).toBeInTheDocument()
  expect(screen.queryByLabelText('Your name')).toBeNull()
})

it('adds the signed-in adult as "You" (editable) when the household is empty, before a partner', async () => {
  const calls = api('household', (url, method, body) => {
    if (url === '/api/household/people' && method === 'POST') return json({ ...alex, id: 'p_9', display_name: body.display_name })
  }, { people: [] })
  render(Welcome)
  const name = await screen.findByLabelText('Your name')
  expect(name).toHaveValue('You')
  await fireEvent.input(name, { target: { value: 'Alex Example' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add me' }))
  expect(await screen.findByText('Alex Example added.')).toBeInTheDocument()
  expect(calls.find((c) => c.method === 'POST' && c.url === '/api/household/people')?.body).toEqual({ display_name: 'Alex Example', role: 'adult' })
  await fireEvent.click(screen.getByLabelText('Family'))
  expect(screen.getByLabelText("Partner's name")).toBeInTheDocument()
})

it('Continue on an empty household adds the signed-in adult as "You" first', async () => {
  const calls = api('household', (url, method, body) => {
    if (url === '/api/household/people' && method === 'POST') return json({ ...alex, display_name: body.display_name })
  }, { people: [] })
  render(Welcome)
  await screen.findByLabelText('Your name')
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Work and income' })).toBeInTheDocument()
  expect(calls.find((c) => c.method === 'POST' && c.url === '/api/household/people')?.body).toEqual({ display_name: 'You', role: 'adult' })
})

it('rejects a child birth year that is not 4 digits', async () => {
  api('household')
  render(Welcome)
  await screen.findByText('Alex Example')
  await fireEvent.input(screen.getByLabelText('Name'), { target: { value: 'Kid A' } })
  await fireEvent.input(screen.getByLabelText('Birth year (children)'), { target: { value: '19' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add dependant' }))
  expect(await screen.findByText('Enter a 4-digit year')).toBeInTheDocument()
})

it('asks "Which account is <income> paid into?" for income without an account, then links it', async () => {
  const income = { id: 'i_1', person_id: 'p_1', kind: 'salary', name: 'Acme Payroll', net_amount: '2345.67', account_id: null, pay_rule: {}, pay_rule_description: 'Last working day', variable_components: [], next_pay_date: null, person_left: false, calendar_assumed: false, status: 'active', version: 4 }
  const account = { id: 'a_1', provider: 'monzo', provider_name: 'Monzo', kind: 'current', nickname: 'Joint Monzo', last4: '1234', owner_ids: ['p_1'], credit_limit: null, purchase_apr: null, promo_apr: null, promo_end: null, statement_day: null, status: 'active', version: 1, joint: false }
  let linked = false
  const calls = api('accounts', (url, method) => {
    if (url === '/api/accounts') return json({ accounts: [account] })
    if (url === '/api/income' && method === 'GET') return json({ income: [linked ? { ...income, account_id: 'a_1', version: 5 } : income] })
    if (url === '/api/income/i_1' && method === 'PATCH') { linked = true; return json({ ...income, account_id: 'a_1', version: 5 }) }
  })
  render(Welcome)
  const select = await screen.findByLabelText('Which account is Acme Payroll paid into?')
  await waitFor(() => expect(select.querySelectorAll('option')).toHaveLength(2))
  await fireEvent.change(select, { target: { value: 'a_1' } })
  expect(await screen.findByText('Acme Payroll will be paid into Joint Monzo.')).toBeInTheDocument()
  expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ changes: { account_id: 'a_1' }, expected_version: 4 })
  await waitFor(() => expect(screen.queryByLabelText('Which account is Acme Payroll paid into?')).toBeNull())
})

it('asks again for an income whose account has closed', async () => {
  const income = { id: 'i_1', person_id: 'p_1', kind: 'salary', name: 'Acme Payroll', net_amount: '1.00', account_id: 'a_old', needs_account: true, pay_rule: {}, pay_rule_description: '', variable_components: [], next_pay_date: null, person_left: false, calendar_assumed: false, status: 'active', version: 2 }
  const account = { id: 'a_1', provider: 'monzo', provider_name: 'Monzo', kind: 'current', nickname: 'New Monzo', last4: null, owner_ids: ['p_1'], credit_limit: null, purchase_apr: null, promo_apr: null, promo_end: null, statement_day: null, status: 'active', version: 1, joint: false }
  api('accounts', (url, method) => {
    if (url === '/api/accounts') return json({ accounts: [account] })
    if (url === '/api/income' && method === 'GET') return json({ income: [income] })
  })
  render(Welcome)
  expect(await screen.findByLabelText('Which account is Acme Payroll paid into?')).toBeInTheDocument()
})

it('does not ask where pay lands when there are no accounts yet', async () => {
  const income = { id: 'i_1', person_id: 'p_1', kind: 'salary', name: 'Acme Payroll', net_amount: '1.00', account_id: null, pay_rule: {}, pay_rule_description: '', variable_components: [], next_pay_date: null, person_left: false, calendar_assumed: false, status: 'active', version: 1 }
  api('accounts', (url, method) => (url === '/api/income' && method === 'GET' ? json({ income: [income] }) : undefined))
  render(Welcome)
  await screen.findByRole('heading', { name: 'Your accounts' })
  expect(screen.queryByLabelText(/paid into\?/)).toBeNull()
})

it('AI step: research lookups is recommended and pre-selected, and Continue saves it', async () => {
  const calls = api('ai', (url, method) => {
    if (url === '/api/settings/privacy.research_lookups' && method === 'PATCH') return json({ key: 'privacy.research_lookups', value: true, version: 4, description: '' })
  })
  render(Welcome)
  const box = await screen.findByLabelText(/Research lookups \(recommended: on\)/)
  expect(box).toBeChecked()
  expect(screen.getByText(/Only merchant names are looked up — never amounts or your details\./)).toBeInTheDocument()
  expect(screen.getByText(/run on your own computer or network/)).toBeInTheDocument()
  await waitFor(() => expect(box).toBeEnabled())
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Your first statements' })).toBeInTheDocument()
  expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ value: true, expected_version: 0 })
})

it('AI step: a non-admin in server mode sees the admin message and no connection controls', async () => {
  session.mode = 'server'; session.user = { username: 'sam', is_admin: false }
  const calls = api('ai')
  render(Welcome)
  expect(await screen.findByText(/Only the household admin can change AI connections/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Find local AI' })).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Your first statements' })).toBeInTheDocument()
  expect(calls.some((c) => c.method === 'PATCH')).toBe(false)
})

it('AI step: a cloud connection offers the notice and acknowledges it', async () => {
  const conn = { id: 'c1', preset: 'openai', name: 'OpenAI', api_style: 'openai', base_url: 'https://api.openai.com/v1', is_local: false, has_key: true, headers: [], needs_notice: true, notice_acknowledged_at: null, enabled: true, version: 3 }
  const calls = api('ai', (url, method) => {
    if (url === '/api/llm/connections' && method === 'GET') return json({ connections: [conn] })
    if (url === '/api/llm/connections/c1/acknowledge-notice') return json({ ...conn, needs_notice: false, version: 4 })
  })
  render(Welcome)
  await fireEvent.click(await screen.findByRole('button', { name: "Review what's sent" }))
  expect(await screen.findByText('OpenAI will see the statement text Tuppence sends to it.')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'I understand' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: "Review what's sent" })).toBeNull())
  expect(calls.find((c) => c.url.endsWith('acknowledge-notice'))?.body).toEqual({ expected_version: 3 })
})

it('AI step: a research opt-out made earlier is kept (not pre-selected, not written)', async () => {
  const calls = api('ai', undefined, { researchVersion: 5 })
  render(Welcome)
  const box = await screen.findByLabelText(/Research lookups \(recommended: on\)/)
  await waitFor(() => expect(box).toBeEnabled())
  expect(box).not.toBeChecked()
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Your first statements' })).toBeInTheDocument()
  expect(calls.some((c) => c.method === 'PATCH')).toBe(false)
})

it('AI step: unticking research on the default writes the opt-out explicitly', async () => {
  const calls = api('ai', (url, method) => (url === '/api/settings/privacy.research_lookups' && method === 'PATCH' ? json({ key: 'privacy.research_lookups', value: false, version: 1, description: '' }) : undefined))
  render(Welcome)
  const box = await screen.findByLabelText(/Research lookups \(recommended: on\)/)
  await waitFor(() => expect(box).toBeEnabled())
  await fireEvent.click(box)
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  await screen.findByRole('heading', { level: 1, name: 'Your first statements' })
  expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ value: false, expected_version: 0 })
})

it('AI step: shows the agent preset (current pre-selected) and saves a change on Continue', async () => {
  const calls = api('ai', (url, method) => (url === '/api/settings/config.preset' && method === 'PATCH' ? json({ key: 'config.preset', value: 'frugal', version: 3, description: '' }) : undefined), { researchVersion: 1 })
  render(Welcome)
  const select = await screen.findByLabelText('Agent preset')
  await waitFor(() => expect(select.querySelectorAll('option')).toHaveLength(3))
  expect(select).toHaveValue('balanced')
  expect(screen.getAllByText(/Fewest AI calls/).length).toBeGreaterThan(0)
  await fireEvent.change(select, { target: { value: 'frugal' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  await screen.findByRole('heading', { level: 1, name: 'Your first statements' })
  expect(calls.find((c) => c.url === '/api/settings/config.preset')?.body).toEqual({ value: 'frugal', expected_version: 2 })
})

it('Continue on Home saves the typed details to the timeline before recording the step', async () => {
  const order: string[] = []
  const calls = api('home', (url, method, body) => {
    if (url === '/api/household/timeline' && method === 'POST') { order.push(`timeline:${body.attribute}`); return json({ id: 1 }) }
    if (/^\/api\/onboarding\/steps\/home$/.test(url)) order.push('step:home')
    return undefined
  }, { nation: 'england' })
  render(Welcome)
  await screen.findByLabelText('Council tax band')
  await waitFor(() => expect(screen.getByLabelText('Housing')).toBeEnabled())
  await fireEvent.change(screen.getByLabelText('Housing'), { target: { value: 'renting' } })
  await fireEvent.input(screen.getByLabelText('Monthly housing cost'), { target: { value: '950' } })
  await fireEvent.change(screen.getByLabelText('Council tax band'), { target: { value: 'C' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Accounts and cards' })).toBeInTheDocument()
  expect(order).toEqual(['timeline:housing_tenure', 'timeline:housing_monthly_pence', 'timeline:council_tax_band', 'step:home'])
  expect(calls.find((c) => c.url === '/api/household/timeline' && c.body.attribute === 'housing_monthly_pence')?.body.value).toBe('950.00')
})

it('an invalid amount on Home blocks Continue and the step is not recorded', async () => {
  const calls = api('home')
  render(Welcome)
  await screen.findByLabelText('Council tax band')
  await waitFor(() => expect(screen.getByLabelText('Housing')).toBeEnabled())
  await fireEvent.input(screen.getByLabelText('Monthly housing cost'), { target: { value: 'lots' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect((await screen.findAllByText(/Enter (an amount|the monthly cost)/)).length).toBeGreaterThan(0)
  expect(calls.some((c) => c.url === '/api/onboarding/steps/home')).toBe(false)
  expect(screen.getByRole('heading', { level: 1, name: 'Your home' })).toBeInTheDocument()
})

it('Continue on Debts saves a filled form first; an invalid one blocks; an untouched one just continues', async () => {
  const calls = api('debts', (url, method) => (url === '/api/debts' && method === 'POST' ? json({ id: 'd_1', kind: 'personal_loan', lender: 'Bank', balance: '500.00', balance_date: '2026-10-07', apr: null, monthly_payment: null, end_date: null, student_loan_plan: null, details: {}, car_finance_redress_window: false, status: 'active', version: 1 }, 201) : undefined))
  render(Welcome)
  await screen.findByLabelText('Lender')
  await fireEvent.input(screen.getByLabelText('Lender'), { target: { value: 'Bank' } })
  await fireEvent.input(screen.getByLabelText('Current balance'), { target: { value: 'abc' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByText(/Enter the current balance/)).toBeInTheDocument()
  expect(calls.some((c) => c.url === '/api/onboarding/steps/debts')).toBe(false)
  await fireEvent.input(screen.getByLabelText('Current balance'), { target: { value: '500' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('heading', { level: 1, name: "What you're saving for" })).toBeInTheDocument()
  expect(calls.find((c) => c.url === '/api/debts' && c.method === 'POST')?.body).toMatchObject({ lender: 'Bank', balance: '500.00' })
  await screen.findByLabelText('Goal name')
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Choose your AI' })).toBeInTheDocument()
  expect(calls.filter((c) => c.method === 'POST' && c.url === '/api/goals')).toHaveLength(0)
})

it('Back saves typed household input instead of losing it', async () => {
  const calls = api('household', (url, method, body) => (url === '/api/household/people' && method === 'POST' ? json({ ...alex, id: 'p_2', display_name: body.display_name }) : undefined))
  render(Welcome)
  await screen.findByText('Alex Example')
  await fireEvent.click(screen.getByLabelText('Family'))
  await fireEvent.input(screen.getByLabelText("Partner's name"), { target: { value: 'Sam Example' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Welcome' })).toBeInTheDocument()
  expect(calls.find((c) => c.method === 'POST' && c.url === '/api/household/people')?.body).toEqual({ display_name: 'Sam Example', role: 'adult' })
})

it('AI step: Back writes no research consent (spec §12.5 opt-in), even with the box pre-selected', async () => {
  const calls = api('ai')
  render(Welcome)
  const box = await screen.findByLabelText(/Research lookups \(recommended: on\)/)
  await waitFor(() => expect(box).toBeEnabled())
  expect(box).toBeChecked()
  await fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  expect(await screen.findByRole('heading', { level: 1, name: "What you're saving for" })).toBeInTheDocument()
  expect(calls.some((c) => c.url.startsWith('/api/settings/') && c.method === 'PATCH')).toBe(false)
})

it('AI step: an explicit untick is recorded at once, so Back keeps the opt-out', async () => {
  const calls = api('ai', (url, method) => (url === '/api/settings/privacy.research_lookups' && method === 'PATCH' ? json({ key: 'privacy.research_lookups', value: false, version: 1, description: '' }) : undefined))
  render(Welcome)
  const box = await screen.findByLabelText(/Research lookups \(recommended: on\)/)
  await waitFor(() => expect(box).toBeEnabled())
  await fireEvent.click(box)
  await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(1))
  await fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  await screen.findByRole('heading', { level: 1, name: "What you're saving for" })
  expect(calls.filter((c) => c.method === 'PATCH').map((c) => c.body)).toEqual([{ value: false, expected_version: 0 }])
})

it('Back on an empty household step does not create "You"', async () => {
  const calls = api('household', undefined, { people: [] })
  render(Welcome)
  await screen.findByLabelText('Your name')
  await fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Welcome' })).toBeInTheDocument()
  expect(calls.some((c) => c.method === 'POST')).toBe(false)
})

it('the welcome step reloads the household after a conflict, so Continue can succeed', async () => {
  let version = 1
  let patches = 0
  const calls = api('welcome', (url, method, body) => {
    if (url === '/api/household' && method === 'GET') return json({ ...household(), version })
    if (url === '/api/household' && method === 'PATCH') {
      patches += 1
      if (body.expected_version !== version) return json({ detail: 'This was changed somewhere else. Reload and try again.', current_version: version }, 409)
      return json({ ...household('wales'), version: version + 1 })
    }
  })
  render(Welcome)
  await waitFor(() => expect(screen.getByLabelText('Nation')).toBeEnabled())
  version = 2 // changed in another tab after the page loaded
  await fireEvent.change(screen.getByLabelText('Nation'), { target: { value: 'wales' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByText('This was changed somewhere else. Reload and try again.')).toBeInTheDocument()
  await waitFor(() => expect(calls.filter((c) => c.url === '/api/household' && c.method === 'GET')).toHaveLength(2))
  expect(screen.getByLabelText('Nation')).toHaveValue('wales')
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('heading', { level: 1, name: "Who's in your household" })).toBeInTheDocument()
  expect(patches).toBe(2)
  expect(calls.filter((c) => c.method === 'PATCH').map((c) => c.body.expected_version)).toEqual([1, 2])
})

it('Home: a failure part-way reloads what is stored, so the retry sends no stale value', async () => {
  const entries: Array<Record<string, unknown>> = []
  let failOnce = true
  const calls = api('home', (url, method, body) => {
    if (url.startsWith('/api/household/timeline') && method === 'GET') return json({ entries })
    if (url === '/api/household/timeline' && method === 'POST') {
      if (body.attribute === 'housing_monthly_pence' && failOnce) { failOnce = false; return json({ detail: 'Something broke.' }, 500) }
      const stored = entries.find((e) => e.attribute === body.attribute)?.value ?? null
      if (body.expected_current !== stored) return json({ detail: 'This was changed somewhere else. Reload and try again.', current_version: 1 }, 409)
      entries.push({ id: entries.length + 1, subject_type: 'household', subject_id: '1', attribute: body.attribute, value: body.value, valid_from: body.valid_from, valid_to: null, source: 'user', version: 1 })
      return json(entries[entries.length - 1], 201)
    }
  }, { nation: 'england' })
  render(Welcome)
  await screen.findByLabelText('Council tax band')
  await waitFor(() => expect(screen.getByLabelText('Housing')).toBeEnabled())
  await fireEvent.change(screen.getByLabelText('Housing'), { target: { value: 'renting' } })
  await fireEvent.input(screen.getByLabelText('Monthly housing cost'), { target: { value: '950' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByText('Something broke.')).toBeInTheDocument()
  expect(screen.getByLabelText('Monthly housing cost')).toHaveValue('950') // what is typed stays
  await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
  await fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Accounts and cards' })).toBeInTheDocument()
  const posts = calls.filter((c) => c.url === '/api/household/timeline' && c.method === 'POST').map((c) => c.body.attribute)
  expect(posts).toEqual(['housing_tenure', 'housing_monthly_pence', 'housing_monthly_pence'])
})
