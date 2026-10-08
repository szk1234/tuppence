import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Spending from './Spending.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const period = { start: '2026-10-01', end: '2026-10-31', label: 'October 2026', mode: 'calendar_month',
  previous: '2026-09-01', next: '2026-11-01', has_later_data: false }
const top = { period, path: [{ id: null, label: 'All spending' }], total: '300.00', direct: '0.00',
  money_in: '2450.00', saved: '0.00', moved: '50.00', waiting_for_ai: 0,
  tiles: [{ id: 'food', label: 'Food & drink', amount: '200.00', count: 9, has_children: true },
          { id: 'other', label: 'Other spending', amount: '100.00', count: 3, has_children: false }] }
const other = { ...top, path: [...top.path, { id: 'other', label: 'Other spending' }], total: '100.00',
  direct: '100.00', tiles: [] }
const bakery = { id: 't_1', date: '2026-10-12', amount: '-4.50', description: 'SUNRISE BAKERY REF 0042',
  merchant: 'Sunrise Bakery', account_id: 'a_1', category_id: 'other', category_label: 'Other spending',
  who: 'household', status: 'guessed', decided_by: 'review', confidence: 0.5, version: 3 }
const categories = [
  { id: 'food', parent_id: null, level: 1, label: 'Food & drink', kind: 'spend', essential: false, source: 'seed', retired: false, version: 1 },
  { id: 'food.eating-out', parent_id: 'food', level: 2, label: 'Eating out', kind: 'spend', essential: false, source: 'seed', retired: false, version: 1 },
  { id: 'other', parent_id: null, level: 1, label: 'Other spending', kind: 'spend', essential: false, source: 'seed', retired: false, version: 1 },
]

function stub() {
  const calls: { url: string; method: string; body: any }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null })
    if (url === '/api/categories') return json({ categories })
    if (url === '/api/accounts') return json({ accounts: [{ id: 'a_1', nickname: 'Joint', provider_name: 'Starling' }] })
    if (url === '/api/household/people') return json({ people: [{ id: 'p_1', display_name: 'Alex Example' }] })
    if (url === '/api/analysis') return json({ running: false, queued: false, waiting: {}, last_run: { status: 'done', summary: 'Sorted 84 transactions.', finished_at: 'x', started_at: 'x' } })
    if (url === '/api/categories/refiles') return json({ refiles: [] })
    if (url.startsWith('/api/spending/transactions')) return json({ transactions: [bakery] })
    if (url.startsWith('/api/spending')) return json(url.includes('category=other') ? other : top)
    if (url === '/api/transactions/t_1/understanding') return json({
      understanding: { version: 4, category_id: 'food.eating-out' },
      rule_offer: { merchant_id: 'm_1', merchant_name: 'Sunrise Bakery', category_id: 'food.eating-out', matches: 3, will_change: 2, kept_yours: 1 } })
    if (url === '/api/rules') return json({ rule: { id: 'r_1' }, changed: 2 }, 201)
    if (url === '/api/transactions/t_1/why') return json({ transaction_id: 't_1', status: 'guessed', status_label: 'Best guess',
      decided_by: 'review', decided_by_label: 'The AI, on a second look', confidence: 0.5, category_path: ['Other spending'],
      steps: ['The AI took a second look (50% sure): no match.'], rule: null, merchant: null, knowledge_version: 3,
      current_knowledge_version: 3, stale: false, history: [], version: 3 })
    return json({ detail: `unexpected ${url}` }, 500)
  }))
  return calls
}

it('drills from the treemap to a transaction, recategorises it and makes a rule', async () => {
  const calls = stub()
  render(Spending)
  expect(await screen.findByRole('heading', { name: 'October 2026' })).toBeInTheDocument()
  const map = screen.getByRole('group', { name: 'Spending by category' })
  await fireEvent.click(within(map).getByRole('button', { name: /^Other spending, £100\.00, 33%/ }))
  const crumbs = await screen.findByRole('navigation', { name: 'Breadcrumb' })
  await vi.waitFor(() => expect(within(crumbs).getByText('Other spending')).toHaveAttribute('aria-current', 'page'))
  const select = await screen.findByLabelText('Category for Sunrise Bakery on 12/10/2026')
  expect(screen.getByText('Best guess')).toBeInTheDocument()
  await vi.waitFor(() => expect(select).toBeEnabled())
  await fireEvent.change(select, { target: { value: 'food.eating-out' } })
  expect(await screen.findByText('2 other payments to Sunrise Bakery would change too.')).toBeInTheDocument()
  expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ category_id: 'food.eating-out', expected_version: 3 })
  const applyAll = screen.getByRole('button', { name: 'Apply to all from Sunrise Bakery' })
  await vi.waitFor(() => expect(applyAll).toBeEnabled())
  await fireEvent.click(applyAll)
  expect(await screen.findByText('Rule saved: 2 more payments now follow it.')).toBeInTheDocument()
  expect(calls.find((c) => c.url === '/api/rules')!.body).toEqual({
    merchant_id: 'm_1', set_category_id: 'food.eating-out', created_from_transaction_id: 't_1', apply_to_past: true })
  await fireEvent.click(within(crumbs).getByRole('button', { name: 'All spending' }))
  expect(await screen.findByRole('group', { name: 'Spending by category' })).toBeInTheDocument()
})

it('explains a decision in the Why panel', async () => {
  stub()
  render(Spending)
  await fireEvent.click(await screen.findByRole('button', { name: /^Other spending, £100\.00/ }))
  await fireEvent.click(await screen.findByRole('button', { name: 'Why? Sunrise Bakery on 12/10/2026' }))
  expect(await screen.findByText('The AI took a second look (50% sure): no match.')).toBeInTheDocument()
  expect(screen.getByText(/decided by The AI, on a second look/)).toBeInTheDocument()
})

it('locks the category select and "Apply to all" while the change is being saved', async () => {
  const calls = stub()
  const base = globalThis.fetch as unknown as (url: string, init?: RequestInit) => Promise<Response>
  let releasePatch: (r: Response) => void = () => {}
  let releaseRule: (r: Response) => void = () => {}
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PATCH') return new Promise<Response>((res) => { releasePatch = res })
    if (url === '/api/rules' && init?.method === 'POST') return new Promise<Response>((res) => { releaseRule = res })
    return base(url, init)
  }))
  render(Spending)
  await fireEvent.click(await screen.findByRole('button', { name: /^Other spending, £100\.00/ }))
  const select = await screen.findByLabelText('Category for Sunrise Bakery on 12/10/2026')
  await vi.waitFor(() => expect(select).toBeEnabled())
  await fireEvent.change(select, { target: { value: 'food.eating-out' } })
  await vi.waitFor(() => expect(select).toBeDisabled())
  expect(screen.getByLabelText('Account')).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Run analysis now' })).toBeDisabled()
  releasePatch(json({ understanding: { version: 4, category_id: 'food.eating-out' },
    rule_offer: { merchant_id: 'm_1', merchant_name: 'Sunrise Bakery', category_id: 'food.eating-out', matches: 3, will_change: 2, kept_yours: 1 } }))
  const apply = await screen.findByRole('button', { name: 'Apply to all from Sunrise Bakery' })
  await vi.waitFor(() => expect(apply).toBeEnabled())
  await fireEvent.click(apply)
  await vi.waitFor(() => expect(apply).toBeDisabled())
  expect(select).toBeDisabled()
  releaseRule(json({ rule: { id: 'r_1' }, changed: 2 }, 201))
  await vi.waitFor(() => expect(select).toBeEnabled())
  expect(calls.length).toBeGreaterThan(0)
})

it('encodes ids in the paths it builds', async () => {
  const seen: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string) => { seen.push(url); return json({}) }))
  const u = await import('../lib/understanding')
  await u.getWhy('a/b?c')
  await u.disableRule('r/1', 1)
  await u.dismissCommitment('c#1', 1)
  expect(seen).toEqual(['/api/transactions/a%2Fb%3Fc/why', '/api/rules/r%2F1/disable', '/api/commitments/c%231/dismiss'])
})

it('after a failed save it shows the message, reloads and the select shows the saved category', async () => {
  stub()
  const base = globalThis.fetch as unknown as (url: string, init?: RequestInit) => Promise<Response>
  let lists = 0
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PATCH') return json({ detail: 'This changed since you opened it.' }, 409)
    if (url.startsWith('/api/spending/transactions')) lists += 1
    return base(url, init)
  }))
  render(Spending)
  await fireEvent.click(await screen.findByRole('button', { name: /^Other spending, £100\.00/ }))
  const select = (await screen.findByLabelText('Category for Sunrise Bakery on 12/10/2026')) as HTMLSelectElement
  await vi.waitFor(() => expect(select).toBeEnabled())
  const before = lists
  await fireEvent.change(select, { target: { value: 'food.eating-out' } })
  expect(await screen.findByText('This changed since you opened it.')).toBeInTheDocument()
  await vi.waitFor(() => expect(lists).toBeGreaterThan(before))
  const again = (await screen.findByLabelText('Category for Sunrise Bakery on 12/10/2026')) as HTMLSelectElement
  await vi.waitFor(() => expect(again).toBeEnabled())
  expect(again.value).toBe('other')
})

it('after a failed "Apply to all" it shows the message and reloads', async () => {
  const calls = stub()
  const base = globalThis.fetch as unknown as (url: string, init?: RequestInit) => Promise<Response>
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/rules' && init?.method === 'POST') return json({ detail: 'Rule problem.' }, 409)
    return base(url, init)
  }))
  render(Spending)
  await fireEvent.click(await screen.findByRole('button', { name: /^Other spending, £100\.00/ }))
  const select = await screen.findByLabelText('Category for Sunrise Bakery on 12/10/2026')
  await vi.waitFor(() => expect(select).toBeEnabled())
  await fireEvent.change(select, { target: { value: 'food.eating-out' } })
  const apply = await screen.findByRole('button', { name: 'Apply to all from Sunrise Bakery' })
  await vi.waitFor(() => expect(apply).toBeEnabled())
  const before = calls.filter((c) => c.url.startsWith('/api/spending/transactions')).length
  await fireEvent.click(apply)
  expect(await screen.findByText('Rule problem.')).toBeInTheDocument()
  await vi.waitFor(() => expect(calls.filter((c) => c.url.startsWith('/api/spending/transactions')).length).toBeGreaterThan(before))
})

it('clears an old message when a new action starts', async () => {
  stub()
  const base = globalThis.fetch as unknown as (url: string, init?: RequestInit) => Promise<Response>
  let fail = true
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/analysis/run') {
      if (fail) { fail = false; return json({ detail: 'Could not start.' }, 500) }
      return json({ job_id: 1 }, 202)
    }
    return base(url, init)
  }))
  render(Spending)
  const run = await screen.findByRole('button', { name: 'Run analysis now' })
  await vi.waitFor(() => expect(run).toBeEnabled())
  await fireEvent.click(run)
  expect(await screen.findByText('Could not start.')).toBeInTheDocument()
  await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Run analysis now' })).toBeEnabled())
  await fireEvent.click(screen.getByRole('button', { name: 'Run analysis now' }))
  await vi.waitFor(() => expect(screen.queryByText('Could not start.')).not.toBeInTheDocument())
})

it('shows the latest period even when an older response arrives last', async () => {
  const gates: Record<string, (r: Response) => void> = {}
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/categories') return json({ categories })
    if (url === '/api/accounts') return json({ accounts: [] })
    if (url === '/api/household/people') return json({ people: [] })
    if (url === '/api/analysis') return json({ running: false, queued: false, waiting: {}, last_run: null })
    if (url === '/api/categories/refiles') return json({ refiles: [] })
    if (url.startsWith('/api/spending')) {
      const on = new URL(url, 'http://x').searchParams.get('on')
      if (on === '2026-11-01' || on === '2026-09-01') return new Promise<Response>((res) => { gates[on] = res })
      return json(top)
    }
    return json({}, 500)
  }))
  render(Spending)
  await screen.findByRole('heading', { name: 'October 2026' })
  await vi.waitFor(() => expect(screen.getByLabelText('Account')).toBeEnabled())
  await fireEvent.click(screen.getByRole('button', { name: 'Next period' }))
  await fireEvent.click(screen.getByRole('button', { name: 'Previous period' }))
  await vi.waitFor(() => expect(Object.keys(gates)).toHaveLength(2))
  gates['2026-09-01'](json({ ...top, period: { ...period, label: 'September 2026', start: '2026-09-01' } }))
  await screen.findByRole('heading', { name: 'September 2026' })
  gates['2026-11-01'](json({ ...top, period: { ...period, label: 'November 2026', start: '2026-11-01' } }))
  await new Promise((r) => setTimeout(r, 30))
  expect(screen.getByRole('heading', { name: 'September 2026' })).toBeInTheDocument()
})


// --- the money that isn't spending, and sub-categories Tuppence added ----------------------

const kinds = [
  ...categories,
  { id: 'income', parent_id: null, level: 1, label: 'Income', kind: 'income', essential: false, source: 'seed', retired: false, version: 1 },
  { id: 'income.salary', parent_id: 'income', level: 2, label: 'Salary & wages', kind: 'income', essential: false, source: 'seed', retired: false, version: 1 },
  { id: 'savings', parent_id: null, level: 1, label: 'Savings & investments', kind: 'transfer', essential: false, source: 'seed', retired: false, version: 1 },
  { id: 'transfers', parent_id: null, level: 1, label: 'Transfers', kind: 'transfer', essential: false, source: 'seed', retired: false, version: 1 },
  { id: 'transfers.card-repayment', parent_id: 'transfers', level: 2, label: 'Credit card repayments', kind: 'transfer', essential: false, source: 'seed', retired: false, version: 1 },
]
const views: Record<string, unknown> = {
  income: { ...top, path: [...top.path, { id: 'income', label: 'Income' }], total: '0.00', tiles: [] },
  savings: { ...top, path: [...top.path, { id: 'savings', label: 'Savings & investments' }], total: '0.00', tiles: [] },
  transfers: { ...top, path: [...top.path, { id: 'transfers', label: 'Transfers' }], total: '0.00', tiles: [] },
}
const pay = { id: 't_9', date: '2026-10-25', amount: '2450.00', description: 'ACME PAYROLL', merchant: 'Acme',
  account_id: 'a_1', category_id: 'income.salary', category_label: 'Income › Salary & wages', who: 'p_1',
  status: 'inferred', decided_by: 'rule', confidence: 1, version: 2 }
const friend = { id: 't_7', date: '2026-10-03', amount: '-50.00', description: 'J SMITH', merchant: null,
  account_id: 'a_1', category_id: 'transfers.card-repayment', category_label: 'Transfers › Credit card repayments',
  who: 'household', status: 'guessed', decided_by: 'rule', confidence: 0.75, version: 2 }
const refile = { id: 'rf_1', parent: 'Groceries', created: ['Supermarkets', 'Specialist shops'], moved: 12,
  created_at: '2026-10-08T10:00:00Z' }

function stubMoney(over: (url: string, init?: RequestInit) => Promise<Response> | null = () => null) {
  const calls: { url: string; method: string; body: any }[] = []
  let refiles = [refile]
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null })
    const special = over(url, init)
    if (special) return special
    if (url === '/api/categories') return json({ categories: kinds })
    if (url === '/api/accounts') return json({ accounts: [] })
    if (url === '/api/household/people') return json({ people: [] })
    if (url === '/api/analysis') return json({ running: false, queued: false, waiting: {}, last_run: null })
    if (url === '/api/categories/refiles') return json({ refiles })
    if (url === '/api/categories/refiles/rf_1/undo' && method === 'POST') { refiles = []; return json({ moved: 12 }) }
    if (url.startsWith('/api/spending/transactions')) {
      const category = new URL(url, 'http://x').searchParams.get('category')
      return json({ transactions: category === 'income' ? [pay] : category === 'transfers' ? [friend] : [] })
    }
    if (url.startsWith('/api/spending')) {
      const category = new URL(url, 'http://x').searchParams.get('category')
      return json(category ? views[category] : top)
    }
    if (url === '/api/transactions/t_7/understanding' && method === 'PATCH') {
      return json({ understanding: { version: 3, category_id: 'other' }, rule_offer: null })
    }
    return json({ detail: `unexpected ${url}` }, 500)
  }))
  return calls
}

it('opens the lists behind money in, savings and money moved between accounts', async () => {
  const calls = stubMoney()
  render(Spending)
  await fireEvent.click(await screen.findByRole('button', { name: '£2,450.00 came in' }))
  expect(await screen.findByText('Acme')).toBeInTheDocument()
  expect(screen.getByText(/Money that came in isn't spending/)).toBeInTheDocument()
  expect(screen.queryByText(/spent/)).not.toBeInTheDocument()
  expect(calls.some((c) => c.url.startsWith('/api/spending/transactions') && c.url.includes('category=income'))).toBe(true)
  const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
  await fireEvent.click(within(crumbs).getByRole('button', { name: 'All spending' }))
  await fireEvent.click(await screen.findByRole('button', { name: '£0.00 saved or invested' }))
  const nav = screen.getByRole('navigation', { name: 'Breadcrumb' })
  await vi.waitFor(() => expect(within(nav).getByText('Savings & investments')).toHaveAttribute('aria-current', 'page'))
  expect(calls.some((c) => c.url.startsWith('/api/spending/transactions') && c.url.includes('category=savings'))).toBe(true)
  await fireEvent.click(within(nav).getByRole('button', { name: 'All spending' }))
  await fireEvent.click(await screen.findByRole('button', { name: '£50.00 moved between your accounts or taken as cash' }))
  expect(await screen.findByText('J SMITH')).toBeInTheDocument()
  expect(screen.getByText(/If a payment here isn't one of these, choose “Not a transfer”/)).toBeInTheDocument()
})

it('says "Not a transfer" for a paired payment and files it as something else', async () => {
  let release: (r: Response) => void = () => {}
  const calls = stubMoney((url, init) =>
    init?.method === 'PATCH' ? new Promise<Response>((res) => { release = res }) : null)
  render(Spending)
  await fireEvent.click(await screen.findByRole('button', { name: '£50.00 moved between your accounts or taken as cash' }))
  const not = await screen.findByRole('button', { name: 'Not a transfer: J SMITH on 03/10/2026' })
  await vi.waitFor(() => expect(not).toBeEnabled())
  expect(screen.getByText('Best guess')).toBeInTheDocument()
  await fireEvent.click(not)
  const instead = (await screen.findByLabelText('What is J SMITH on 03/10/2026 instead?')) as HTMLSelectElement
  expect([...instead.options].map((o) => o.value)).not.toContain('transfers.card-repayment')
  await fireEvent.change(instead, { target: { value: 'other' } })
  await vi.waitFor(() => expect(instead).toBeDisabled())
  expect(screen.getByLabelText('Category for J SMITH on 03/10/2026')).toBeDisabled()
  release(json({ understanding: { version: 3, category_id: 'other' }, rule_offer: null }))
  expect(await screen.findByText("Filed under Other spending. It's no longer counted as a transfer.")).toBeInTheDocument()
  expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ category_id: 'other', is_transfer: false, expected_version: 2 })
})

it('after a failed "Not a transfer" it shows the message and reloads', async () => {
  const calls = stubMoney((url, init) =>
    init?.method === 'PATCH' ? Promise.resolve(json({ detail: 'This changed since you opened it.' }, 409)) : null)
  render(Spending)
  await fireEvent.click(await screen.findByRole('button', { name: '£50.00 moved between your accounts or taken as cash' }))
  const not = await screen.findByRole('button', { name: 'Not a transfer: J SMITH on 03/10/2026' })
  await vi.waitFor(() => expect(not).toBeEnabled())
  await fireEvent.click(not)
  const before = calls.filter((c) => c.url.startsWith('/api/spending/transactions')).length
  await fireEvent.change(await screen.findByLabelText('What is J SMITH on 03/10/2026 instead?'), { target: { value: 'other' } })
  expect(await screen.findByText('This changed since you opened it.')).toBeInTheDocument()
  await vi.waitFor(() => expect(calls.filter((c) => c.url.startsWith('/api/spending/transactions')).length).toBeGreaterThan(before))
})

it('lists the sub-categories Tuppence added and undoes one', async () => {
  let release: (r: Response) => void = () => {}
  const calls = stubMoney((url, init) =>
    url === '/api/categories/refiles/rf_1/undo' && init?.method === 'POST'
      ? new Promise<Response>((res) => { release = res }) : null)
  render(Spending)
  const section = await screen.findByRole('region', { name: 'Sub-categories Tuppence added' })
  expect(within(section).getByText(/Split Groceries into Supermarkets, Specialist shops · moved 12 payments · 08\/10\/2026/)).toBeInTheDocument()
  const undo = within(section).getByRole('button', { name: 'Undo: the split of Groceries' })
  await vi.waitFor(() => expect(undo).toBeEnabled())
  await fireEvent.click(undo)
  await vi.waitFor(() => expect(undo).toBeDisabled())
  expect(screen.getByRole('button', { name: 'Run analysis now' })).toBeDisabled()
  release(json({ moved: 12 }))
  expect(await screen.findByText('Undone: 12 payments are back in Groceries.')).toBeInTheDocument()
  expect(calls.some((c) => c.url === '/api/categories/refiles/rf_1/undo' && c.method === 'POST')).toBe(true)
})

it('after a failed undo it shows the message and reloads the list', async () => {
  const calls = stubMoney((url, init) =>
    url === '/api/categories/refiles/rf_1/undo' && init?.method === 'POST'
      ? Promise.resolve(json({ detail: 'This has already been undone.' }, 422)) : null)
  render(Spending)
  const undo = await screen.findByRole('button', { name: 'Undo: the split of Groceries' })
  await vi.waitFor(() => expect(undo).toBeEnabled())
  const before = calls.filter((c) => c.url === '/api/categories/refiles').length
  await fireEvent.click(undo)
  expect(await screen.findByText('This has already been undone.')).toBeInTheDocument()
  await vi.waitFor(() => expect(calls.filter((c) => c.url === '/api/categories/refiles').length).toBeGreaterThan(before))
})
