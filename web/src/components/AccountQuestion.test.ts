import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, it, vi } from 'vitest'
import AccountQuestion from './AccountQuestion.svelte'
import type { StatementView } from '../lib/statements'

afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const statement = {
  id: 's_1', filename: 'card.pdf', status: 'needs_account', version: 3,
  question: {
    text: 'Which account is this?', reason: 'More than one of your accounts could match.', best_guess: 'a_2',
    candidates: ['a_1', 'a_2'], prefill: { provider: 'barclaycard', kind: 'credit_card', last4: '4242', nickname: 'PDF statement' },
  },
} as unknown as StatementView

function stub(answer: (body: any) => unknown) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/accounts') return json({ accounts: [
      { id: 'a_1', nickname: 'Joint', provider_name: 'Monzo', kind: 'current', last4: '1234' },
      { id: 'a_2', nickname: 'Card', provider_name: 'Barclaycard', kind: 'credit_card', last4: null },
    ] })
    if (url === '/api/accounts/providers') return json({ providers: [
      { id: 'monzo', name: 'Monzo', kinds: ['current'] }, { id: 'barclaycard', name: 'Barclaycard', kinds: ['credit_card'] },
      { id: 'other', name: 'Other', kinds: ['current', 'savings', 'credit_card'] },
    ] })
    if (url === '/api/household/people') return json({ people: [{ id: 'p_1', display_name: 'Alex Example', role: 'adult' }] })
    if (url === '/api/statements/s_1/account' && init?.method === 'POST') return json(answer(JSON.parse(init.body as string)))
    return json({ detail: 'unexpected' }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

it('preselects the best guess and answers with it', async () => {
  const sent: any[] = []
  stub((body) => { sent.push(body); return { ...statement, status: 'parsing' } })
  const onanswered = vi.fn()
  render(AccountQuestion, { statement, onanswered })
  const guess = await screen.findByLabelText(/Card · Barclaycard/)
  expect(guess).toBeChecked()
  await fireEvent.click(screen.getByRole('button', { name: 'Use this account' }))
  await vi.waitFor(() => expect(onanswered).toHaveBeenCalled())
  expect(sent).toEqual([{ account_id: 'a_2', expected_version: 3 }])
})

it('offers a new account filled in from the statement', async () => {
  const sent: any[] = []
  stub((body) => { sent.push(body); return { ...statement, status: 'parsing' } })
  render(AccountQuestion, { statement })
  await screen.findByLabelText(/Card · Barclaycard/)
  await fireEvent.click(screen.getByLabelText('+ New account'))
  expect(await screen.findByLabelText('Last 4 digits')).toHaveValue('4242')
  expect(screen.getByLabelText('Bank or card provider')).toHaveValue('barclaycard')
  expect(screen.getByLabelText('Alex Example')).toBeChecked()
  await fireEvent.input(screen.getByLabelText('Nickname'), { target: { value: 'Everyday card' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Use this account' }))
  await vi.waitFor(() => expect(sent.length).toBe(1))
  expect(sent[0].new_account).toEqual({
    provider: 'barclaycard', provider_name: null, kind: 'credit_card', nickname: 'Everyday card', last4: '4242',
    owner_ids: ['p_1'],
  })
})

it('stays locked until accounts, providers and people have loaded', async () => {
  stub(() => statement)
  render(AccountQuestion, { statement })
  expect(screen.getByRole('button', { name: 'Use this account' })).toBeDisabled()
  await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Use this account' })).toBeEnabled())
})
