import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Rules from './Rules.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

it('lists rules and switches one off', async () => {
  const rule = { id: 'r_1', description: 'Payments to Sunrise Bakery → Food & drink › Eating out', source: 'user',
    enabled: true, hit_count: 3, version: 1, merchant_id: 'm_1', set_category_id: 'food.eating-out', min_amount: null, max_amount: null }
  const seed = { ...rule, id: 'seed-dvla', description: 'Payments mentioning “DVLA” → Road tax', source: 'seed' }
  let rules = [rule, seed]
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/rules/r_1/disable' && init?.method === 'POST') {
      rules = [seed]
      return json({ rule: { ...rule, enabled: false }, released: 3 })
    }
    return json({ rules })
  }))
  render(Rules)
  expect(await screen.findByText(/Payments to Sunrise Bakery/)).toBeInTheDocument()
  expect(screen.getByText(/Payments mentioning “DVLA”/)).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Switch off: Payments to Sunrise Bakery → Food & drink › Eating out' }))
  expect(await screen.findByText('Switched off. 3 transactions will be looked at again.')).toBeInTheDocument()
  expect(await screen.findByText('No rules yet.')).toBeInTheDocument()
})

it('locks "Switch off" while a rule is being switched off', async () => {
  const rule = { id: 'r_1', description: 'Payments to Sunrise Bakery', source: 'user', enabled: true, hit_count: 3, version: 1,
    merchant_id: 'm_1', set_category_id: 'food', min_amount: null, max_amount: null }
  let release: (r: Response) => void = () => {}
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) =>
    init?.method === 'POST' ? new Promise<Response>((res) => { release = res }) : json({ rules: [rule] })))
  render(Rules)
  const button = await screen.findByRole('button', { name: /Switch off: Payments to Sunrise Bakery/ })
  await vi.waitFor(() => expect(button).toBeEnabled())
  await fireEvent.click(button)
  await vi.waitFor(() => expect(button).toBeDisabled())
  release(json({ rule: { ...rule, enabled: false }, released: 0 }))
  await vi.waitFor(() => expect(button).toBeEnabled())
})
