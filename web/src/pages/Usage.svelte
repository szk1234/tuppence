<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../components/Notice.svelte'
  import { api, ApiError } from '../lib/api'

  type Row = { calls: number; failed_calls: number; tokens: number; gbp: number }
  type Usage = { total_gbp: number; calls: number; failed_calls: number; estimated_calls: number; by_task: Record<string, Row>; by_model: Record<string, Row>; month: string; cap_gbp: number }

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

  const thisMonth = new Date().toISOString().slice(0, 7)
  const gbp = (n: number) => `£${n.toFixed(2)}`
</script>

<section>
  <h1>Usage</h1>
  <Notice message={error} />
  {#if usage}
    <div class="card">
      <label for="usage-month">Month</label>
      <input id="usage-month" type="month" value={month} onchange={(e) => { const v = (e.currentTarget as HTMLInputElement).value; if (v) load(v) }} />
      <p><strong>{gbp(usage.total_gbp)} of {gbp(usage.cap_gbp)}</strong> {usage.month === thisMonth ? 'this month' : `in ${usage.month}`} · {usage.calls} calls{#if usage.failed_calls} ({usage.failed_calls} failed){/if}</p>
      {#if usage.estimated_calls > 0}
        <p class="hint"><span class="badge">estimated</span> {usage.estimated_calls} {usage.estimated_calls === 1 ? 'call' : 'calls'} used a fallback price because the model has no known price: $5 per million input tokens and $15 per million output tokens, so the cap never treats unknown spend as free. You can set the real price on the model in Settings › AI.</p>
      {/if}
    </div>
    {#each [['By task', usage.by_task], ['By model', usage.by_model]] as [title, rows] (title)}
      <div class="card">
        <h2>{title}</h2>
        {#if Object.keys(rows as Record<string, Row>).length === 0}<p>No AI calls yet.</p>{:else}
          <div class="table-wrap">
            <table>
              <thead><tr><th>{title === 'By task' ? 'Task' : 'Model'}</th><th>Calls</th><th>Failed</th><th>Tokens</th><th>Cost</th></tr></thead>
              <tbody>
                {#each Object.entries(rows as Record<string, Row>) as [key, r] (key)}
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
