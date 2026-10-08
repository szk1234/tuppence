import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Spending from './Spending.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const period = { start: '2026-10-01', end: '2026-10-31', label: 'October 2026', mode: 'calendar_month',
  previous: '2026-09-01', next: '2026-11-01', has_later_data: false }
const top = { period, path: [{ id: null, label: 'All spending' }], total: '300.00', direct: '0.00',
  money_in: '2450.00', saved: '0.00', waiting_for_ai: 0,
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
  expect(within(crumbs).getByText('Other spending')).toHaveAttribute('aria-current', 'page')
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
