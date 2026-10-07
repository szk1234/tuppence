import { render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Usage from './Usage.svelte'

afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } })

it('shows the month total against the cap, tables and the estimated note', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/usage')) return json({
      total_gbp: 1.234, calls: 7, failed_calls: 1, estimated_calls: 2, month: new Date().toISOString().slice(0, 7), cap_gbp: 10,
      by_task: { coach: { calls: 7, failed_calls: 1, tokens: 1200, gbp: 1.234 } },
      by_model: { 'gpt-x': { calls: 7, failed_calls: 1, tokens: 1200, gbp: 1.234 } },
      by_day: {},
    })
    return json({ detail: 'unexpected' }, 500)
  }))
  render(Usage)
  expect(await screen.findByText('£1.23 of £10.00')).toBeInTheDocument()
  expect(screen.getByText(/7 calls/)).toBeInTheDocument()
  expect(screen.getByText('coach')).toBeInTheDocument()
  expect(screen.getAllByText('estimated').length).toBeGreaterThan(0)
  expect(screen.getByText(/fallback price/)).toBeInTheDocument()
})
