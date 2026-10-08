import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import StatementDetail from './StatementDetail.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const base = {
  id: 's_1', filename: 'starling.csv', format: 'csv', account_id: 'a_1', account_name: 'Starling (Starling)',
  provider: 'starling', period_start: '2026-10-01', period_end: '2026-10-28', opening_balance: '1000.00',
  closing_balance: '2252.32', balance_verified: false, counts: { rows: 2, new: 0, duplicates: 0, skipped: 0 },
  question: null, error: null, warnings: [], importer: 'csv:starling', created_at: 'x', duplicate: false,
  level: 'full', draft_skipped: [], transactions: [],
}

it('lets the person fix a line, check again and import', async () => {
  const review = {
    ...base, status: 'needs_review', status_label: 'Needs your check', version: 4,
    check_errors: ['balance mismatch: opening 1000.00 + sum 1252.50 = 2252.50, closing 2252.32'],
    draft_rows: [
      { ref: 'L2', date: '2026-10-01', amount: '-42.18', description: 'Greenbasket Stores', balance_after: '957.82', edited: false, errors: [] },
      { ref: 'L3', date: '2026-10-03', amount: '-48.02', description: 'Home Cover Ltd', balance_after: '909.62', edited: false,
        errors: ['L3: running balance mismatch (previous 957.82 + amount -48.02 = 909.80, got 909.62)'] },
    ],
  }
  const calls: { url: string; method: string; body: any }[] = []
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null })
    if (method === 'GET') return json(review)
    if (method === 'PUT') return json({ ...review, version: 5, check_errors: [], draft_rows: review.draft_rows.map((r) => ({ ...r, errors: [] })) })
    return json({ ...review, status: 'imported', version: 6 })
  }))
  render(StatementDetail, { id: 's_1' })
  expect(await screen.findByText(/^balance mismatch/)).toBeInTheDocument()
  expect(screen.getByText(/L3: running balance mismatch/)).toBeInTheDocument()
  await fireEvent.input(screen.getByLabelText('Amount for L3'), { target: { value: '-48.20' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
  expect(await screen.findByText('Saved. Everything adds up now.')).toBeInTheDocument()
  const put = calls.find((c) => c.method === 'PUT')!
  expect(put.url).toBe('/api/statements/s_1/draft')
  expect(put.body.rows[1]).toEqual({ ref: 'L3', date: '2026-10-03', amount: '-48.20', description: 'Home Cover Ltd' })
  expect(put.body.expected_version).toBe(4)
  await fireEvent.click(screen.getByRole('button', { name: 'Import these transactions' }))
  await vi.waitFor(() => expect(calls.some((c) => c.url === '/api/statements/s_1/accept')).toBe(true))
  expect(calls.find((c) => c.url === '/api/statements/s_1/accept')!.body).toEqual({ expected_version: 5 })
})

it('shows imported transactions in UK formats', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({
    ...base, status: 'imported', status_label: 'Imported', version: 2, balance_verified: true, check_errors: [], draft_rows: [],
    transactions: [{ id: 't_1', date: '2026-10-01', amount: '-42.18', description: 'Greenbasket Stores', balance_after: '957.82' }],
  })))
  render(StatementDetail, { id: 's_1' })
  expect(await screen.findByText('Greenbasket Stores')).toBeInTheDocument()
  expect(screen.getAllByText('01/10/2026').length).toBeGreaterThan(0)
  expect(screen.getByText('-£42.18')).toBeInTheDocument()
  expect(screen.getByText('Balances add up')).toBeInTheDocument()
})

it('decides held-back lines, offers Wrong account and Try again with the cost warning', async () => {
  const review = {
    ...base, status: 'needs_review', status_label: 'Needs your check', version: 4,
    check_errors: ['2 lines were held back from the AI'],
    held_lines: [{ ref: 'L5', text: 'Mystery payment 12.50' }, { ref: 'L6', text: 'Account summary' }],
    draft_rows: [{ ref: 'L2', date: '2026-10-01', amount: '-42.18', description: 'Greenbasket Stores', balance_after: null, edited: false, errors: [] }],
  }
  const calls: { url: string; method: string; body: any }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null })
    if (url === '/api/accounts') return json({ accounts: [{ id: 'a_1', nickname: 'Starling', provider_name: 'Starling', kind: 'current', last4: null }] })
    if (url === '/api/accounts/providers') return json({ providers: [] })
    if (url === '/api/household/people') return json({ people: [] })
    if (method === 'GET') return json(review)
    if (method === 'PUT') return json({ ...review, version: 5, held_lines: [], check_errors: [] })
    return json({ ...review, status: 'received', version: 6 })
  }))
  render(StatementDetail, { id: 's_1' })
  expect(await screen.findByText(/Mystery payment/)).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'L6 is not a transaction' }))
  await fireEvent.click(screen.getByRole('button', { name: 'Add L5 as a transaction' }))
  expect(screen.queryByText(/Account summary/)).not.toBeInTheDocument()
  await fireEvent.input(screen.getByLabelText('Date for L5'), { target: { value: '03/10/2026' } })
  await fireEvent.input(screen.getByLabelText('Amount for L5'), { target: { value: '-12.50' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Import these transactions' }))
  expect(await screen.findByText(/Press Check again to save them/)).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
  expect(await screen.findByText('Saved. Everything adds up now.')).toBeInTheDocument()
  const put = calls.find((c) => c.method === 'PUT')!
  expect(put.body.rows.map((r: any) => r.ref)).toEqual(['L2', 'L5'])
  expect(put.body.rows[1]).toEqual({ ref: 'L5', date: '2026-10-03', amount: '-12.50', description: 'Mystery payment 12.50' })
  expect(put.body.skipped).toEqual([{ ref: 'L6', reason: 'Marked as not a transaction' }])
  expect(screen.getByText(/may cost another read/)).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Wrong account?' }))
  await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Use this account' })).toBeEnabled())
  await fireEvent.click(screen.getByLabelText(/Starling · Starling/))
  await fireEvent.click(screen.getByRole('button', { name: 'Use this account' }))
  await vi.waitFor(() => expect(calls.some((c) => c.url === '/api/statements/s_1/change-account')).toBe(true))
  expect(calls.find((c) => c.url === '/api/statements/s_1/change-account')!.body).toEqual({ account_id: 'a_1', expected_version: 5 })
})

it('guards re-reading an imported statement behind an explicit confirmation', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({
    ...base, status: 'imported', status_label: 'Imported', version: 2, check_errors: [], draft_rows: [], held_lines: [], transactions: [],
  })))
  render(StatementDetail, { id: 's_1' })
  expect(await screen.findByText('Something wrong with this import?')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Wrong account?' })).not.toBeInTheDocument()
  expect(screen.getByText('This will remove these transactions and read the file again.')).toBeInTheDocument()
  await fireEvent.click(screen.getByLabelText(/I understand/))
  expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Wrong account?' })).toBeInTheDocument()
})
