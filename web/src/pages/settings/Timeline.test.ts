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

it('removes an entry with its version', async () => {
  const calls = stubApi((url, method) => {
    if (url === '/api/household/people') return json({ people: [person] })
    if (url.startsWith('/api/household/timeline?subject_type=household')) return json({ entries: [] })
    if (url.startsWith('/api/household/timeline?subject_type=person')) return json({ entries: [entry(7, 'income_band', '12570_50270', '2024-04-01')] })
    if (method === 'DELETE') return json(undefined, 204) as never
  })
  render(Timeline)
  await fireEvent.click(await screen.findByRole('button', { name: /Remove Income band change from 1 Apr 2024 for Alex Example/ }))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
  expect(calls.find((c) => c.method === 'DELETE')?.url).toBe('/api/household/timeline/7?expected_version=1')
})
