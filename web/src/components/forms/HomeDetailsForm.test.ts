import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import HomeDetailsForm from './HomeDetailsForm.svelte'
import { json, stubApi } from './helpers'

afterEach(() => vi.unstubAllGlobals())

const entry = (attribute: string, value: unknown, valid_from: string, id = 1) =>
  ({ id, subject_type: 'household', subject_id: '1', attribute, value, valid_from, valid_to: null, source: 'user', version: 2 })

async function loaded() {
  render(HomeDetailsForm)
  await vi.waitFor(() => expect(screen.getByLabelText('Housing')).toBeEnabled())
}

it('writes changed attributes with a from date and expected_current', async () => {
  let entries = [entry('housing_tenure', 'renting', '2020-01-01'), entry('housing_monthly_pence', '1200.00', '2020-01-01', 2)]
  const calls = stubApi((url, method) => {
    if (url.startsWith('/api/household/timeline') && method === 'GET') return json({ entries })
    if (url === '/api/household/timeline' && method === 'POST') { entries = [...entries]; return json({}, 201) }
  })
  await loaded()
  expect(screen.getByLabelText('Housing')).toHaveValue('renting')
  expect(screen.getByLabelText('Monthly housing cost')).toHaveValue('1200.00')
  await fireEvent.input(screen.getByLabelText('Monthly housing cost'), { target: { value: '£1,350' } })
  await fireEvent.input(screen.getByLabelText('Bedrooms'), { target: { value: '2' } })
  await fireEvent.input(screen.getByLabelText('In place from'), { target: { value: '2026-09-01' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save home details' }))
  expect(await screen.findByText('Home details saved.')).toBeInTheDocument()
  const posts = calls.filter((c) => c.method === 'POST').map((c) => c.body)
  expect(posts).toEqual([
    { subject_type: 'household', subject_id: '1', attribute: 'housing_monthly_pence', value: '1350.00', valid_from: '2026-09-01', expected_current: '1200.00' },
    { subject_type: 'household', subject_id: '1', attribute: 'bedrooms', value: 2, valid_from: '2026-09-01', expected_current: null },
  ])
})

it('shows the conflict message from the server', async () => {
  stubApi((url, method) => {
    if (method === 'GET') return json({ entries: [] })
    return json({ detail: 'This was changed somewhere else. Reload and try again.', current_version: 3 }, 409)
  })
  await loaded()
  await fireEvent.change(screen.getByLabelText('Housing'), { target: { value: 'owned' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save home details' }))
  expect(await screen.findByText('This was changed somewhere else. Reload and try again.')).toBeInTheDocument()
})

it('stays locked until the history has loaded and rejects bad bedrooms', async () => {
  let release: (r: Response) => void = () => {}
  const calls = stubApi((url) => (url.startsWith('/api/household/timeline') ? new Promise<Response>((r) => { release = r }) as never : undefined))
  render(HomeDetailsForm)
  expect(screen.getByLabelText('Housing')).toBeDisabled()
  release(json({ entries: [] }))
  await vi.waitFor(() => expect(screen.getByLabelText('Housing')).toBeEnabled())
  await fireEvent.input(screen.getByLabelText('Bedrooms'), { target: { value: 'two' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save home details' }))
  expect(await screen.findByText('Enter bedrooms as a whole number from 0 to 20.')).toBeInTheDocument()
  expect(calls.some((c) => c.method === 'POST')).toBe(false)
})
