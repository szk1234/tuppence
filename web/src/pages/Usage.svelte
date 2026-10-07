<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../components/Notice.svelte'
  import { api, ApiError } from '../lib/api'

  type Row = { calls: number; failed_calls: number; tokens: number; gbp: number }
  type Usage = {
    total_gbp: number; calls: number; failed_calls: number; estimated_calls: number
    by_task: Record<string, Row>; by_model: Record<string, Row>; by_day: Record<string, Row>
    month: string; current_month: string; cap_gbp: number
  }

  let usage = $state<Usage | null>(null)
  let month = $state('')
  let error = $state('')

  async function load(m = '') {
    error = ''
    try {
      usage = await api<Usage>(m ? `/api/usage?month=${encodeURIComponent(m)}` : '/api/usage')
      month = usage.month
    } catch (e) { error = e instanceof ApiError ? e.detail : 'Something went wrong.' }
  }
  onMount(() => { load() })

  const gbp = (n: number) => `£${n.toFixed(2)}`
  // Months and days are the server's UTC ones (the spending cap's clock), shown as written.
  const monthName = (m: string) => {
    const [y, mo] = m.split('-').map(Number)
    return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' })
  }
  const dayName = (d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })
  const tables = $derived(usage ? [
    { title: 'By task', column: 'Task', rows: Object.entries(usage.by_task) },
    { title: 'By model', column: 'Model', rows: Object.entries(usage.by_model) },
    { title: 'By day', column: 'Day', rows: Object.entries(usage.by_day).sort(([a], [b]) => a.localeCompare(b)).map(([d, r]) => [dayName(d), r] as [string, Row]) },
  ] : [])
</script>

<section>
  <h1>Usage</h1>
  <Notice message={error} />
  {#if usage}
    <div class="card">
      <label for="usage-month">Month</label>
      <input id="usage-month" type="month" value={month} onchange={(e) => { const v = (e.currentTarget as HTMLInputElement).value; if (v) load(v) }} />
      <p><strong>{gbp(usage.total_gbp)} of {gbp(usage.cap_gbp)}</strong> {usage.month === usage.current_month ? 'this month' : `in ${monthName(usage.month)}`} · {usage.calls} calls{#if usage.failed_calls} ({usage.failed_calls} failed){/if}</p>
      <p class="hint">Months and days follow UTC, the same clock as the spending cap, so in summer a new month starts at 1am UK time.</p>
      {#if usage.estimated_calls > 0}
        <p class="hint"><span class="badge">estimated</span> {usage.estimated_calls} {usage.estimated_calls === 1 ? 'call' : 'calls'} used a fallback price because the model has no known price: $5 per million input tokens and $15 per million output tokens, so the cap never treats unknown spend as free. You can set a model's real price in Settings › AI, under Models.</p>
      {/if}
    </div>
    {#each tables as t (t.title)}
      <div class="card">
        <h2>{t.title}</h2>
        {#if t.rows.length === 0}<p>No AI calls yet.</p>{:else}
          <div class="table-wrap">
            <table>
              <thead><tr><th>{t.column}</th><th>Calls</th><th>Failed</th><th>Tokens</th><th>Cost</th></tr></thead>
              <tbody>
                {#each t.rows as [key, r] (key)}
                  <tr><td>{key}</td><td>{r.calls}</td><td>{r.failed_calls}</td><td>{r.tokens.toLocaleString('en-GB')}</td><td>{gbp(r.gbp)}</td></tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </div>
    {/each}
  {/if}
</section>
