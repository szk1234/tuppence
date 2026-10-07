import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import { session } from '../../lib/session.svelte'
import AI from './AI.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  session.mode = 'local'; session.user = null
})
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } })

const conn = (over: Record<string, unknown> = {}) => ({
  id: 'c1', preset: 'openai', name: 'OpenAI', api_style: 'openai', base_url: 'https://api.openai.com/v1', is_local: false,
  has_key: true, headers: [], needs_notice: true, notice_acknowledged_at: null, enabled: true, version: 3, ...over,
})
const routing = (over: Record<string, unknown> = {}) => ({
  mode: 'simple', mode_version: 0, simple_model: null, simple_version: 0,
  tasks: { coach: { chain: [], local_only: false, version: 0 }, read: { chain: [], local_only: false, version: 0 } }, ...over,
})

function setup(handler: (url: string, init?: RequestInit) => Response | undefined = () => undefined, state: { conn: any; routing: any } = { conn: conn(), routing: routing() }) {
  const calls: Array<[string, RequestInit | undefined]> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push([url, init])
    const custom = handler(url, init)
    if (custom) return custom
    if (url === '/api/llm/presets') return json({ presets: [
      { id: 'openai', label: 'OpenAI', api_style: 'openai', base_url: 'https://api.openai.com/v1', kind: 'cloud', key_required: true },
      { id: 'ollama', label: 'Ollama', api_style: 'openai', base_url: 'http://localhost:11434/v1', kind: 'local', key_required: false },
    ] })
    if (url === '/api/llm/connections') return json({ connections: [state.conn] })
    if (url === '/api/llm/models') return json({ models: [{ connection_id: 'c1', model_id: 'gpt-x', display_name: 'GPT X' }] })
    if (url === '/api/llm/routing') return json(state.routing)
    if (url === '/api/llm/connections/c1/acknowledge-notice') { state.conn = conn({ needs_notice: false, version: 4 }); return json(state.conn) }
    return json({ detail: 'unexpected' }, 500)
  }))
  return calls
}

it('shows the cloud notice, acknowledges it and removes the button', async () => {
  const calls = setup()
  render(AI)
  const card = await screen.findByRole('region', { name: 'OpenAI' })
  expect(within(card).getByText('Internet')).toBeInTheDocument()
  await fireEvent.click(within(card).getByRole('button', { name: "Review what's sent" }))
  expect(await screen.findByText('Before you use OpenAI')).toBeInTheDocument()
  expect(screen.getByText('OpenAI will see the statement text Tuppence sends to it.')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'I understand' }))
  await waitFor(() => expect(within(card).queryByRole('button', { name: "Review what's sent" })).toBeNull())
  const ack = calls.find(([u, i]) => u === '/api/llm/connections/c1/acknowledge-notice' && i?.method === 'POST')!
  expect(JSON.parse(ack[1]!.body as string)).toEqual({ expected_version: 3 })
})

it('Cancel and Escape close the notice without acknowledging', async () => {
  const calls = setup()
  render(AI)
  const card = await screen.findByRole('region', { name: 'OpenAI' })
  await fireEvent.click(within(card).getByRole('button', { name: "Review what's sent" }))
  await fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.queryByText('Before you use OpenAI')).toBeNull())
  await fireEvent.click(within(card).getByRole('button', { name: "Review what's sent" }))
  await fireEvent.keyDown(await screen.findByText('Before you use OpenAI'), { key: 'Escape' })
  await waitFor(() => expect(screen.queryByText('Before you use OpenAI')).toBeNull())
  expect(calls.some(([u]) => u.includes('acknowledge'))).toBe(false)
})

it('sends a message and shows the reply, then shows an error detail', async () => {
  let n = 0
  setup((url) => {
    if (url === '/api/llm/try') return n++ === 0 ? json({ text: 'Hello!' }) : json({ detail: 'Local only is on, and every model for this task is in the cloud.' }, 502)
  })
  render(AI)
  await screen.findByRole('region', { name: 'OpenAI' })
  await fireEvent.input(screen.getByLabelText('Message'), { target: { value: 'Hi' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByText('Hello!')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  expect((await screen.findByText(/Local only is on/)).closest('[role="alert"]')).not.toBeNull()
})

it('shows the notice when /try answers notice_required, then retries', async () => {
  let n = 0
  const calls = setup((url) => {
    if (url === '/api/llm/try') return n++ === 0 ? json({ detail: 'Confirm first.', code: 'notice_required', connection_id: 'c1' }, 409) : json({ text: 'Hi back' })
  })
  render(AI)
  await screen.findByRole('region', { name: 'OpenAI' })
  await fireEvent.input(screen.getByLabelText('Message'), { target: { value: 'Hi' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await fireEvent.click(await screen.findByRole('button', { name: 'I understand' }))
  expect(await screen.findByText('Hi back')).toBeInTheDocument()
  expect(calls.filter(([u]) => u === '/api/llm/try').length).toBe(2)
})

it('tests a connection, refreshes its version and edits without sending a blank key', async () => {
  const calls = setup((url, init) => {
    if (url === '/api/llm/connections/c1/test') return json({ ok: true, reason: 'ok', status: 200, models: [{}, {}], connection: conn({ version: 5 }) })
    if (url === '/api/llm/connections/c1' && init?.method === 'PATCH') return json(conn({ name: 'Work AI', version: 6 }))
  })
  render(AI)
  const card = await screen.findByRole('region', { name: 'OpenAI' })
  await fireEvent.click(within(card).getByRole('button', { name: 'Test' }))
  expect(await within(card).findByText('2 models found')).toBeInTheDocument()
  await fireEvent.click(within(card).getByRole('button', { name: 'Edit' }))
  expect(within(card).getByText(/Changing the address clears the saved API key/)).toBeInTheDocument()
  await fireEvent.input(within(card).getByLabelText('Name'), { target: { value: 'Work AI' } })
  await fireEvent.click(within(card).getByRole('button', { name: 'Save changes' }))
  await waitFor(() => expect(calls.some(([, i]) => i?.method === 'PATCH')).toBe(true))
  const patch = calls.find(([, i]) => i?.method === 'PATCH')!
  expect(JSON.parse(patch[1]!.body as string)).toEqual({ changes: { name: 'Work AI' }, expected_version: 5 })
})

it('detects local servers and adds one; adds a connection from a preset', async () => {
  const calls = setup((url, init) => {
    if (url === '/api/llm/detect') return json({ servers: [{ preset: 'ollama', base_url: 'http://localhost:11434/v1', model_count: 3 }] })
    if (url === '/api/llm/connections' && init?.method === 'POST') return json(conn({ id: 'c2', name: 'Ollama', is_local: true, needs_notice: false }), 201)
  })
  render(AI)
  await screen.findByRole('region', { name: 'OpenAI' })
  await fireEvent.click(screen.getByRole('button', { name: 'Find local AI' }))
  await fireEvent.click(await screen.findByRole('button', { name: /^Add ollama/ }))
  expect(await screen.findByRole('region', { name: 'Ollama' })).toBeInTheDocument()
  await fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'ollama' } })
  expect((screen.getByLabelText('Base URL') as HTMLInputElement).value).toBe('http://localhost:11434/v1')
  expect(screen.queryByLabelText('API key')).toBeNull()
  const post = calls.find(([u, i]) => u === '/api/llm/connections' && i?.method === 'POST')!
  expect(JSON.parse(post[1]!.body as string)).toEqual({ preset: 'ollama', base_url: 'http://localhost:11434/v1' })
})

it('picks the simple model and switches to advanced with a local-only pin', async () => {
  const state = { conn: conn({ needs_notice: false }), routing: routing() }
  const calls = setup((url, init) => {
    if (url === '/api/settings/llm.simple_model' && init?.method === 'PATCH') { state.routing = routing({ simple_model: { connection_id: 'c1', model_id: 'gpt-x' }, simple_version: 1 }); return json({}) }
    if (url === '/api/settings/llm.mode' && init?.method === 'PATCH') { state.routing = routing({ mode: 'advanced', mode_version: 1 }); return json({}) }
    if (url === '/api/llm/routing/tasks/coach' && init?.method === 'PUT') return json(routing({ mode: 'advanced', tasks: { coach: { chain: [], local_only: true, version: 1 }, read: { chain: [], local_only: false, version: 0 } } }))
  }, state)
  render(AI)
  const sel = await screen.findByLabelText('Model for everything')
  expect(within(sel).getByText('OpenAI — gpt-x')).toBeInTheDocument()
  await fireEvent.change(sel, { target: { value: JSON.stringify(['c1', 'gpt-x']) } })
  await waitFor(() => expect(calls.some(([u]) => u === '/api/settings/llm.simple_model')).toBe(true))
  const p = calls.find(([u]) => u === '/api/settings/llm.simple_model')!
  expect(JSON.parse(p[1]!.body as string)).toEqual({ value: { connection_id: 'c1', model_id: 'gpt-x' }, expected_version: 0 })
  await fireEvent.click(screen.getByLabelText('Advanced: choose per task'))
  const task = await screen.findByRole('region', { name: 'Task coach' })
  await fireEvent.click(within(task).getByLabelText('Keep this task on this device'))
  await waitFor(() => expect(calls.some(([u]) => u === '/api/llm/routing/tasks/coach')).toBe(true))
  const put = calls.find(([u]) => u === '/api/llm/routing/tasks/coach')!
  expect(JSON.parse(put[1]!.body as string)).toEqual({ chain: [], local_only: true, expected_version: 0 })
})

it('forgets saved keys after confirmation', async () => {
  const calls = setup((url, init) => { if (url === '/api/llm/secrets/forget' && init?.method === 'POST') return new Response(null, { status: 204 }) })
  render(AI)
  await screen.findByRole('region', { name: 'OpenAI' })
  await fireEvent.click(screen.getByRole('button', { name: 'Forget saved AI keys' }))
  expect(calls.some(([u]) => u === '/api/llm/secrets/forget')).toBe(false)
  await fireEvent.click(await screen.findByRole('button', { name: 'Forget keys' }))
  await waitFor(() => expect(calls.some(([u]) => u === '/api/llm/secrets/forget')).toBe(true))
})

it('reloads connections after a 409 on edit so a retry uses the fresh version', async () => {
  const state = { conn: conn({ version: 3 }), routing: routing() }
  let patches = 0
  const calls = setup((url, init) => {
    if (url === '/api/llm/connections/c1' && init?.method === 'PATCH') {
      if (patches++ === 0) {
        state.conn = conn({ version: 9 })
        return json({ detail: 'This was changed somewhere else. Reload and try again.', current_version: 9 }, 409)
      }
      return json(conn({ name: 'Work AI', version: 10 }))
    }
  }, state)
  render(AI)
  const card = await screen.findByRole('region', { name: 'OpenAI' })
  await fireEvent.click(within(card).getByRole('button', { name: 'Edit' }))
  await fireEvent.input(within(card).getByLabelText('Name'), { target: { value: 'Work AI' } })
  await fireEvent.click(within(card).getByRole('button', { name: 'Save changes' }))
  expect(await screen.findByText('This was changed somewhere else. Reload and try again.')).toBeInTheDocument()
  await waitFor(() => expect(calls.filter(([u]) => u === '/api/llm/connections').length).toBeGreaterThan(1))
  await fireEvent.click(within(card).getByRole('button', { name: 'Save changes' }))
  await waitFor(() => expect(calls.filter(([, i]) => i?.method === 'PATCH').length).toBe(2))
  const second = calls.filter(([, i]) => i?.method === 'PATCH')[1]
  expect(JSON.parse(second[1]!.body as string).expected_version).toBe(9)
})

it('hides write controls for a non-admin in server mode and shows a 403 message', async () => {
  session.mode = 'server'; session.user = { username: 'sam', is_admin: false }
  setup((url, init) => {
    if (url === '/api/settings/llm.mode' && init?.method === 'PATCH') return json({ detail: 'Only the household admin can change AI connections.' }, 403)
  })
  render(AI)
  const card = await screen.findByRole('region', { name: 'OpenAI' })
  expect(within(card).queryByRole('button', { name: 'Test' })).toBeNull()
  expect(within(card).queryByRole('button', { name: 'Remove' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Find local AI' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Save connection' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Forget saved AI keys' })).toBeNull()
  expect(screen.queryByLabelText('Message')).toBeNull()
  expect(screen.getAllByText(/Only the household admin/).length).toBeGreaterThan(0)
})

it('shows the 403 detail when a write is refused', async () => {
  setup((url, init) => {
    if (url === '/api/llm/connections/c1/test') return json({ detail: 'Only the household admin can change AI connections.' }, 403)
    if (url === '/api/settings/llm.mode' && init?.method === 'PATCH') return json({ detail: 'Only the household admin can change AI connections.' }, 403)
  })
  render(AI)
  await screen.findByRole('region', { name: 'OpenAI' })
  await fireEvent.click(await screen.findByLabelText('Advanced: choose per task'))
  expect(await screen.findByText('Only the household admin can change AI connections.')).toBeInTheDocument()
})

it('shows a 429 message naming the cap', async () => {
  setup((url) => {
    if (url === '/api/llm/try') return json({ detail: "This month's AI spending cap (£10.00) would be exceeded." }, 429)
  })
  render(AI)
  await screen.findByRole('region', { name: 'OpenAI' })
  await fireEvent.input(screen.getByLabelText('Message'), { target: { value: 'Hi' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByText(/spending cap \(£10.00\)/)).toBeInTheDocument()
})

it('keeps chain entries beyond the second when editing a task', async () => {
  const ref = (m: string) => ({ connection_id: 'c1', model_id: m })
  const state = { conn: conn({ needs_notice: false }), routing: routing({ mode: 'advanced', tasks: {
    coach: { chain: [ref('gpt-x'), ref('b'), ref('c'), ref('d')], local_only: false, version: 4 },
  } }) }
  const calls = setup((url, init) => {
    if (url === '/api/llm/routing/tasks/coach' && init?.method === 'PUT') return json(state.routing)
  }, state)
  render(AI)
  const task = await screen.findByRole('region', { name: 'Task coach' })
  expect(within(task).getByText("More fallbacks are set; they're kept.")).toBeInTheDocument()
  await fireEvent.click(within(task).getByLabelText('Keep this task on this device'))
  await waitFor(() => expect(calls.some(([, i]) => i?.method === 'PUT')).toBe(true))
  const put = calls.find(([, i]) => i?.method === 'PUT')!
  const body = JSON.parse(put[1]!.body as string)
  expect(body.local_only).toBe(true)
  expect(body.expected_version).toBe(4)
  expect(body.chain.map((r: any) => r.model_id)).toEqual(['gpt-x', 'b', 'c', 'd'])
})

it('moves focus into the dialog on open and back to the opener on close', async () => {
  setup()
  render(AI)
  const card = await screen.findByRole('region', { name: 'OpenAI' })
  const opener = within(card).getByRole('button', { name: "Review what's sent" })
  opener.focus()
  await fireEvent.click(opener)
  const heading = await screen.findByText('Before you use OpenAI')
  await waitFor(() => expect(document.activeElement).toBe(heading))
  await fireEvent.keyDown(heading, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByText('Before you use OpenAI')).toBeNull())
  await waitFor(() => expect(document.activeElement).toBe(opener))
})
