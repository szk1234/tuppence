import { render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import HomeCards from './HomeCards.svelte'

afterEach(() => vi.unstubAllGlobals())

it('shows this period, top categories, what is due and the last run', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    period: { start: '2026-10-01', end: '2026-10-31', label: 'October 2026' }, spent: '2140.55',
    top: [{ id: 'housing', label: 'Housing', amount: '1350.00' }, { id: 'food', label: 'Food & drink', amount: '420.10' }],
    due_soon: [{ date: '2026-10-14', commitment_id: 'c_1', name: 'Streamly', amount: '9.99', kind: 'subscription' }],
    analysis: { running: false, queued: false, waiting: { awaiting_ai: 4 },
      last_run: { status: 'done', summary: 'Sorted 84 transactions.', finished_at: 'x', started_at: 'x' } },
  }), { headers: { 'Content-Type': 'application/json' } })))
  render(HomeCards)
  expect(await screen.findByRole('heading', { name: 'Spending · October 2026' })).toBeInTheDocument()
  expect(screen.getByText('£2,140.55')).toBeInTheDocument()
  expect(screen.getByText('Housing: £1,350.00')).toBeInTheDocument()
  expect(screen.getByText('14/10/2026: Streamly £9.99')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('4 transactions are waiting for an AI model')
  expect(screen.getByText('Last look: Sorted 84 transactions.')).toBeInTheDocument()
})

it('shows nothing when the payload is not a summary', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'x' }), { headers: { 'Content-Type': 'application/json' } })))
  const { container } = render(HomeCards)
  await new Promise((r) => setTimeout(r, 20))
  expect(container.querySelector('section')).toBeNull()
})
