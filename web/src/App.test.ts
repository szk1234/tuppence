import { render, screen } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.svelte'

afterEach(() => vi.unstubAllGlobals())

describe('App', () => {
  it('shows the name, tagline and backend status', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ status: 'ok', version: '0.1.0.dev0', mode: 'local' }), { status: 200 })))
    render(App)
    expect(screen.getByRole('heading', { name: 'Tuppence' })).toBeInTheDocument()
    expect(screen.getByText(/private AI money coach for UK households/)).toBeInTheDocument()
    expect(await screen.findByText(/Connected · v0\.1\.0\.dev0 · local/)).toBeInTheDocument()
  })

  it('says so when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network') }))
    render(App)
    expect(await screen.findByText(/Can't reach the Tuppence service/)).toBeInTheDocument()
  })
})
