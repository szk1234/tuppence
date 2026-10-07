import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import { json, stubApi } from '../../components/forms/helpers'
import Timeline from './Timeline.svelte'

afterEach(() => vi.unstubAllGlobals())

const person = { id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 }
const entry = (id: number, attribute: string, value: unknown, from: string, to: string | null = null) =>
  ({ id, subject_type: 'person', subject_id: 'p_1', attribute, value, valid_from: from, valid_to: to, source: 'user', version: 1 })

it('lists history and records a change with expected_current', async () => {
  let entries = [entry(1, 'employment_status', 'employed', '2020-01-01')]
  const calls = stubApi((url, method, body) => {
    if (url === '/api/household/people') return json({ people: [person] })
    if (url.startsWith('/api/household/timeline?subject_type=household')) return json({ entries: [] })
    if (url.startsWith('/api/household/timeline?subject_type=person')) return json({ entries })
    if (url === '/api/household/timeline' && method === 'POST') { entries = [...entries, entry(2, body.attribute, body.value, body.valid_from)]; return json(entries[1], 201) }
  })
  render(Timeline)
  expect(await screen.findByText(/Employed · from 1 Jan 2020/)).toBeInTheDocument()
  await vi.waitFor(() => expect(screen.getByLabelText('Who is this about?')).toBeEnabled())
  await fireEvent.change(screen.getByLabelText('Who is this about?'), { target: { value: 'person:p_1' } })
  await fireEvent.change(screen.getByLabelText('What changed?'), { target: { value: 'employment_status' } })
  await fireEvent.change(screen.getByLabelText('New value'), { target: { value: 'self_employed' } })
  await fireEvent.input(screen.getByLabelText('From'), { target: { value: '2026-09-01' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add change' }))
  expect(await screen.findByText(/Work status updated from 1 Sept? 2026/)).toBeInTheDocument()
  expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
    subject_type: 'person', subject_id: 'p_1', attribute: 'employment_status', value: 'self_employed', valid_from: '2026-09-01', expected_current: 'employed',
  })
  expect(await screen.findByText(/Self-employed · from/)).toBeInTheDocument()
})

it('removes an entry with its version, only after confirming', async () => {
  const calls = stubApi((url, method) => {
    if (url === '/api/household/people') return json({ people: [person] })
    if (url.startsWith('/api/household/timeline?subject_type=household')) return json({ entries: [] })
    if (url.startsWith('/api/household/timeline?subject_type=person')) return json({ entries: [entry(7, 'income_band', '12570_50270', '2024-04-01')] })
    if (method === 'DELETE') return json(undefined, 204) as never
  })
  render(Timeline)
  const remove = await screen.findByRole('button', { name: /Remove Income band change from 1 Apr 2024 for Alex Example/ })
  await fireEvent.click(remove)
  expect(await screen.findByText(/Remove Alex Example's income band change from 1 Apr 2024\?/)).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
  await fireEvent.click(remove)
  await fireEvent.click(await screen.findByRole('button', { name: 'Remove change' }))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
  expect(calls.find((c) => c.method === 'DELETE')?.url).toBe('/api/household/timeline/7?expected_version=1')
})

it('sends a housing cost as pounds text and bedrooms as a whole number, and refuses "1 450"', async () => {
  const calls = stubApi((url, method, body) => {
    if (url === '/api/household/people') return json({ people: [] })
    if (url.startsWith('/api/household/timeline?')) return json({ entries: [] })
    if (url === '/api/household/timeline' && method === 'POST') return json({ ...entry(9, body.attribute, body.value, body.valid_from), subject_type: 'household', subject_id: '1' }, 201)
  })
  render(Timeline)
  await vi.waitFor(() => expect(screen.getByLabelText('What changed?')).toBeEnabled())
  await fireEvent.change(screen.getByLabelText('What changed?'), { target: { value: 'housing_monthly_pence' } })
  await fireEvent.input(screen.getByLabelText('New value'), { target: { value: '1 450' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add change' }))
  expect(await screen.findByText('Enter an amount like 1450 or 1,450.50.', { selector: '[role="alert"]:not(.warn)' })).toBeInTheDocument()
  expect(calls.some((c) => c.method === 'POST')).toBe(false)
  await fireEvent.input(screen.getByLabelText('New value'), { target: { value: '1,450' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add change' }))
  await vi.waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1))
  expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ attribute: 'housing_monthly_pence', value: '1450.00', expected_current: null })
  await vi.waitFor(() => expect(screen.getByLabelText('What changed?')).toBeEnabled())
  await fireEvent.change(screen.getByLabelText('What changed?'), { target: { value: 'bedrooms' } })
  await fireEvent.input(screen.getByLabelText('New value'), { target: { value: '21' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add change' }))
  expect(await screen.findByText('Enter a whole number from 0 to 20.')).toBeInTheDocument()
  await fireEvent.input(screen.getByLabelText('New value'), { target: { value: '3' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add change' }))
  await vi.waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2))
  expect(calls.filter((c) => c.method === 'POST')[1].body).toMatchObject({ attribute: 'bedrooms', value: 3 })
})
