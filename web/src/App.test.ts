import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.svelte'
import Home from './pages/Home.svelte'

afterEach(() => vi.unstubAllGlobals())

describe('Home', () => {
  it('shows the name, tagline and backend status', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ status: 'ok', version: '0.1.0.dev0', mode: 'local' }), { status: 200 })))
    render(Home)
    expect(screen.getByRole('heading', { name: 'Tuppence' })).toBeInTheDocument()
    expect(screen.getByText(/private AI money coach for UK households/)).toBeInTheDocument()
    expect(await screen.findByText(/Connected · v0\.1\.0\.dev0 · local/)).toBeInTheDocument()
  })

  it('says so when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network') }))
    render(Home)
    expect(await screen.findByText(/Can't reach the Tuppence service/)).toBeInTheDocument()
  })
})

describe('App', () => {
  it('shows first-run setup in server mode', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ authenticated: false, mode: 'server', needs_setup: true, user: null, csrf_token: null }), { status: 200 })))
    render(App)
    expect(await screen.findByRole('heading', { name: 'Set up Tuppence' })).toBeInTheDocument()
  })

  it('lands on Home after signing in from /login', async () => {
    window.history.pushState({}, '', '/login')
    const { router } = await import('./lib/router.svelte')
    router.path = '/login'
    const { session } = await import('./lib/session.svelte')
    session.loaded = false
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 })
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/auth/session') return json({ authenticated: false, mode: 'server', needs_setup: false, user: null, csrf_token: null })
      if (url === '/api/auth/login') return json({ authenticated: true, mode: 'server', needs_setup: false, user: { username: 'alex', is_admin: true }, csrf_token: 't' })
      return json({ status: 'ok', version: '1', mode: 'server' })
    }))
    render(App)
    await fireEvent.input(await screen.findByLabelText('Username'), { target: { value: 'alex' } })
    await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'long-enough-pass' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { name: 'Tuppence' })).toBeInTheDocument()
    expect(router.path).toBe('/')
  })

  it('keeps a deep link after signing in', async () => {
    window.history.pushState({}, '', '/settings/household')
    const { router } = await import('./lib/router.svelte')
    router.path = '/settings/household'
    const { session } = await import('./lib/session.svelte')
    session.loaded = false
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 })
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/auth/session') return json({ authenticated: false, mode: 'server', needs_setup: false, user: null, csrf_token: null })
      if (url === '/api/auth/login') return json({ authenticated: true, mode: 'server', needs_setup: false, user: { username: 'alex', is_admin: true }, csrf_token: 't' })
      if (url === '/api/household') return json({ nation: null, postcode_district: null, currency: 'GBP', period_mode: 'calendar_month', period_anchor_person_id: null, version: 1 })
      if (url.startsWith('/api/household/people')) return json({ people: [] })
      return json({ status: 'ok', version: '1', mode: 'server' })
    }))
    render(App)
    await fireEvent.input(await screen.findByLabelText('Username'), { target: { value: 'alex' } })
    await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'long-enough-pass' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { name: 'Household', level: 1 })).toBeInTheDocument()
    expect(router.path).toBe('/settings/household')
    expect(window.location.pathname).toBe('/settings/household')
  })

  it('shows sign-in when setup was already completed elsewhere', async () => {
    window.history.pushState({}, '', '/')
    const { router } = await import('./lib/router.svelte')
    router.path = '/'
    const { session } = await import('./lib/session.svelte')
    session.loaded = false
    let done = false
    const fetchMock = vi.fn(async (url: string) => {
      if (url === '/api/auth/session')
        return new Response(JSON.stringify({ authenticated: false, mode: 'server', needs_setup: !done, user: null, csrf_token: null }), { status: 200 })
      if (url === '/api/auth/setup') {
        done = true
        return new Response(JSON.stringify({ detail: 'Setup is already complete.' }), { status: 409 })
      }
      return new Response('{}', { status: 200 })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(App)
    await fireEvent.input(await screen.findByLabelText('Username'), { target: { value: 'alex' } })
    await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'long-enough-pass' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([u]) => u === '/api/auth/session')).toHaveLength(2)
  })
})

describe('onboarding redirect', () => {
  const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status })

  /** Sign in at `path`; `onboarding` is what GET /api/onboarding answers (a number is an HTTP error). */
  async function signInAt(path: string, onboarding: unknown) {
    window.history.pushState({}, '', path)
    const { router } = await import('./lib/router.svelte')
    router.path = path
    const { session } = await import('./lib/session.svelte')
    session.loaded = false
    const fetchMock = vi.fn(async (url: string) => {
      if (url === '/api/auth/session') return json({ authenticated: false, mode: 'server', needs_setup: false, user: null, csrf_token: null })
      if (url === '/api/auth/login') return json({ authenticated: true, mode: 'server', needs_setup: false, user: { username: 'alex', is_admin: true }, csrf_token: 't' })
      if (url === '/api/onboarding') return typeof onboarding === 'number' ? json({ detail: 'nope' }, onboarding) : json(onboarding)
      if (url === '/api/household') return json({ nation: null, postcode_district: null, version: 1 })
      if (url.startsWith('/api/household/people')) return json({ people: [] })
      if (url.startsWith('/api/household/timeline')) return json({ entries: [] })
      return json({ status: 'ok', version: '1', mode: 'server' })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(App)
    await fireEvent.input(await screen.findByLabelText('Username'), { target: { value: 'alex' } })
    await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'long-enough-pass' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    return { router, fetchMock }
  }
  const fresh = { steps: [], next_step: 'welcome', started: false, finished: false, completeness: 0, prompts: [] }

  it('opens the wizard after sign-in when onboarding has not started and the path is /', async () => {
    const { router } = await signInAt('/', { ...fresh, steps: [{ id: 'welcome', title: 'Welcome', status: 'todo' }] })
    await waitFor(() => expect(router.path).toBe('/welcome'))
    expect(await screen.findByRole('heading', { level: 1, name: 'Welcome' })).toBeInTheDocument()
  })

  it('leaves /login for the wizard too (post-login path is /)', async () => {
    const { router } = await signInAt('/login', { ...fresh, steps: [{ id: 'welcome', title: 'Welcome', status: 'todo' }] })
    await waitFor(() => expect(router.path).toBe('/welcome'))
  })

  it('keeps a deep link even when onboarding has not started', async () => {
    const { router, fetchMock } = await signInAt('/settings/household', fresh)
    expect(await screen.findByRole('heading', { name: 'Household', level: 1 })).toBeInTheDocument()
    expect(router.path).toBe('/settings/household')
    expect(fetchMock.mock.calls.some(([u]) => u === '/api/onboarding')).toBe(false)
  })

  it('stays on Home once onboarding has started', async () => {
    const { router } = await signInAt('/', { ...fresh, started: true })
    expect(await screen.findByRole('heading', { name: 'Tuppence' })).toBeInTheDocument()
    expect(router.path).toBe('/')
  })

  it('stays on Home when the onboarding lookup fails', async () => {
    const { router } = await signInAt('/', 500)
    expect(await screen.findByRole('heading', { name: 'Tuppence' })).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 20))
    expect(router.path).toBe('/')
  })
})
