import { render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../App.svelte'
import { router } from '../lib/router.svelte'
import { session } from '../lib/session.svelte'
import { statementIdFromPath } from '../lib/statements'

afterEach(() => vi.unstubAllGlobals())

const BAD = ['..%2Fsettings%2Fx', '%2e%2e%2faccounts', 's_1%2Fdraft', '%3F', '%23', '%E0%A4%A', 's_1', 's_0123456789%2F..']

it.each(BAD)('shows Not found and fetches nothing for /statements/%s', async (tail) => {
  const fetchMock = vi.fn(async (_url: string) => new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  Object.assign(session, { loaded: true, authenticated: true, mode: 'local' })
  router.path = `/statements/${tail}`
  render(App)
  expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.filter(([u]) => String(u).includes('/api/statements'))).toEqual([])
  router.path = '/'
})

it('accepts only server-format ids', () => {
  expect(statementIdFromPath('/statements/s_0123456789')).toEqual({ id: 's_0123456789' })
  expect(statementIdFromPath('/statements')).toBe('none')
  expect(statementIdFromPath('/statements/s_01234567890')).toBe('bad')
})
