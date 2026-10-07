import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, it, vi } from 'vitest'
import type { Debt } from '../../lib/types'
import DebtForm from './DebtForm.svelte'

const debt = (over: Partial<Debt> = {}): Debt => ({
  id: 'd_1', kind: 'car_finance_pcp', lender: 'Motor Co', person_id: null, balance: '8000.00', balance_date: '2026-10-01', apr: 7.9,
  monthly_payment: '250.00', end_date: null, student_loan_plan: null,
  details: { agreement_start: '2022-03-01', via_broker: true, balloon: '4000.00', total_payable: '15000.00', annual_mileage: 8000 },
  car_finance_redress_window: true, status: 'active', version: 4, ...over,
})

it('shows kind-specific sections', async () => {
  render(DebtForm, { people: [], onsubmit: async () => true })
  const kind = screen.getByLabelText('Type of debt')
  expect(screen.queryByLabelText('Student loan plan')).toBeNull()
  await fireEvent.change(kind, { target: { value: 'student_loan' } })
  expect(screen.getByLabelText('Student loan plan')).toBeInTheDocument()
  await fireEvent.change(kind, { target: { value: 'car_finance_pcp' } })
  expect(screen.getByLabelText('Balloon payment')).toBeInTheDocument()
  expect(screen.getByLabelText('Annual mileage allowance')).toBeInTheDocument()
  await fireEvent.change(kind, { target: { value: 'car_finance_hp' } })
  expect(screen.queryByLabelText('Balloon payment')).toBeNull()
  await fireEvent.change(kind, { target: { value: 'mortgage' } })
  expect(screen.getByLabelText('Fixed rate ends')).toBeInTheDocument()
  await fireEvent.change(kind, { target: { value: 'informal' } })
  expect(screen.getByLabelText('Direction')).toBeInTheDocument()
})

it('creates a car finance debt with details', async () => {
  const onsubmit = vi.fn(async () => true)
  render(DebtForm, { people: [], onsubmit })
  await fireEvent.change(screen.getByLabelText('Type of debt'), { target: { value: 'car_finance_pcp' } })
  await fireEvent.input(screen.getByLabelText('Lender'), { target: { value: 'Motor Co' } })
  await fireEvent.input(screen.getByLabelText('Current balance'), { target: { value: '£8,000' } })
  await fireEvent.input(screen.getByLabelText('Interest rate (APR %)'), { target: { value: '7.9' } })
  await fireEvent.change(screen.getByLabelText('Arranged through a broker or dealer'), { target: { value: 'yes' } })
  await fireEvent.input(screen.getByLabelText('Balloon payment'), { target: { value: '4000' } })
  await fireEvent.input(screen.getByLabelText('Annual mileage allowance'), { target: { value: '8000' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add debt' }))
  await vi.waitFor(() => expect(onsubmit).toHaveBeenCalled())
  expect(onsubmit).toHaveBeenCalledWith({
    kind: 'car_finance_pcp', lender: 'Motor Co', balance: '8000.00', apr: 7.9,
    details: { via_broker: true, balloon: '4000.00', annual_mileage: 8000 },
  })
})

it('does not send an empty rate as 0 and asks for a direction on informal debts', async () => {
  const onsubmit = vi.fn(async () => true)
  render(DebtForm, { people: [], onsubmit })
  await fireEvent.input(screen.getByLabelText('Lender'), { target: { value: 'Bank' } })
  await fireEvent.input(screen.getByLabelText('Current balance'), { target: { value: '100' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add debt' }))
  await vi.waitFor(() => expect(onsubmit).toHaveBeenCalledWith({ kind: 'personal_loan', lender: 'Bank', balance: '100.00' }))
  await fireEvent.change(screen.getByLabelText('Type of debt'), { target: { value: 'informal' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add debt' }))
  expect(await screen.findByText('Say whether you owe this or are owed it.')).toBeInTheDocument()
})

it('sends only the changed details on edit, and null to clear one', async () => {
  const onsubmit = vi.fn(async () => true)
  render(DebtForm, { people: [], initial: debt(), onsubmit })
  await fireEvent.input(screen.getByLabelText('Balloon payment'), { target: { value: '4,500' } })
  await fireEvent.input(screen.getByLabelText('Annual mileage allowance'), { target: { value: '' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save debt' }))
  await vi.waitFor(() => expect(onsubmit).toHaveBeenCalledWith({ details: { balloon: '4500.00', annual_mileage: null } }))
})

it('sends nothing for details when only a top-level field changed', async () => {
  const onsubmit = vi.fn(async () => true)
  render(DebtForm, { people: [], initial: debt(), onsubmit })
  await fireEvent.input(screen.getByLabelText('Current balance'), { target: { value: '7500' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save debt' }))
  await vi.waitFor(() => expect(onsubmit).toHaveBeenCalledWith({ balance: '7500.00' }))
})
