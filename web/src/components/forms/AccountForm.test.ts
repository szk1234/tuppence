import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import type { Account, Person } from '../../lib/types'
import AccountForm from './AccountForm.svelte'
import { json, stubApi } from './helpers'

afterEach(() => vi.unstubAllGlobals())

const people: Person[] = [
  { id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 },
  { id: 'p_2', display_name: 'Sam Example', role: 'adult', birth_year: null, status: 'active', version: 1 },
]
const providers = { providers: [
  { id: 'monzo', name: 'Monzo', kinds: ['current', 'savings'] },
  { id: 'barclaycard', name: 'Barclaycard', kinds: ['credit_card'] },
  { id: 'other', name: 'Other', kinds: ['current', 'savings', 'credit_card'] },
] }
const route = (url: string) => (url === '/api/accounts/providers' ? json(providers) : undefined)

async function renderForm(props: Record<string, unknown> = {}) {
  const onsubmit = vi.fn(async () => true)
  render(AccountForm, { people, onsubmit, ...props })
  await vi.waitFor(() => expect(screen.getByLabelText('Nickname')).toBeEnabled())
  return onsubmit
}

it('hides card fields for current accounts and shows them for credit cards', async () => {
  stubApi(route)
  await renderForm()
  expect(screen.queryByLabelText('Credit limit')).toBeNull()
  expect(screen.getByText('We never ask for full account numbers or sort codes.')).toBeInTheDocument()
  await fireEvent.change(screen.getByLabelText('Account type'), { target: { value: 'credit_card' } })
  expect(screen.getByLabelText('Credit limit')).toBeInTheDocument()
  expect(screen.getByLabelText('Purchase APR (%)')).toBeInTheDocument()
  expect(screen.getByLabelText('Statement day (1 to 31)')).toBeInTheDocument()
})

it('stays locked until the providers have loaded', async () => {
  let release: (r: Response) => void = () => {}
  stubApi((url) => (url === '/api/accounts/providers' ? new Promise<Response>((r) => { release = r }) as never : undefined))
  render(AccountForm, { people, onsubmit: async () => true })
  expect(screen.getByLabelText('Nickname')).toBeDisabled()
  release(json(providers))
  await vi.waitFor(() => expect(screen.getByLabelText('Nickname')).toBeEnabled())
})

it('filters providers by kind but can show them all', async () => {
  stubApi(route)
  await renderForm()
  const names = () => Array.from(screen.getByLabelText('Provider').querySelectorAll('option')).map((o) => o.textContent)
  expect(names()).toContain('Monzo')
  expect(names()).not.toContain('Barclaycard')
  await fireEvent.click(screen.getByLabelText('Show all providers'))
  expect(names()).toContain('Barclaycard')
})

it('sends a joint account with the provider name for Other', async () => {
  stubApi(route)
  const onsubmit = await renderForm()
  await fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'other' } })
  await fireEvent.input(screen.getByLabelText('Provider name'), { target: { value: 'Tiny Credit Union' } })
  await fireEvent.input(screen.getByLabelText('Nickname'), { target: { value: 'Bills' } })
  await fireEvent.input(screen.getByLabelText('Last 4 digits (optional)'), { target: { value: '1234' } })
  await fireEvent.click(screen.getByLabelText('Alex Example'))
  await fireEvent.click(screen.getByLabelText('Sam Example'))
  await fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
  await vi.waitFor(() => expect(onsubmit).toHaveBeenCalled())
  expect(onsubmit).toHaveBeenCalledWith({
    provider: 'other', provider_name: 'Tiny Credit Union', kind: 'current', nickname: 'Bills', last4: '1234', owner_ids: ['p_1', 'p_2'],
  })
})

it('asks for owners and a sensible last 4 digits before sending', async () => {
  stubApi(route)
  const onsubmit = await renderForm()
  await fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'monzo' } })
  await fireEvent.input(screen.getByLabelText('Nickname'), { target: { value: 'Main' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
  expect(await screen.findByText('Choose at least one owner.')).toBeInTheDocument()
  await fireEvent.click(screen.getByLabelText('Alex Example'))
  await fireEvent.input(screen.getByLabelText('Last 4 digits (optional)'), { target: { value: '12a4' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
  expect(await screen.findByText('Enter exactly 4 digits, or leave this blank.')).toBeInTheDocument()
  expect(onsubmit).not.toHaveBeenCalled()
})

it('sends only the changes on edit, and clears card fields when the kind changes', async () => {
  stubApi(route)
  const card: Account = {
    id: 'a_1', provider: 'barclaycard', provider_name: 'Barclaycard', kind: 'credit_card', nickname: 'Everyday', last4: null,
    owner_ids: ['p_1'], credit_limit: '2500.00', purchase_apr: 22.9, promo_apr: null, promo_end: null, statement_day: 12,
    status: 'active', version: 3, joint: false,
  }
  const onsubmit = await renderForm({ initial: card })
  expect(screen.getByLabelText('Credit limit')).toHaveValue('2500.00')
  await fireEvent.input(screen.getByLabelText('Nickname'), { target: { value: 'Everyday card' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save account' }))
  await vi.waitFor(() => expect(onsubmit).toHaveBeenCalledWith({ nickname: 'Everyday card' }))
})

it('keeps card APRs within 0-100', async () => {
  const onsubmit = vi.fn(async () => true)
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ providers: [{ id: 'barclaycard', name: 'Barclaycard', kinds: ['credit_card'] }] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  render(AccountForm, { people: [{ id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 }], onsubmit })
  await fireEvent.change(screen.getByLabelText('Account type'), { target: { value: 'credit_card' } })
  await vi.waitFor(() => expect(screen.getByLabelText('Provider')).toBeEnabled())
  await fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'barclaycard' } })
  await fireEvent.input(screen.getByLabelText('Nickname'), { target: { value: 'Card' } })
  await fireEvent.input(screen.getByLabelText('Purchase APR (%)'), { target: { value: '129.9' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
  expect(await screen.findByText('Enter the purchase APR as a percentage like 22.9.')).toBeInTheDocument()
  expect(onsubmit).not.toHaveBeenCalled()
  vi.unstubAllGlobals()
})
