import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import FirstUploadStep from './FirstUploadStep.svelte'

afterEach(() => vi.unstubAllGlobals())

it('uploads statements from the last onboarding step', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    statements: [{ id: 's_1' }, { id: 's_2' }], rejected: [],
  }), { status: 201, headers: { 'Content-Type': 'application/json' } })))
  render(FirstUploadStep)
  expect(screen.getByText(/Drop in your last 3 months of statements/)).toBeInTheDocument()
  const files = [new File(['a'], 'a.csv'), new File(['b'], 'b.csv')]
  await fireEvent.change(screen.getByLabelText('Choose files'), { target: { files } })
  expect(await screen.findByText(/2 files added/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Statements page' })).toHaveAttribute('href', '/statements')
})
