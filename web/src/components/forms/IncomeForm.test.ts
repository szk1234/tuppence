import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import type { Account, Income, Person } from '../../lib/types'
import IncomeForm from './IncomeForm.svelte'
import { json, stubApi } from './helpers'

afterEach(() => vi.unstubAllGlobals())

const people: Person[] = [
  { id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 },
  { id: 'p_2', display_name: 'Sam Example', role: 'adult', birth_year: null, status: 'active', version: 1 },
  { id: 'p_3', display_name: 'Kid A', role: 'child', birth_year: 2019, status: 'active', version: 1 },
]
const accounts: Account[] = [
  { id: 'a_1', provider: 'monzo', provider_name: 'Monzo', kind: 'current', nickname: 'Joint Monzo', last4: null, owner_ids: ['p_1'], credit_limit: null,
    purchase_apr: null, promo_apr: null, promo_end: null, statement_day: null, status: 'active', version: 1, joint: false },
  { id: 'a_2', provider: 'monzo', provider_name: 'Monzo', kind: 'current', nickname: 'Someone else', last4: null, owner_ids: ['p_9'], credit_limit: null,
    purchase_apr: null, promo_apr: null, promo_end: null, statement_day: null, status: 'active', version: 1, joint: false },
]
const preview = (url: string) => (url === '/api/income/preview-rule' ? json({ next_dates: ['2026-10-30'], calendar_assumed: false }) : undefined)

it('builds a salary body with pounds as a string and offers only the owner\'s accounts', async () => {
  stubApi(preview)
  const onsubmit = vi.fn(async () => true)
  render(IncomeForm, { people, accounts, onsubmit })
  expect(screen.queryByRole('option', { name: 'Kid A' })).toBeNull()
  await fireEvent.input(screen.getByLabelText('Income name'), { target: { value: 'Salary' } })
  await fireEvent.input(screen.getByLabelText("Take-home amount each time you're paid"), { target: { value: '£2,345.67' } })
  await fireEvent.change(screen.getByLabelText('How often are you paid?'), { target: { value: 'last_working_day' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add income' }))
  expect(await screen.findByText('Choose who receives this income.')).toBeInTheDocument()
  expect(screen.queryByRole('option', { name: 'Someone else' })).toBeNull() // no person chosen yet means no accounts offered
  await fireEvent.change(screen.getByLabelText('Who receives this income?'), { target: { value: 'p_1' } })
  expect(screen.getByRole('option', { name: 'Joint Monzo' })).toBeInTheDocument()
  expect(screen.queryByRole('option', { name: 'Someone else' })).toBeNull()
  await fireEvent.click(screen.getByLabelText('Bonus'))
  await fireEvent.click(screen.getByRole('button', { name: 'Add income' }))
  await vi.waitFor(() => expect(onsubmit).toHaveBeenCalled())
  expect(onsubmit).toHaveBeenCalledWith({
    person_id: 'p_1', kind: 'salary', name: 'Salary', net_amount: '2345.67', pay_rule: { type: 'last_working_day' }, variable_components: ['bonus'],
  })
})

it('does not send an income with no pay schedule', async () => {
  stubApi(preview)
  const onsubmit = vi.fn(async () => true)
  render(IncomeForm, { people: [people[0]], accounts, onsubmit })
  await fireEvent.input(screen.getByLabelText('Income name'), { target: { value: 'Salary' } })
  await fireEvent.input(screen.getByLabelText("Take-home amount each time you're paid"), { target: { value: '100' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add income' }))
  expect(await screen.findByText(/Choose how often you're paid/)).toBeInTheDocument()
  expect(onsubmit).not.toHaveBeenCalled()
})

it('edits only what changed', async () => {
  stubApi(preview)
  const onsubmit = vi.fn(async () => true)
  const income: Income = {
    id: 'i_1', person_id: 'p_1', kind: 'salary', name: 'Salary', net_amount: '2000.00', account_id: null,
    pay_rule: { type: 'monthly_day', day: 28, adjust: 'previous_working_day' }, pay_rule_description: 'On the 28th', variable_components: [],
    next_pay_date: '2026-10-28', person_left: false, calendar_assumed: false, status: 'active', version: 2,
  }
  render(IncomeForm, { people, accounts, initial: income, onsubmit })
  await fireEvent.input(screen.getByLabelText("Take-home amount each time you're paid"), { target: { value: '2,100' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save income' }))
  await vi.waitFor(() => expect(onsubmit).toHaveBeenCalledWith({ net_amount: '2100.00' }))
})
