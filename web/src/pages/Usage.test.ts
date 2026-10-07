import { render, screen, within } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Usage from './Usage.svelte'

afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } })

it('shows the month total against the cap, tables and the estimated note', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/usage')) return json({
      total_gbp: 1.234, calls: 7, failed_calls: 1, estimated_calls: 2, month: '2026-10', current_month: '2026-10', cap_gbp: 10,
      by_task: { coach: { calls: 7, failed_calls: 1, tokens: 1200, gbp: 1.234 } },
      by_model: { 'gpt-x': { calls: 7, failed_calls: 1, tokens: 1200, gbp: 1.234 } },
      by_day: { '2026-10-07': { calls: 4, failed_calls: 0, tokens: 700, gbp: 0.9 }, '2026-10-02': { calls: 3, failed_calls: 1, tokens: 500, gbp: 0.334 } },
    })
    return json({ detail: 'unexpected' }, 500)
  }))
  render(Usage)
  expect(await screen.findByText('£1.23 of £10.00')).toBeInTheDocument()
  expect(screen.getByText(/7 calls/)).toBeInTheDocument()
  expect(screen.getByText('coach')).toBeInTheDocument()
  expect(screen.getAllByText('estimated').length).toBeGreaterThan(0)
  expect(screen.getByText(/fallback price/)).toBeInTheDocument()
  expect(screen.getByText(/this month/)).toBeInTheDocument()
  expect(screen.getByText(/set a model's real price in Settings › AI, under Models/)).toBeInTheDocument()
  const days = screen.getByRole('heading', { name: 'By day' }).parentElement!
  const rows = within(days).getAllByRole('row').slice(1).map((r) => r.textContent)
  expect(rows).toEqual(['Fri 2 Oct31500£0.33', 'Wed 7 Oct40700£0.90'])
})

it('names a past month and says months follow the cap clock', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({
    total_gbp: 0, calls: 0, failed_calls: 0, estimated_calls: 0, month: '2026-07', current_month: '2026-08', cap_gbp: 10,
    by_task: {}, by_model: {}, by_day: {},
  })))
  render(Usage)
  expect(await screen.findByText(/in July 2026/)).toBeInTheDocument()
  expect(screen.getByText(/same clock as the spending cap/)).toBeInTheDocument()
})
