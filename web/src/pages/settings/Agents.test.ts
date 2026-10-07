import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
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
