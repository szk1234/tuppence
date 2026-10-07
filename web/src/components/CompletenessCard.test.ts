import { render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import CompletenessCard from './CompletenessCard.svelte'
import { json, stubApi } from './forms/helpers'

afterEach(() => vi.unstubAllGlobals())

const state = (over: Record<string, unknown> = {}) => ({
  steps: [], next_step: 'household', started: true, finished: false, completeness: 47,
  prompts: [1, 2, 3, 4].map((n) => ({ id: `p${n}`, text: `Do thing ${n}`, unlocks: `feature ${n}`, link: `/settings/x${n}` })), ...over,
})

it('shows the percentage, the top three prompts and Continue setup', async () => {
  stubApi((url) => (url === '/api/onboarding' ? json(state()) : undefined))
  render(CompletenessCard)
  expect(await screen.findByText('Your profile is 47% complete')).toBeInTheDocument()
  expect(screen.getAllByRole('listitem')).toHaveLength(3)
  expect(screen.queryByText('Do thing 4')).toBeNull()
  expect(screen.getByRole('link', { name: 'Do thing 1' })).toHaveAttribute('href', '/settings/x1')
  expect(screen.getByRole('link', { name: 'Continue setup' })).toHaveAttribute('href', '/welcome')
})

it('renders nothing when the request fails or the JSON is unexpected', async () => {
  stubApi(() => json({ status: 'ok', version: '0.1.0' }))
  const { container } = render(CompletenessCard)
  await new Promise((r) => setTimeout(r, 50))
  expect(container.querySelector('section')).toBeNull()
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network') }))
  const again = render(CompletenessCard)
  await new Promise((r) => setTimeout(r, 50))
  expect(again.container.querySelector('section')).toBeNull()
})

it('hides Continue setup once setup is finished', async () => {
  stubApi(() => json(state({ finished: true, prompts: [] , completeness: 80})))
  render(CompletenessCard)
  expect(await screen.findByText('Your profile is 80% complete')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Continue setup' })).toBeNull()
})
