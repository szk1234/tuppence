import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import PayRuleField from './PayRuleField.svelte'
import { json, stubApi } from './helpers'

afterEach(() => vi.unstubAllGlobals())

it('previews the next paydays from the server', async () => {
  const calls = stubApi((url) => (url === '/api/income/preview-rule'
    ? json({ description: 'Last working day of the month', next_dates: ['2026-10-30', '2026-11-30'], calendar_assumed: false }) : undefined))
  render(PayRuleField)
  await fireEvent.change(screen.getByLabelText('How often are you paid?'), { target: { value: 'last_working_day' } })
  expect(await screen.findByText('Next paydays: 30 Oct 2026, 30 Nov 2026')).toBeInTheDocument()
  expect(calls.find((c) => c.url === '/api/income/preview-rule')?.body).toEqual({ pay_rule: { type: 'last_working_day' } })
  expect(screen.queryByText('Set your nation for accurate bank holidays.')).toBeNull()
})

it('asks for the extra details each frequency needs', async () => {
  const calls = stubApi((url) => (url === '/api/income/preview-rule' ? json({ next_dates: ['2026-11-27'], calendar_assumed: true }) : undefined))
  render(PayRuleField)
  const how = screen.getByLabelText('How often are you paid?')
  await fireEvent.change(how, { target: { value: 'fortnightly' } })
  expect(screen.getByLabelText('A recent payday')).toBeInTheDocument()
  expect(calls.filter((c) => c.url === '/api/income/preview-rule')).toHaveLength(0)
  await fireEvent.change(how, { target: { value: 'monthly_day' } })
  await fireEvent.input(screen.getByLabelText('Day of the month (1 to 31)'), { target: { value: '28' } })
  expect(screen.getByLabelText("If it's a weekend or bank holiday")).toBeInTheDocument()
  expect(await screen.findByText('Next paydays: 27 Nov 2026')).toBeInTheDocument()
  expect(screen.getByText('Set your nation for accurate bank holidays.')).toBeInTheDocument()
  await fireEvent.change(how, { target: { value: 'last_weekday' } })
  expect(screen.getByLabelText('Which weekday?')).toBeInTheDocument()
})

it('does not preview an out-of-range day', async () => {
  const calls = stubApi(() => undefined)
  render(PayRuleField)
  await fireEvent.change(screen.getByLabelText('How often are you paid?'), { target: { value: 'monthly_day' } })
  await fireEvent.input(screen.getByLabelText('Day of the month (1 to 31)'), { target: { value: '32' } })
  await new Promise((r) => setTimeout(r, 400))
  expect(calls).toHaveLength(0)
})
