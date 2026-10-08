import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import UploadDropzone from './UploadDropzone.svelte'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

it('uploads every chosen file and lists the ones refused', async () => {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => json({
    statements: [{ id: 's_1', filename: 'monzo.csv' }],
    rejected: [{ filename: 'photo.heic', reason: 'HEIC photos (the iPhone camera format) can\'t be read yet.' }],
  }, 201))
  vi.stubGlobal('fetch', fetchMock)
  const onuploaded = vi.fn()
  render(UploadDropzone, { onuploaded })
  const files = [new File(['Date,Amount\n'], 'monzo.csv', { type: 'text/csv' }), new File(['x'], 'photo.heic')]
  await fireEvent.change(screen.getByLabelText('Choose files'), { target: { files } })
  expect(await screen.findByText(/HEIC photos/)).toBeInTheDocument()
  const [url, init] = fetchMock.mock.calls[0]
  expect(url).toBe('/api/statements')
  const body = init!.body as FormData
  expect(body.getAll('files').map((f) => (f as File).name)).toEqual(['monzo.csv', 'photo.heic'])
  expect(new Headers(init!.headers).get('Content-Type')).toBeNull()
  expect(onuploaded).toHaveBeenCalledWith([{ id: 's_1', filename: 'monzo.csv' }])
})

it('keeps the file input inside its label so focus shows on the button', () => {
  render(UploadDropzone, {})
  const input = screen.getByLabelText('Choose files')
  expect(input.closest('label')).toHaveClass('button')
  input.focus()
  expect(input).toHaveFocus()
  expect(input.closest('label')!.matches(':focus-within')).toBe(true)
})
