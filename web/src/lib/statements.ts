import { api } from './api'

export type Status =
  | 'received' | 'identifying' | 'needs_account' | 'parsing' | 'needs_review' | 'imported' | 'failed'

export type Question = {
  text: string
  reason: string
  best_guess: string | null
  candidates: string[]
  prefill: { provider: string | null; kind: string | null; last4: string | null; nickname: string | null }
}

export type StatementView = {
  id: string; filename: string; format: string; status: Status; status_label: string
  account_id: string | null; account_name: string | null; provider: string | null
  period_start: string | null; period_end: string | null
  opening_balance: string | null; closing_balance: string | null; balance_verified: boolean
  counts: { rows: number; new: number; duplicates: number; skipped: number }
  question: Question | null; error: string | null; warnings: string[]; importer: string | null
  created_at: string; version: number; duplicate: boolean
}

export type DraftRow = {
  ref: string; date: string; amount: string; description: string; balance_after: string | null
  edited: boolean; errors: string[]
}

export type Transaction = { id: string; date: string; amount: string; description: string; balance_after: string | null }

export type StatementDetail = StatementView & {
  level: 'full' | 'screenshot'; check_errors: string[]; draft_rows: DraftRow[]
  draft_skipped: { ref: string; reason: string }[]; held_lines: { ref: string; text: string }[]
  transactions: Transaction[]
}

export type NewAccount = {
  provider: string; provider_name: string | null; kind: string; nickname: string
  last4: string | null; owner_ids: string[]
}

export const IN_PROGRESS: Status[] = ['received', 'identifying', 'parsing']

export const listStatements = async () =>
  (await api<{ statements: StatementView[] }>('/api/statements')).statements

export const getStatement = (id: string) => api<StatementDetail>(`/api/statements/${id}`)

export function uploadStatements(files: File[]) {
  const form = new FormData()
  for (const file of files) form.append('files', file, file.name)
  return api<{ statements: StatementView[]; rejected: { filename: string; reason: string }[] }>(
    '/api/statements', { method: 'POST', body: form })
}

export const answerAccount = (
  id: string, body: { account_id?: string; new_account?: NewAccount; expected_version: number },
) => api<StatementView>(`/api/statements/${id}/account`, { method: 'POST', body })

/** "Wrong account?": the file is read again for the chosen account (which may cost another AI read). */
export const changeAccount = (
  id: string, body: { account_id?: string; new_account?: NewAccount; expected_version: number },
) => api<StatementView>(`/api/statements/${id}/change-account`, { method: 'POST', body })

export const saveDraft = (
  id: string,
  body: {
    rows: { ref: string; date: string; amount: string; description: string }[]
    skipped: { ref: string; reason: string }[]
    expected_version: number
  },
) => api<StatementDetail>(`/api/statements/${id}/draft`, { method: 'PUT', body })

export const acceptDraft = (id: string, version: number) =>
  api<StatementView>(`/api/statements/${id}/accept`, { method: 'POST', body: { expected_version: version } })

export const retryStatement = (id: string, version: number) =>
  api<StatementView>(`/api/statements/${id}/retry`, { method: 'POST', body: { expected_version: version } })

export const removeStatement = (id: string) => api<void>(`/api/statements/${id}`, { method: 'DELETE' })
