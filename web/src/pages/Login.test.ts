import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import Login from './Login.svelte'

afterEach(() => vi.unstubAllGlobals())

it('shows the server error message on wrong password', async () => {
  vi.stubGlobal('fetch', vi.fn(async () =>
    new Response(JSON.stringify({ detail: 'Wrong username or password.' }), { status: 401 })))
  render(Login)
  await fireEvent.input(screen.getByLabelText('Username'), { target: { value: 'alex' } })
  await fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'nope-nope-nope' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByText('Wrong username or password.')).toBeInTheDocument()
})
