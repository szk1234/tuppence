import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Statements from './Statements.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const view = (over: Record<string, unknown>) => ({
  id: 's_1', filename: 'monzo.csv', format: 'csv', status: 'imported', status_label: 'Imported', account_id: 'a_1',
  account_name: 'Monzo (Monzo)', provider: 'monzo', period_start: '2026-10-01', period_end: '2026-10-28',
  opening_balance: null, closing_balance: null, balance_verified: false,
  counts: { rows: 9, new: 9, duplicates: 0, skipped: 1 }, question: null, error: null, warnings: [],
  importer: 'csv:monzo', created_at: 'x', version: 3, duplicate: false, ...over,
})

it('shows each statement with a plain status and what to do next', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url === '/api/settings'
    ? json({ settings: [{ key: 'ingest.vision_for_scans', value: false, version: 0 }] })
    : json({ statements: [
      view({}),
      view({ id: 's_2', filename: 'bad.csv', status: 'needs_review', status_label: 'Needs your check' }),
      view({ id: 's_3', filename: 'statement.pdf', status: 'failed', status_label: "Couldn't import",
             error: 'This file needs an AI model to read it.' }),
    ] })))
  render(Statements)
  const monzo = await screen.findByRole('listitem', { name: 'monzo.csv' })
  expect(within(monzo).getByText('Imported')).toBeInTheDocument()
  expect(within(monzo).getByText('9 new transactions · balance unverified')).toBeInTheDocument()
  expect(within(monzo).getByText('Monzo (Monzo) · 01/10/2026 to 28/10/2026')).toBeInTheDocument()
  const bad = screen.getByRole('listitem', { name: 'bad.csv' })
  expect(within(bad).getByRole('link', { name: 'Check and fix' })).toHaveAttribute('href', '/statements/s_2')
  const pdf = screen.getByRole('listitem', { name: 'statement.pdf' })
  expect(within(pdf).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  expect(await screen.findByLabelText(/Use my AI vision model/)).not.toBeChecked()
})

it('locks the vision switch while its change is saving', async () => {
  let release: (r: Response) => void = () => {}
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PATCH') return new Promise<Response>((res) => { release = res })
    return url === '/api/settings'
      ? json({ settings: [{ key: 'ingest.vision_for_scans', value: false, version: 0 }] })
      : json({ statements: [] })
  }))
  render(Statements)
  const box = await screen.findByLabelText(/Use my AI vision model/)
  await fireEvent.click(box)
  await vi.waitFor(() => expect(box).toBeDisabled())
  release(json({ key: 'ingest.vision_for_scans', value: true, version: 1 }))
  await vi.waitFor(() => expect(box).toBeEnabled())
  expect(box).toBeChecked()
})
