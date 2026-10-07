import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { stubApi } from '../../components/forms/helpers'
import { router } from '../../lib/router.svelte'
import Household from './Household.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

// The forms stay disabled until the household has loaded; wait for that like a user would.
async function renderLoaded() {
  render(Household)
  await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Save household' })).toBeEnabled())
}

it('lists people and adds a new person', async () => {
  const people: unknown[] = [{ id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 }]
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.startsWith('/api/household/timeline')) return json({ entries: [] })
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
  await renderLoaded()
  expect(await screen.findByText('Alex Example')).toBeInTheDocument()
  await fireEvent.input(screen.getByLabelText('Name'), { target: { value: 'Kid A' } })
  await fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'child' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add person' }))
  expect(await screen.findByText('Kid A')).toBeInTheDocument()
})

it('shows the friendly error for a full postcode', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.startsWith('/api/household/timeline')) return json({ entries: [] })
    if (url === '/api/household' && init?.method === 'PATCH')
      return json({ detail: 'Just the first part of your postcode, please (for example LS6).' }, 422)
    if (url === '/api/household') return json({ nation: null, postcode_district: null, currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 })
    return json({ people: [] })
  }))
  await renderLoaded()
  await fireEvent.input(await screen.findByLabelText('Postcode district'), { target: { value: 'LS6 2AB' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save household' }))
  expect(await screen.findByText(/first part of your postcode/)).toBeInTheDocument()
})

it('rejects an invalid birth year without sending it', async () => {
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url.startsWith('/api/household/timeline')) return json({ entries: [] })
    if (url === '/api/household') return json({ nation: null, postcode_district: null, currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 })
    return json({ people: [] })
  })
  vi.stubGlobal('fetch', fetchMock)
  await renderLoaded()
  await fireEvent.input(await screen.findByLabelText('Name'), { target: { value: 'Kid B' } })
  await fireEvent.input(screen.getByLabelText('Birth year (children)'), { target: { value: '20x' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add person' }))
  expect(await screen.findByText('Enter a 4-digit year')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some((c) => (c[1] as RequestInit | undefined)?.method === 'POST')).toBe(false)
})

it('locks the add-person form until the new person is saved', async () => {
  let release: (r: Response) => void = () => {}
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.startsWith('/api/household/timeline')) return json({ entries: [] })
    if (url === '/api/household') return json({ nation: null, postcode_district: null, currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 })
    if (url.startsWith('/api/household/people') && (!init || !init.method || init.method === 'GET')) return json({ people: [] })
    if (url === '/api/household/people' && init?.method === 'POST') {
      return new Promise<Response>((resolve) => { release = resolve })
    }
    return json({ detail: 'unexpected' }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
  await renderLoaded()
  const nameInput = await screen.findByLabelText('Name')
  await vi.waitFor(() => expect(nameInput).toBeEnabled())
  await fireEvent.input(nameInput, { target: { value: 'Alex Example' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Add person' }))
  await vi.waitFor(() => expect(screen.getByLabelText('Name')).toBeDisabled())
  expect(screen.getByRole('button', { name: 'Add person' })).toBeDisabled()
  release(json({ id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 }, 201))
  await vi.waitFor(() => expect(screen.getByLabelText('Name')).toBeEnabled())
  expect(screen.getByLabelText('Name')).toHaveValue('')
  expect(await screen.findByText('Alex Example added.')).toBeInTheDocument()
})

it('keeps the forms locked until the household has loaded', async () => {
  let release: (r: Response) => void = () => {}
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/household/timeline')) return json({ entries: [] })
    if (url === '/api/household') return new Promise<Response>((resolve) => { release = resolve })
    if (url.startsWith('/api/household/people')) return json({ people: [] })
    return json({ detail: 'unexpected' }, 500)
  }))
  render(Household)
  expect(screen.getByLabelText('Postcode district')).toBeDisabled()
  expect(screen.getByLabelText('Name')).toBeDisabled()
  release(json({ nation: 'england', postcode_district: 'LS6', currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 }))
  await vi.waitFor(() => expect(screen.getByLabelText('Postcode district')).toBeEnabled())
  expect(screen.getByLabelText('Postcode district')).toHaveValue('LS6')
})

describe('editing people', () => {
  const hh = { nation: null, postcode_district: null, currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 }
  const alex = { id: 'p_1', display_name: 'Alex Example', role: 'adult', birth_year: null, status: 'active', version: 1 }
  const kid = { id: 'p_2', display_name: 'Kid A', role: 'child', birth_year: null, status: 'active', version: 1 }
  afterEach(() => { router.hash = '' })

  it("adds a child's birth year through Edit (PATCH with the version)", async () => {
    let people = [alex, kid]
    const calls = stubApi((url, method, body) => {
      if (url.startsWith('/api/household/timeline')) return json({ entries: [] })
      if (url === '/api/household') return json(hh)
      if (url === '/api/household/people' && method === 'GET') return json({ people })
      if (url === '/api/household/people/p_2' && method === 'PATCH') {
        const updated = { ...kid, ...body.changes, version: 2 }
        people = [alex, updated]
        return json(updated)
      }
    })
    await renderLoaded()
    await fireEvent.click(await screen.findByRole('button', { name: 'Edit Kid A' }))
    expect(screen.queryByRole('button', { name: 'Add person' })).toBeNull()
    expect(screen.getByLabelText('Name')).toHaveValue('Kid A')
    await fireEvent.input(screen.getByLabelText('Birth year (children)'), { target: { value: '2019' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Save person' }))
    expect(await screen.findByText('Kid A saved.')).toBeInTheDocument()
    expect(screen.getByText(/born 2019/)).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ changes: { birth_year: 2019 }, expected_version: 1 })
  })

  it('a stale edit gets the conflict message and the form restarts from what is stored', async () => {
    let people = [alex, kid]
    const calls = stubApi((url, method) => {
      if (url.startsWith('/api/household/timeline')) return json({ entries: [] })
      if (url === '/api/household') return json(hh)
      if (url === '/api/household/people' && method === 'GET') return json({ people })
      if (url === '/api/household/people/p_2' && method === 'PATCH') {
        people = [alex, { ...kid, display_name: 'Kid B', version: 3 }]
        return json({ detail: 'This was changed somewhere else. Reload and try again.', current_version: 3 }, 409)
      }
    })
    await renderLoaded()
    await fireEvent.click(await screen.findByRole('button', { name: 'Edit Kid A' }))
    await fireEvent.input(screen.getByLabelText('Birth year (children)'), { target: { value: '2019' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Save person' }))
    expect(await screen.findByText('This was changed somewhere else. Reload and try again.')).toBeInTheDocument()
    await vi.waitFor(() => expect(screen.getByLabelText('Name')).toHaveValue('Kid B'))
    expect(screen.getByLabelText('Birth year (children)')).toHaveValue('')
    expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(1)
  })

  it('a prompt link to #person-<id> brings that person into view with their controls', async () => {
    router.hash = '#person-p_1'
    stubApi((url) => {
      if (url.startsWith('/api/household/timeline')) return json({ entries: [] })
      if (url === '/api/household') return json(hh)
      if (url === '/api/household/people') return json({ people: [alex, kid] })
    })
    await renderLoaded()
    const row = document.getElementById('person-p_1')!
    await vi.waitFor(() => expect(document.activeElement).toBe(row))
    expect(within(row).getByLabelText('Work status for Alex Example')).toBeInTheDocument()
    expect(within(row).getByLabelText('Income band for Alex Example (optional)')).toBeInTheDocument()
    // A child has no work or income questions, but can be edited for the birth-year prompt.
    const child = document.getElementById('person-p_2')!
    expect(within(child).queryByLabelText(/Work status/)).toBeNull()
    expect(within(child).getByRole('button', { name: 'Edit Kid A' })).toBeInTheDocument()
  })

  it('records "Prefer not to say" as an answer, and a failed change shows what is really stored', async () => {
    const band = { id: 7, subject_type: 'person', subject_id: 'p_1', attribute: 'income_band', value: '12570_50270', valid_from: '2020-01-01', valid_to: null, source: 'user', version: 1 }
    let fail = false
    const calls = stubApi((url, method) => {
      if (url.startsWith('/api/household/timeline') && method === 'GET') return json({ entries: [band] })
      if (url === '/api/household/timeline' && method === 'POST') {
        return fail ? json({ detail: 'This was changed somewhere else. Reload and try again.', current_version: 2 }, 409) : json({ ...band, id: 8 }, 201)
      }
      if (url === '/api/household') return json(hh)
      if (url === '/api/household/people') return json({ people: [alex] })
    })
    await renderLoaded()
    const select = await screen.findByLabelText('Income band for Alex Example (optional)')
    await vi.waitFor(() => expect(select).toHaveValue('12570_50270'))
    await vi.waitFor(() => expect(select).toBeEnabled())
    await fireEvent.change(select, { target: { value: 'prefer_not_to_say' } })
    expect(await screen.findByText('Alex Example: income band saved.')).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ attribute: 'income_band', value: 'prefer_not_to_say', expected_current: '12570_50270' })
    fail = true
    await vi.waitFor(() => expect(select).toBeEnabled())
    await fireEvent.change(select, { target: { value: 'over_125140' } })
    expect(await screen.findByText('This was changed somewhere else. Reload and try again.')).toBeInTheDocument()
    await vi.waitFor(() => expect(select).toHaveValue('12570_50270'))
  })
})
