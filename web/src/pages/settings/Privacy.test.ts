import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Privacy from './Privacy.svelte'

afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } })

function setup() {
  const calls: Array<[string, RequestInit | undefined]> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push([url, init])
    if (url === '/api/settings') return json({ settings: [
      { key: 'privacy.local_only', value: false, default: false, version: 0, description: '' },
      { key: 'privacy.pseudonymise', value: false, default: false, version: 0, description: '' },
      { key: 'privacy.research_lookups', value: false, default: false, version: 0, description: '' },
      { key: 'privacy.live_market_data', value: true, default: true, version: 0, description: '' },
      { key: 'privacy.datapack_updates', value: true, default: true, version: 0, description: '' },
      { key: 'privacy.hidden_names', value: ['Acme Lettings'], default: [], version: 2, description: '' },
    ] })
    if (url.startsWith('/api/privacy/log')) return json({ entries: [
      { id: 1, ts: '2026-10-07T10:00:00Z', purpose: 'llm', task: 'coach', connection_id: 'c1', destination: 'api.openai.com',
        method: 'POST', path: '/v1/chat/completions', bytes_out: 0, bytes_in: 0, status: null, redactions: 0, outcome: 'blocked', note: 'Local only is on' },
      { id: 2, ts: '2026-07-31T23:30:00Z', purpose: 'llm', task: 'coach', connection_id: 'c1', destination: 'api.example.com',
        method: 'POST', path: '/v1/chat/completions', bytes_out: 2048, bytes_in: 512, status: 200, redactions: 3, outcome: 'sent', note: null },
    ] })
    if (url === '/api/settings/privacy.local_only') return json({ key: 'privacy.local_only', value: true, default: false, version: 1, description: '' })
    if (url === '/api/settings/privacy.hidden_names') return json({ key: 'privacy.hidden_names', value: ['Acme Lettings', 'Bob'], default: [], version: 3, description: '' })
    return json({ detail: 'unexpected' }, 500)
  }))
  return calls
}

it('toggles Local only and shows blocked calls', async () => {
  const calls = setup()
  render(Privacy)
  expect(await screen.findByText('api.openai.com')).toBeInTheDocument()
  expect(screen.getByText('blocked')).toBeInTheDocument()
  await fireEvent.click(screen.getByLabelText('Local only'))
  const patch = calls.find(([u, i]) => u === '/api/settings/privacy.local_only' && i?.method === 'PATCH')!
  expect(JSON.parse(patch[1]!.body as string)).toEqual({ value: true, expected_version: 0 })
})

it('saves names to hide one per line with the version', async () => {
  const calls = setup()
  render(Privacy)
  const box = (await screen.findByLabelText('Names to hide')) as HTMLTextAreaElement
  await vi.waitFor(() => expect(box).toBeEnabled())
  expect(box.value).toBe('Acme Lettings')
  await fireEvent.input(box, { target: { value: 'Acme Lettings\n  Bob \n\nBob' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save names' }))
  expect(await screen.findByText('Names saved.')).toBeInTheDocument()
  const patch = calls.find(([u, i]) => u === '/api/settings/privacy.hidden_names' && i?.method === 'PATCH')!
  expect(JSON.parse(patch[1]!.body as string)).toEqual({ value: ['Acme Lettings', 'Bob'], expected_version: 2 })
})


it('shows the redaction count, UK times and the best-effort caveat', async () => {
  setup()
  render(Privacy)
  const row = (await screen.findByText('api.example.com')).closest('tr')!
  const cells = [...row.querySelectorAll('td')].map((c) => c.textContent)
  expect(cells[6]).toBe('3')
  expect(cells[0]).toContain('01/08/2026')  // 23:30 UTC on 31 July is 00:30 in UK summer time
  expect(screen.getByText(/Best-effort: some formats may slip through/)).toBeInTheDocument()
  expect(screen.getByText(/not what was sent/)).toBeInTheDocument()
})
