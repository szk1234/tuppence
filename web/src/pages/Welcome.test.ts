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
function api(next: string | null, extra: (url: string, method: string, body: any) => Response | undefined = () => undefined, opts: { nation?: string | null; people?: unknown[] } = {}) {
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
    if (url === '/api/settings') return json({ settings: [{ key: 'privacy.research_lookups', value: false, version: 3, description: '' }] })
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
  expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ value: true, expected_version: 3 })
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
