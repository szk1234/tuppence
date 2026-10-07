import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import { json, stubApi } from '../../components/forms/helpers'
import Income from './Income.svelte'

afterEach(() => vi.unstubAllGlobals())

const person = { id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 }
const income = (over: Record<string, unknown> = {}) => ({
  id: 'i_1', person_id: 'p_1', kind: 'salary', name: 'Salary', net_amount: '2345.67', account_id: null,
  pay_rule: { type: 'last_working_day' }, pay_rule_description: 'Last working day of the month', variable_components: [],
  next_pay_date: '2026-10-30', person_left: false, calendar_assumed: true, status: 'active', version: 1, ...over,
})

it('lists income, nudges for the nation and ends an income', async () => {
  let current = income()
  const calls = stubApi((url, method) => {
    if (url === '/api/household/people') return json({ people: [person] })
    if (url === '/api/accounts') return json({ accounts: [] })
    if (url.startsWith('/api/income?')) return json({ income: [current] })
    if (url === '/api/income/i_1/end' && method === 'POST') { current = income({ status: 'ended', version: 2 }); return json(current) }
  })
  render(Income)
  expect(await screen.findByText(/Alex Example · £2,345.67 · Last working day of the month · next payday 30 Oct 2026/)).toBeInTheDocument()
  expect(screen.getByText('Set your nation for accurate bank holidays.')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'End Salary' }))
  expect(await screen.findByRole('heading', { name: 'Ended' })).toBeInTheDocument()
  expect(calls.find((c) => c.url === '/api/income/i_1/end')?.body).toEqual({ expected_version: 1 })
})

it('an income whose account closed is shown as needing a new one, and the edit form says so', async () => {
  stubApi((url) => {
    if (url === '/api/household/people') return json({ people: [person] })
    if (url === '/api/accounts') return json({ accounts: [] })
    if (url.startsWith('/api/income?')) return json({ income: [income({ account_id: 'a_gone', needs_account: true })] })
  })
  render(Income)
  expect(await screen.findByText(/its account has closed or is no longer theirs: choose another/)).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Edit Salary' }))
  expect(screen.getByText('The account this was paid into has closed or is no longer theirs. Choose another.')).toBeInTheDocument()
})
