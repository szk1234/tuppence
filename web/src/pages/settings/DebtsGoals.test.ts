import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import { json, stubApi } from '../../components/forms/helpers'
import Debts from './Debts.svelte'
import Goals from './Goals.svelte'

afterEach(() => vi.unstubAllGlobals())

it('settles a debt and reports details removed on a kind change', async () => {
  const debt = (over: Record<string, unknown> = {}) => ({
    id: 'd_1', kind: 'car_finance_pcp', lender: 'Motor Co', person_id: null, balance: '8000.00', balance_date: '2026-10-01', apr: 7.9,
    monthly_payment: null, end_date: null, student_loan_plan: null, details: { balloon: '4000.00' }, car_finance_redress_window: false,
    status: 'active', version: 1, ...over,
  })
  let current = debt()
  const calls = stubApi((url, method) => {
    if (url === '/api/household/people') return json({ people: [] })
    if (url.startsWith('/api/debts?')) return json({ debts: [current] })
    if (url === '/api/debts/d_1' && method === 'PATCH') { current = debt({ kind: 'personal_loan', details: {}, details_removed: ['balloon'], version: 2 }); return json(current) }
    if (url === '/api/debts/d_1/settle') { current = debt({ status: 'settled', version: 3 }); return json(current) }
  })
  render(Debts)
  await fireEvent.click(await screen.findByRole('button', { name: 'Edit Motor Co' }))
  await fireEvent.change(screen.getByLabelText('Type of debt'), { target: { value: 'personal_loan' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save debt' }))
  expect(await screen.findByText(/Details that no longer apply were removed: balloon/)).toBeInTheDocument()
  expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ changes: { kind: 'personal_loan' }, expected_version: 1 })
  await fireEvent.click(screen.getByRole('button', { name: 'Settle Motor Co' }))
  expect(await screen.findByRole('heading', { name: 'Settled' })).toBeInTheDocument()
})

it('offers the emergency fund suggestion and adds it', async () => {
  const goals: unknown[] = []
  const calls = stubApi((url, method, body) => {
    if (url.startsWith('/api/goals?')) return json({ goals })
    if (url === '/api/goals/suggestions') return json({ emergency_fund: goals.length === 0 })
    if (url === '/api/goals' && method === 'POST') {
      const g = { id: 'g_1', saved_amount: '0.00', target_amount: null, target_date: null, status: 'active', version: 1, ...body }
      goals.push(g); return json(g, 201)
    }
  })
  render(Goals)
  await fireEvent.click(await screen.findByRole('button', { name: 'Add emergency fund goal' }))
  expect(await screen.findByText('Emergency fund added.')).toBeInTheDocument()
  expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ name: 'Emergency fund', kind: 'emergency_fund', priority: 1 })
  expect(screen.queryByRole('button', { name: 'Add emergency fund goal' })).toBeNull()
})

it('the Goals page still loads when the suggestions call fails, and the suggestion keeps a typed goal', async () => {
  const goal = { id: 'g_1', name: 'Holiday', kind: 'holiday', saved_amount: '0.00', target_amount: null, target_date: null, priority: 2, status: 'active', version: 1 }
  stubApi((url) => {
    if (url.startsWith('/api/goals?')) return json({ goals: [goal] })
    if (url === '/api/goals/suggestions') return json({ detail: 'boom' }, 500)
  })
  render(Goals)
  expect(await screen.findByText('Holiday')).toBeInTheDocument()
  expect(await screen.findByLabelText('Goal name')).toBeInTheDocument() // the add form loads too
  expect(screen.queryByText('boom')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Add emergency fund goal' })).toBeNull()
})

it('accepting the emergency fund suggestion on Goals does not clear the form', async () => {
  const goals: unknown[] = []
  stubApi((url, method, body) => {
    if (url.startsWith('/api/goals?')) return json({ goals })
    if (url === '/api/goals/suggestions') return json({ emergency_fund: true })
    if (url === '/api/goals' && method === 'POST') {
      const g = { id: 'g_1', saved_amount: '0.00', target_amount: null, target_date: null, status: 'active', version: 1, ...body }
      goals.push(g); return json(g, 201)
    }
  })
  render(Goals)
  await fireEvent.input(await screen.findByLabelText('Goal name'), { target: { value: 'House deposit' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add emergency fund goal' }))
  expect(await screen.findByText('Emergency fund added.')).toBeInTheDocument()
  expect(screen.getByLabelText('Goal name')).toHaveValue('House deposit')
})
