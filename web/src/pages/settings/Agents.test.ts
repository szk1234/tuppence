import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import { session } from '../../lib/session.svelte'
import Agents from './Agents.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const researcher = (max: number, version: number, overridden: string[] = []) => ({
  manifest: {
    name: 'researcher', description: 'Identifies unknown merchants.', enabled: true, task: 'research', model_chain: [],
    budgets: { max_llm_calls: 60, max_tokens: 300000, max_gbp: 0.5, max_seconds: 600 },
    thresholds: {}, limits: { max_merchants_per_run: max, max_tool_calls_per_merchant: 5, max_page_fetches_per_merchant: 2 },
    questions: null, triggers: [], tools: [], tone: 'plain', prompt_template: null,
  },
  overridden, user_file_error: null, version,
})

it('edits a limit and shows it as overridden', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/config/agents') return json({ agents: [researcher(20, 0)], preset: { value: 'balanced', version: 0 } })
    if (url === '/api/config/presets') return json({ presets: ['balanced', 'frugal', 'thorough'] })
    if (url === '/api/config/agents/researcher' && init?.method === 'PATCH') {
      const body = JSON.parse(init.body as string)
      expect(body).toEqual({ changes: { limits: { max_merchants_per_run: 12 } }, expected_version: 0 })
      return json(researcher(12, 1, ['limits.max_merchants_per_run']))
    }
    return json({ detail: 'unexpected' }, 500)
  }))
  render(Agents)
  const card = await screen.findByRole('region', { name: 'researcher' })
  const input = within(card).getByLabelText('max_merchants_per_run')
  await fireEvent.input(input, { target: { value: '12' } })
  await fireEvent.click(within(card).getByRole('button', { name: 'Save researcher' }))
  expect(await within(card).findByText('Changed by you')).toBeInTheDocument()
})

it('flags a cleared number, does not send it, and disables Save', async () => {
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url === '/api/config/agents') return json({ agents: [researcher(20, 0)], preset: { value: 'balanced', version: 0 } })
    if (url === '/api/config/presets') return json({ presets: ['balanced'] })
    return json({ detail: 'unexpected' }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(Agents)
  const card = await screen.findByRole('region', { name: 'researcher' })
  const input = within(card).getByLabelText('max_merchants_per_run')
  await fireEvent.input(input, { target: { value: '12' } })
  expect(within(card).getByRole('button', { name: 'Save researcher' })).toBeEnabled()
  await fireEvent.input(input, { target: { value: '' } })
  expect(await within(card).findByText('Enter a number')).toBeInTheDocument()
  const save = within(card).getByRole('button', { name: 'Save researcher' })
  expect(save).toBeDisabled()
  await fireEvent.click(save)
  expect(fetchMock.mock.calls.some((c) => (c[1] as RequestInit | undefined)?.method === 'PATCH')).toBe(false)
})

it('a household member in server mode sees the agents read-only, with no way to change them', async () => {
  session.mode = 'server'; session.user = { username: 'sam', is_admin: false }
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/config/agents') return json({ agents: [researcher(20, 0, ['limits.max_merchants_per_run'])], preset: { value: 'balanced', version: 0 } })
    if (url === '/api/config/presets') return json({ presets: ['balanced', 'frugal'] })
    return json({ detail: 'unexpected' }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
  try {
    render(Agents)
    const card = await screen.findByRole('region', { name: 'researcher' })
    expect(screen.getByText('Only the household admin can change agent settings.')).toBeInTheDocument()
    expect(screen.getByLabelText('Preset')).toBeDisabled()
    expect(within(card).getByLabelText('max_merchants_per_run')).toBeDisabled()
    expect(within(card).getByLabelText('Enabled')).toBeDisabled()
    expect(within(card).queryByRole('button', { name: 'Save researcher' })).toBeNull()
    expect(within(card).queryByRole('button', { name: 'Reset' })).toBeNull()
    expect(within(card).getByText('Changed by you')).toBeInTheDocument()
  } finally { session.mode = 'local'; session.user = null }
})
