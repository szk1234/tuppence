import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import WhyPanel from './WhyPanel.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const why = (version: number) => ({ transaction_id: 't_1', status: 'guessed', status_label: 'Best guess', decided_by: 'review',
  decided_by_label: 'The AI, on a second look', confidence: 0.5, category_path: ['Other spending'], steps: ['A step.'],
  rule: null, merchant: null, knowledge_version: 3, current_knowledge_version: 3, stale: false, history: [], version })

it('after a failed "decide again" it shows the message, refetches and retries with the fresh version', async () => {
  const bodies: any[] = []
  let gets = 0
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      bodies.push(JSON.parse(init.body as string))
      return bodies.length === 1 ? json({ detail: 'This changed since you opened it.' }, 409) : json({})
    }
    gets += 1
    return json(why(gets === 1 ? 3 : 8))
  }))
  const onchanged = vi.fn()
  render(WhyPanel, { id: 't_1', onclose: () => {}, onchanged })
  await fireEvent.click(await screen.findByRole('button', { name: 'Let Tuppence decide again' }))
  expect(await screen.findByText('This changed since you opened it.')).toBeInTheDocument()
  await vi.waitFor(() => expect(gets).toBe(2))
  await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Let Tuppence decide again' })).toBeEnabled())
  await fireEvent.click(screen.getByRole('button', { name: 'Let Tuppence decide again' }))
  await vi.waitFor(() => expect(onchanged).toHaveBeenCalled())
  expect(bodies).toEqual([{ expected_version: 3 }, { expected_version: 8 }])
})
