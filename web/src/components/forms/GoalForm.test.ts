import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, it, vi } from 'vitest'
import GoalForm from './GoalForm.svelte'

it('suggests an emergency fund and adds it in one click', async () => {
  const onsubmit = vi.fn(async () => true)
  render(GoalForm, { suggestEmergencyFund: true, onsubmit })
  expect(screen.getByText(/No emergency fund yet — most people aim for 3–6 months of essential spending/)).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Add emergency fund goal' }))
  await vi.waitFor(() => expect(onsubmit).toHaveBeenCalledWith({ name: 'Emergency fund', kind: 'emergency_fund', priority: 1 }))
})

it('hides the suggestion when there is nothing to suggest', () => {
  render(GoalForm, { suggestEmergencyFund: false, onsubmit: async () => true })
  expect(screen.queryByRole('button', { name: 'Add emergency fund goal' })).toBeNull()
})

it('sends blank optional fields as absent rather than zero', async () => {
  const onsubmit = vi.fn(async () => true)
  render(GoalForm, { onsubmit })
  await fireEvent.input(screen.getByLabelText('Goal name'), { target: { value: 'Holiday' } })
  await fireEvent.change(screen.getByLabelText('Type of goal'), { target: { value: 'holiday' } })
  await fireEvent.input(screen.getByLabelText('Target amount (optional)'), { target: { value: '2,000' } })
  await fireEvent.change(screen.getByLabelText('Priority'), { target: { value: '3' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add goal' }))
  await vi.waitFor(() => expect(onsubmit).toHaveBeenCalledWith({ name: 'Holiday', kind: 'holiday', target_amount: '2000.00', saved_amount: '0.00', priority: 3 }))
})

it('rejects a target that is not an amount', async () => {
  const onsubmit = vi.fn(async () => true)
  render(GoalForm, { onsubmit })
  await fireEvent.input(screen.getByLabelText('Goal name'), { target: { value: 'Car' } })
  await fireEvent.input(screen.getByLabelText('Target amount (optional)'), { target: { value: 'lots' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add goal' }))
  expect(await screen.findByText('Enter the target like 5000 or 5,000.')).toBeInTheDocument()
  expect(onsubmit).not.toHaveBeenCalled()
})
