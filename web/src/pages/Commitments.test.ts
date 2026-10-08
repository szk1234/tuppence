import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Commitments from './Commitments.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const streamly = { id: 'c_1', name: 'Streamly', kind: 'subscription', cadence: 'monthly', cadence_label: 'Every month',
  amount: '11.99', annual_cost: '143.88', next_due: '2026-11-14', last_paid: '2026-10-14', status: 'active',
  flags: ['price_rise'], flag_labels: ['Price went up'],
  price_history: [{ since: '2026-01-14', amount: '9.99' }, { since: '2026-06-14', amount: '11.99' }],
  duplicate_of: [], account_id: 'a_1', category_id: 'subscriptions.tv-streaming', dismissed: false, version: 2 }
const view = { commitments: [streamly], calendar_start: '2026-11-01', calendar_end: '2026-12-31',
  upcoming: [{ date: '2026-11-14', commitment_id: 'c_1', name: 'Streamly', amount: '11.99', kind: 'subscription' }],
  totals: { annual: '143.88', monthly: '11.99', count: 1, by_kind: { bill: '0.00', subscription: '143.88', instalment: '0.00' } } }

it('shows totals, flags, the calendar and lets the person hide one', async () => {
  const calls: { url: string; body: any }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(init.body as string) : null })
    if (url.endsWith('/dismiss')) return json({ ...streamly, dismissed: true, version: 3 })
    return json(view)
  }))
  render(Commitments)
  expect(await screen.findByText(/Price went up from £9\.99 to £11\.99 on 14\/06\/2026/)).toBeInTheDocument()
  expect(screen.getAllByText('£143.88').length).toBeGreaterThan(0)
  expect(screen.getByText('November 2026')).toBeInTheDocument()
  expect(screen.getByText('Streamly £11.99')).toBeInTheDocument()
  expect(screen.getByRole('cell', { name: '14/11/2026' })).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Streamly is not a commitment' }))
  await vi.waitFor(() => expect(calls.some((c) => c.url === '/api/commitments/c_1/dismiss')).toBe(true))
  expect(calls.find((c) => c.url.endsWith('/dismiss'))!.body).toEqual({ expected_version: 2 })
})
