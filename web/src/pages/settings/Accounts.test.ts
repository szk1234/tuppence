import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import { json, stubApi } from '../../components/forms/helpers'
import Accounts from './Accounts.svelte'

afterEach(() => vi.unstubAllGlobals())

const person = { id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 }
const account = (over: Record<string, unknown> = {}) => ({
  id: 'a_1', provider: 'monzo', provider_name: 'Monzo', kind: 'current', nickname: 'Main', last4: '1234', owner_ids: ['p_1'], credit_limit: null,
  purchase_apr: null, promo_apr: null, promo_end: null, statement_day: null, status: 'active', version: 1, joint: false, ...over,
})

it('closes an account and shows it under Closed, then reopens it', async () => {
  let current = account()
  const calls = stubApi((url, method) => {
    if (url === '/api/household/people') return json({ people: [person] })
    if (url.startsWith('/api/accounts?')) return json({ accounts: [current] })
    if (url === '/api/accounts/providers') return json({ providers: [{ id: 'monzo', name: 'Monzo', kinds: ['current'] }] })
    if (url === '/api/accounts/a_1/close' && method === 'POST') { current = account({ status: 'closed', version: 2 }); return json(current) }
    if (url === '/api/accounts/a_1/reopen' && method === 'POST') { current = account({ version: 3 }); return json(current) }
  })
  render(Accounts)
  expect(await screen.findByText('Main')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Closed' })).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: 'Close Main' }))
  const heading = await screen.findByRole('heading', { name: 'Closed' })
  expect(within(heading.parentElement as HTMLElement).getByText('Main')).toBeInTheDocument()
  expect(calls.find((c) => c.url === '/api/accounts/a_1/close')?.body).toEqual({ expected_version: 1 })
  await fireEvent.click(screen.getByRole('button', { name: 'Reopen Main' }))
  await vi.waitFor(() => expect(screen.queryByRole('heading', { name: 'Closed' })).toBeNull())
})

it('shows the conflict message and reloads on a stale close', async () => {
  let gets = 0
  stubApi((url, method) => {
    if (url === '/api/household/people') return json({ people: [person] })
    if (url.startsWith('/api/accounts?')) { gets += 1; return json({ accounts: [account({ nickname: gets > 1 ? 'Renamed' : 'Main', version: gets })] }) }
    if (method === 'POST') return json({ detail: 'This was changed somewhere else. Reload and try again.', current_version: 2 }, 409)
  })
  render(Accounts)
  await fireEvent.click(await screen.findByRole('button', { name: 'Close Main' }))
  expect(await screen.findByText('This was changed somewhere else. Reload and try again.')).toBeInTheDocument()
  expect(await screen.findByText('Renamed')).toBeInTheDocument()
})

it('adds an account', async () => {
  const accounts: unknown[] = []
  const calls = stubApi((url, method, body) => {
    if (url === '/api/household/people') return json({ people: [person] })
    if (url.startsWith('/api/accounts?')) return json({ accounts })
    if (url === '/api/accounts/providers') return json({ providers: [{ id: 'monzo', name: 'Monzo', kinds: ['current'] }] })
    if (url === '/api/accounts' && method === 'POST') { const a = account({ nickname: body.nickname }); accounts.push(a); return json(a, 201) }
  })
  render(Accounts)
  await vi.waitFor(() => expect(screen.getByLabelText('Nickname')).toBeEnabled())
  await fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'monzo' } })
  await fireEvent.input(screen.getByLabelText('Nickname'), { target: { value: 'Bills' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
  expect(await screen.findByText('Bills added.')).toBeInTheDocument()
  expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ provider: 'monzo', kind: 'current', nickname: 'Bills', owner_ids: ['p_1'] })
  expect(screen.getByLabelText('Nickname')).toHaveValue('')
})

it('closing an account says which incomes need a new receiving account', async () => {
  let current = account()
  stubApi((url, method) => {
    if (url === '/api/household/people') return json({ people: [person] })
    if (url.startsWith('/api/accounts?')) return json({ accounts: [current] })
    if (url === '/api/accounts/providers') return json({ providers: [] })
    if (url === '/api/accounts/a_1/close' && method === 'POST') {
      current = account({ status: 'closed', version: 2 })
      return json({ ...current, affected_income: [{ id: 'i_1', name: 'Acme Payroll' }] })
    }
  })
  render(Accounts)
  await fireEvent.click(await screen.findByRole('button', { name: 'Close Main' }))
  expect(await screen.findByText('Main closed. Acme Payroll needs a new receiving account: choose one in Settings › Income.')).toBeInTheDocument()
})
