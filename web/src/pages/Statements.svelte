<script lang="ts">
  import { onMount } from 'svelte'
  import AccountQuestion from '../components/AccountQuestion.svelte'
  import Notice from '../components/Notice.svelte'
  import UploadDropzone from '../components/UploadDropzone.svelte'
  import { api, ApiError } from '../lib/api'
  import { dmyDate } from '../lib/dates'
  import { link } from '../lib/router.svelte'
  import {
    IN_PROGRESS, listStatements, removeStatement, retryStatement, type StatementView,
  } from '../lib/statements'

  type Setting = { key: string; value: unknown; version: number }

  let statements = $state<StatementView[]>([])
  let loaded = $state(false)
  let error = $state('')
  let vision = $state<{ on: boolean; version: number } | null>(null)
  let visionBusy = $state(false)
  let acting = $state(false)
  let tick = $state(0)

  const fail = (err: unknown) => { error = err instanceof ApiError ? err.detail : 'Something went wrong.' }

  async function load() {
    try {
      statements = await listStatements()
      loaded = true
    } catch (err) { fail(err) } finally { tick++ }
  }
  onMount(async () => {
    await load()
    try {
      const all = await api<{ settings: Setting[] }>('/api/settings')
      const entry = all.settings.find((x) => x.key === 'ingest.vision_for_scans')
      if (entry) vision = { on: entry.value === true, version: entry.version }
    } catch (err) { fail(err) }
  })

  async function toggleVision(event: Event) {
    const box = event.currentTarget as HTMLInputElement
    if (!vision || visionBusy) return
    visionBusy = true
    try {
      const entry = await api<Setting>('/api/settings/ingest.vision_for_scans', {
        method: 'PATCH', body: { value: !vision.on, expected_version: vision.version },
      })
      vision = { on: entry.value === true, version: entry.version }
    } catch (err) { box.checked = vision.on; fail(err) } finally { visionBusy = false }
  }

  $effect(() => {
    void tick // a failed refresh re-arms the timer
    if (!statements.some((s) => IN_PROGRESS.includes(s.status))) return
    const timer = setTimeout(load, 1500)
    return () => clearTimeout(timer)
  })

  function period(s: StatementView) {
    return s.period_start && s.period_end ? ` · ${dmyDate(s.period_start)} to ${dmyDate(s.period_end)}` : ''
  }

  function summary(s: StatementView) {
    const parts = [`${s.counts.new} new transaction${s.counts.new === 1 ? '' : 's'}`]
    if (s.counts.duplicates) parts.push(`${s.counts.duplicates} already in Tuppence`)
    if (!s.balance_verified) parts.push('balance unverified')
    return parts.join(' · ')
  }

  async function retry(s: StatementView) {
    if (acting) return
    error = ''
    acting = true
    try { await retryStatement(s.id, s.version); await load() } catch (err) { fail(err) } finally { acting = false }
  }

  async function remove(s: StatementView) {
    if (!confirm(`Remove ${s.filename} and its transactions?`)) return
    if (acting) return
    error = ''
    acting = true
    try { await removeStatement(s.id); await load() } catch (err) { fail(err) } finally { acting = false }
  }
</script>

<section>
  <h1>Statements</h1>
  <p>Add statements for any of your accounts. Bank and card exports are read on this device; PDFs and screenshots are read by your chosen AI, and every amount is checked against the file.</p>
  <UploadDropzone onuploaded={() => load()} />
  <Notice message={error} />
  {#if loaded && !statements.length}<p>No statements yet.</p>{/if}
  <ul class="statements">
    {#each statements as s (s.id)}
      <li class="card" aria-label={s.filename}>
        <div class="title">
          <strong>{s.filename}</strong>
          <span class="badge status-{s.status}">{s.status_label}</span>
        </div>
        {#if s.account_name}<p class="meta">{s.account_name}{period(s)}</p>{/if}
        {#if s.status === 'imported'}
          <p>{summary(s)}</p>
          <a href={`/statements/${s.id}`} onclick={link}>View transactions</a>
        {:else if s.status === 'needs_account' && s.question}
          <AccountQuestion statement={s} onanswered={() => load()} />
        {:else if s.status === 'needs_review'}
          <p>Some figures didn't add up, so nothing was imported yet.</p>
          <a href={`/statements/${s.id}`} onclick={link}>Check and fix</a>
        {:else if s.status === 'failed'}
          <p class="warn">{s.error}</p>
          <button onclick={() => retry(s)} disabled={acting}>Try again</button>
        {/if}
        {#each s.warnings as warning}<p class="hint">{warning}</p>{/each}
        <button class="link" disabled={acting} onclick={() => remove(s)} aria-label={`Remove ${s.filename}`}>Remove</button>
      </li>
    {/each}
  </ul>
  {#if vision}
    <details>
      <summary>Reading scans and screenshots</summary>
      <label class="choice">
        <input type="checkbox" checked={vision.on} disabled={visionBusy} onchange={toggleVision} />
        Use my AI vision model instead of reading them on this device
      </label>
      <p class="hint">The whole page is sent to the model, including your name and address. Only turn this on for scans this device can't read.</p>
    </details>
  {/if}
</section>

<style>
  .statements { list-style: none; padding: 0; }
  .title { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; align-items: baseline; }
  .status-failed, .status-needs_review, .status-needs_account { background: var(--danger-bg); color: var(--danger); }
  .choice { display: flex; gap: .5rem; align-items: center; font-weight: 400; }
</style>
