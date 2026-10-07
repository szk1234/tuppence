import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Household from './Household.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

it('lists people and adds a new person', async () => {
  const people: unknown[] = [{ id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 }]
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/household') return json({ nation: null, postcode_district: null, currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 })
    if (url.startsWith('/api/household/people') && (!init || !init.method || init.method === 'GET')) return json({ people })
    if (url === '/api/household/people' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string)
      const p = { id: 'p_2', status: 'active', version: 1, birth_year: null, ...body }
      people.push(p)
      return json(p, 201)
    }
    return json({ detail: 'unexpected' }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(Household)
  expect(await screen.findByText('Alex Example')).toBeInTheDocument()
  await fireEvent.input(screen.getByLabelText('Name'), { target: { value: 'Kid A' } })
  await fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'child' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add person' }))
  expect(await screen.findByText('Kid A')).toBeInTheDocument()
})

it('shows the friendly error for a full postcode', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/household' && init?.method === 'PATCH')
      return json({ detail: 'Just the first part of your postcode, please (for example LS6).' }, 422)
    if (url === '/api/household') return json({ nation: null, postcode_district: null, currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 })
    return json({ people: [] })
  }))
  render(Household)
  await fireEvent.input(await screen.findByLabelText('Postcode district'), { target: { value: 'LS6 2AB' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save household' }))
  expect(await screen.findByText(/first part of your postcode/)).toBeInTheDocument()
})

it('rejects an invalid birth year without sending it', async () => {
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url === '/api/household') return json({ nation: null, postcode_district: null, currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 })
    return json({ people: [] })
  })
  vi.stubGlobal('fetch', fetchMock)
  render(Household)
  await fireEvent.input(await screen.findByLabelText('Name'), { target: { value: 'Kid B' } })
  await fireEvent.input(screen.getByLabelText('Birth year (children)'), { target: { value: '20x' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add person' }))
  expect(await screen.findByText('Enter a 4-digit year')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some((c) => (c[1] as RequestInit | undefined)?.method === 'POST')).toBe(false)
})

it('locks the add-person form until the new person is saved', async () => {
  let release: (r: Response) => void = () => {}
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/household') return json({ nation: null, postcode_district: null, currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 })
    if (url.startsWith('/api/household/people') && (!init || !init.method || init.method === 'GET')) return json({ people: [] })
    if (url === '/api/household/people' && init?.method === 'POST') {
      return new Promise<Response>((resolve) => { release = resolve })
    }
    return json({ detail: 'unexpected' }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(Household)
  const nameInput = await screen.findByLabelText('Name')
  await fireEvent.input(nameInput, { target: { value: 'Alex Example' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add person' }))
  await vi.waitFor(() => expect(screen.getByLabelText('Name')).toBeDisabled())
  expect(screen.getByRole('button', { name: 'Add person' })).toBeDisabled()
  release(json({ id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 }, 201))
  await vi.waitFor(() => expect(screen.getByLabelText('Name')).toBeEnabled())
  expect(screen.getByLabelText('Name')).toHaveValue('')
  expect(await screen.findByText('Alex Example added.')).toBeInTheDocument()
})
