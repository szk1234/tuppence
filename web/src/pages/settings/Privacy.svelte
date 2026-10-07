<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../../components/Notice.svelte'
  import { api, ApiError } from '../../lib/api'
  import { session } from '../../lib/session.svelte'

  type Entry = { key: string; value: unknown; version: number; description: string }
  type LogEntry = { id: number; ts: string; purpose: string; task: string | null; destination: string; bytes_out: number; bytes_in: number; redactions: number; outcome: string; note: string | null }

  const TOGGLES: Array<[string, string, string]> = [
    ['privacy.local_only', 'Local only', 'AI and research calls stay on this device or your home network. Cloud models are refused while this is on.'],
    ['privacy.pseudonymise', 'Pseudonymise before cloud AI', 'Names and account numbers become stand-ins like "Adult A" and "ACCT_2". Best-effort: some formats may slip through. Off by default.'],
    ['privacy.research_lookups', 'Research lookups', 'Look up unknown merchant names online. Only merchant names are sent, never amounts or your details.'],
    ['privacy.live_market_data', 'Live market data', 'Interest rates, inflation, exchange rates and share prices. Share lookups reveal which tickers you hold.'],
    ['privacy.datapack_updates', 'Data-pack updates', 'Anonymous downloads of updated UK tax, benefit and rent data.'],
  ]
  const NAMES_KEY = 'privacy.hidden_names'

  let settings = $state<Record<string, Entry>>({})
  let log = $state<LogEntry[]>([])
  let names = $state('')
  let error = $state('')
  let saved = $state('')
  let busy = $state(false)

  const canEdit = $derived(session.mode !== 'server' || session.user?.is_admin === true)
  const fail = (e: unknown) => { saved = ''; error = e instanceof ApiError ? e.detail : 'Something went wrong.' }
  const fmt = (n: number) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`)
  // Stored in UTC; always shown in UK time, whatever the browser's own time zone.
  const when = (ts: string) => new Date(ts).toLocaleString('en-GB', { timeZone: 'Europe/London' })

  async function load() {
    const res = await api<{ settings: Entry[] }>('/api/settings')
    settings = Object.fromEntries(res.settings.map((s) => [s.key, s]))
    const hidden = settings[NAMES_KEY]?.value
    names = Array.isArray(hidden) ? hidden.join('\n') : ''
    log = (await api<{ entries: LogEntry[] }>('/api/privacy/log?limit=100')).entries
  }
  onMount(() => { load().catch(fail) })

  async function toggle(key: string, value: boolean) {
    error = ''; saved = ''; busy = true
    try {
      settings[key] = await api<Entry>(`/api/settings/${key}`, { method: 'PATCH', body: { value, expected_version: settings[key].version } })
    } catch (e) { fail(e); await load().catch(() => {}) } finally { busy = false }
  }

  async function saveNames(e: SubmitEvent) {
    e.preventDefault(); error = ''; saved = ''; busy = true
    const list = [...new Set(names.split('\n').map((n) => n.trim()).filter(Boolean))]
    try {
      settings[NAMES_KEY] = await api<Entry>(`/api/settings/${NAMES_KEY}`, { method: 'PATCH', body: { value: list, expected_version: settings[NAMES_KEY]?.version ?? 0 } })
      names = list.join('\n')
      saved = 'Names saved.'
    } catch (err) { fail(err); await load().catch(() => {}) } finally { busy = false }
  }
</script>

<section>
  <h1>Privacy</h1>
  <p>With a local model, nothing leaves your machine. With a cloud model, your statement text goes to the provider you chose, and every call is logged below.</p>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />
  {#if !canEdit}<p class="hint">Only the household admin can change these settings.</p>{/if}
  <div class="card">
    <fieldset class="bare" disabled={busy || !canEdit}>
      {#each TOGGLES as [key, label, help] (key)}
        {#if settings[key]}
          <div class="toggle">
            <label><input type="checkbox" checked={settings[key].value === true}
              onchange={(e) => toggle(key, (e.currentTarget as HTMLInputElement).checked)} /> {label}</label>
            <p class="hint">{help}</p>
          </div>
        {/if}
      {/each}
    </fieldset>
  </div>
  {#if settings[NAMES_KEY]}
    <form class="card" onsubmit={saveNames}>
      <fieldset class="bare" disabled={busy || !canEdit}>
        <label for="hidden-names">Names to hide</label>
        <p class="hint">One per line, for example your landlord or an employer. They are swapped for stand-ins before anything goes to a cloud AI, when "Pseudonymise before cloud AI" is on.</p>
        <textarea id="hidden-names" rows="4" bind:value={names}></textarea>
        <button type="submit">Save names</button>
      </fieldset>
    </form>
  {/if}
  <div class="card">
    <h2>Privacy log</h2>
    <p class="hint">Every call that leaves, or was stopped from leaving, this machine: when, where to, how much was sent and received, and how many values were swapped for stand-ins. It records these details, not what was sent.</p>
    {#if log.length === 0}<p>Nothing has left this machine yet.</p>{:else}
      <div class="table-wrap">
        <table>
          <thead><tr><th>Time (UK)</th><th>Purpose</th><th>Task</th><th>Destination</th><th>Sent</th><th>Received</th><th><abbr title="Values swapped for stand-ins before sending">Masked</abbr></th><th>Outcome</th></tr></thead>
          <tbody>
            {#each log as e (e.id)}
              <tr><td>{when(e.ts)}</td><td>{e.purpose}</td><td>{e.task ?? '—'}</td><td>{e.destination}</td>
                <td>{fmt(e.bytes_out)}</td><td>{fmt(e.bytes_in)}</td><td>{e.redactions}</td><td title={e.note ?? ''}>{e.outcome}</td></tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>
</section>
