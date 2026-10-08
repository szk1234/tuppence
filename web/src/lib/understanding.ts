import { api } from './api'

export type Filters = { account_id?: string; who?: string; status?: '' | 'unknown' | 'guessed' }
export type PeriodView = {
  start: string; end: string; label: string; mode: 'calendar_month' | 'pay_cycle'
  previous: string; next: string; has_later_data: boolean
}
export type Tile = { id: string; label: string; amount: string; count: number; has_children: boolean }
export type SpendingView = {
  period: PeriodView; path: { id: string | null; label: string }[]; total: string; direct: string
  money_in: string; saved: string; tiles: Tile[]; waiting_for_ai: number
}
export type Txn = {
  id: string; date: string; amount: string; description: string; merchant: string | null
  account_id: string; category_id: string | null; category_label: string | null; who: string | null
  status: 'unknown' | 'guessed' | 'inferred' | 'confirmed'; decided_by: string | null
  confidence: number; version: number
}
export type Category = {
  id: string; parent_id: string | null; level: number; label: string; kind: 'spend' | 'income' | 'transfer'
  essential: boolean; source: string; retired: boolean; version: number
}
export type RuleOffer = {
  merchant_id: string; merchant_name: string; category_id: string; matches: number; will_change: number
  kept_yours: number
}
export type Why = {
  transaction_id: string; status: string; status_label: string; decided_by: string | null
  decided_by_label: string | null; confidence: number; category_path: string[]; steps: string[]
  rule: { id: string; description: string; source: string } | null
  merchant: { id: string; name: string; usual_category: string | null; memory: string; seen_count: number } | null
  knowledge_version: number; current_knowledge_version: number; stale: boolean
  history: { when: string; who: string; category: string; reason: string }[]; version: number
}
export type RuleView = {
  id: string; description: string; source: 'user' | 'learned' | 'seed'; enabled: boolean; hit_count: number
  version: number; merchant_id: string | null; set_category_id: string | null
  min_amount: string | null; max_amount: string | null
}
export type Commitment = {
  id: string; name: string; kind: 'bill' | 'subscription' | 'instalment'; cadence: string
  cadence_label: string; amount: string; annual_cost: string; next_due: string | null; last_paid: string
  status: 'active' | 'lapsed' | 'ended'; flags: string[]; flag_labels: string[]
  price_history: { since: string; amount: string }[]; duplicate_of: string[]; account_id: string
  category_id: string | null; dismissed: boolean; version: number
}
export type Due = { date: string; commitment_id: string; name: string; amount: string; kind: string }
export type CommitmentsView = {
  commitments: Commitment[]; upcoming: Due[]; calendar_start: string; calendar_end: string
  totals: { annual: string; monthly: string; count: number; by_kind: Record<string, string> }
}
export type AnalysisStatus = {
  running: boolean; queued: boolean; waiting: Record<string, number>
  last_run: { status: string; summary: string; finished_at: string | null; started_at: string } | null
}
export type HomeSummary = {
  period: { start: string; end: string; label: string }; spent: string
  top: { id: string; label: string; amount: string }[]; due_soon: Due[]; analysis: AnalysisStatus
}

function query(params: Record<string, string | null | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v)
  const s = q.toString()
  return s ? `?${s}` : ''
}

export const getSpending = (on: string | null, category: string | null, f: Filters) =>
  api<SpendingView>(`/api/spending${query({ on, category, ...f })}`)

export const getTransactions = async (on: string | null, category: string | null, f: Filters) =>
  (await api<{ transactions: Txn[] }>(`/api/spending/transactions${query({ on, category, ...f })}`)).transactions

export const getWhy = (id: string) => api<Why>(`/api/transactions/${encodeURIComponent(id)}/why`)

export const correct = (id: string, body: { category_id?: string; is_transfer?: boolean; expected_version: number }) =>
  api<{ understanding: { version: number; category_id: string | null }; rule_offer: RuleOffer | null }>(
    `/api/transactions/${encodeURIComponent(id)}/understanding`, { method: 'PATCH', body })

export const resetUnderstanding = (id: string, version: number) =>
  api(`/api/transactions/${encodeURIComponent(id)}/understanding/reset`, { method: 'POST', body: { expected_version: version } })

export const getCategories = async () => (await api<{ categories: Category[] }>('/api/categories')).categories

export const createRule = (body: Record<string, unknown>) =>
  api<{ rule: RuleView; changed: number }>('/api/rules', { method: 'POST', body })

export const listRules = async (includeDisabled = false) =>
  (await api<{ rules: RuleView[] }>(`/api/rules${includeDisabled ? '?include_disabled=true' : ''}`)).rules

export const disableRule = (id: string, version: number) =>
  api<{ rule: RuleView; released: number }>(`/api/rules/${encodeURIComponent(id)}/disable`, { method: 'POST', body: { expected_version: version } })

export const getCommitments = (includeDismissed = false) =>
  api<CommitmentsView>(`/api/commitments${includeDismissed ? '?include_dismissed=true' : ''}`)

export const dismissCommitment = (id: string, version: number) =>
  api<Commitment>(`/api/commitments/${encodeURIComponent(id)}/dismiss`, { method: 'POST', body: { expected_version: version } })

export const restoreCommitment = (id: string, version: number) =>
  api<Commitment>(`/api/commitments/${encodeURIComponent(id)}/restore`, { method: 'POST', body: { expected_version: version } })

export const getAnalysis = () => api<AnalysisStatus>('/api/analysis')
export const runAnalysis = () => api<{ job_id: number }>('/api/analysis/run', { method: 'POST' })
export const getHomeSummary = () => api<HomeSummary>('/api/home/summary')

/** "Food & drink › Groceries" for every active category, in tree order. */
export function categoryOptions(categories: Category[]): { id: string; label: string; group: string }[] {
  const byId = new Map(categories.map((c) => [c.id, c]))
  const path = (c: Category): string[] => {
    const out: string[] = []
    let cur: Category | undefined = c
    while (cur) { out.unshift(cur.label); cur = cur.parent_id ? byId.get(cur.parent_id) : undefined }
    return out
  }
  return categories.filter((c) => !c.retired).map((c) => {
    const p = path(c)
    return { id: c.id, label: p.join(' › '), group: p[0] }
  })
}

export const STATUS_LABELS: Record<Txn['status'], string> = {
  unknown: 'Not sorted yet', guessed: 'Best guess', inferred: 'Sorted', confirmed: 'You set this',
}
