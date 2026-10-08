<script lang="ts">
  import { onMount } from 'svelte'
  import AccountQuestion from '../components/AccountQuestion.svelte'
  import Notice from '../components/Notice.svelte'
  import TransactionsTable from '../components/TransactionsTable.svelte'
  import { dmyDate, isoFromDmy } from '../lib/dates'
  import { errorText } from '../lib/form'
  import { formatGBP } from '../lib/money'
  import { link, navigate } from '../lib/router.svelte'
  import {
    acceptDraft, getStatement, IN_PROGRESS, KEPT_NOTE, retryStatement, saveDraft, type StatementDetail, WRONG_ACCOUNT_NOTE,
  } from '../lib/statements'

  let { id }: { id: string } = $props()

  type Edit = { ref: string; date: string; amount: string; description: string; skip: boolean; errors: string[] }

  let detail = $state<StatementDetail | null>(null)
  let edits = $state<Edit[]>([])
  // Held-back lines the person has decided about since the last save (sent with the next "Check again").
  let heldSkipped = $state<string[]>([])
  let heldAdded = $state<string[]>([])
  let error = $state('')
  let saved = $state('')
  let busy = $state(false)
  let wrongAccount = $state(false)
  let tick = $state(0)
  let confirmed = $state(false)

  const fail = (err: unknown) => { saved = ''; error = errorText(err) }

  /** Only a statement whose sums were done and passed "adds up"; with no balance printed there
   * was nothing to check. */
  function balanceCheck(d: StatementDetail): string {
    if (d.opening_balance === null && d.closing_balance === null) return 'No balances printed to check'
    return d.balance_verified ? 'Balances add up' : 'Balance unverified'
  }

  function show(d: StatementDetail) {
    detail = d
    heldSkipped = []
    heldAdded = []
    edits = d.draft_rows.map((r) => ({
      ref: r.ref, date: dmyDate(r.date), amount: r.amount, description: r.description, skip: false, errors: r.errors,
    }))
  }

  async function load() {
    try { show(await getStatement(id)) } catch (err) { fail(err) } finally { tick++ }
  }
  onMount(load)

  // While the file is still being read, look again every 1.5 seconds.
  $effect(() => {
    void tick // a failed look re-arms the timer
    if (!detail || !IN_PROGRESS.includes(detail.status)) return
    const timer = setTimeout(load, 1500)
    return () => clearTimeout(timer)
  })

  const heldLeft = $derived(
    (detail?.held_lines ?? []).filter((h) => !heldSkipped.includes(h.ref) && !heldAdded.includes(h.ref)),
  )
  const dirty = $derived(
    heldSkipped.length > 0 || heldAdded.length > 0 || edits.some((e, i) => {
      const r = detail?.draft_rows[i]
      return e.skip || !r || e.date !== dmyDate(r.date) || e.amount !== r.amount || e.description !== r.description
    }),
  )

  function addHeld(ref: string, text: string) {
    heldAdded = [...heldAdded, ref]
    edits = [...edits, { ref, date: '', amount: '', description: text, skip: false, errors: [] }]
  }

  async function checkAgain() {
    if (!detail) return
    error = ''
    saved = ''
    const bad = edits.find((e) => !e.skip && !isoFromDmy(e.date))
    if (bad) {
      error = `The date for line ${bad.ref} should look like 01/10/2026.`
      return
    }
    busy = true
    try {
      show(await saveDraft(id, {
        rows: edits.filter((e) => !e.skip).map((e) => ({
          ref: e.ref, date: isoFromDmy(e.date)!, amount: e.amount, description: e.description,
        })),
        skipped: [
          ...detail.draft_skipped,
          ...edits.filter((e) => e.skip).map((e) => ({ ref: e.ref, reason: 'Marked as not a transaction' })),
          ...heldSkipped.map((ref) => ({ ref, reason: 'Marked as not a transaction' })),
        ],
        expected_version: detail.version,
      }))
      saved = detail?.check_errors.length || edits.some((e) => e.errors.length)
        ? 'Saved. Some checks still fail.'
        : 'Saved. Everything adds up now.'
    } catch (err) { fail(err) } finally { busy = false }
  }

  async function importRows() {
    if (!detail) return
    if (dirty) {
      error = 'You have changed some lines. Press Check again to save them before importing.'
      return
    }
    const failing = detail.check_errors.length > 0 || detail.draft_rows.some((r) => r.errors.length > 0)
    if (failing && !confirm('Some checks still fail. Import these transactions anyway?')) return
    error = ''
    busy = true
    try {
      await acceptDraft(id, detail.version)
      navigate('/statements')
    } catch (err) { fail(err) } finally { busy = false }
  }

  async function retry() {
    if (!detail || busy) return
    error = ''
    busy = true
    try { await retryStatement(id, detail.version); navigate('/statements') } catch (err) { fail(err) } finally { busy = false }
  }
</script>

{#snippet more()}
  {#if detail && detail.transactions_total > detail.transactions.length}
    <p class="hint">Showing the first {detail.transactions.length.toLocaleString('en-GB')} of {detail.transactions_total.toLocaleString('en-GB')} transactions.</p>
  {/if}
{/snippet}

{#snippet kept()}
  {#if detail && detail.status !== 'imported' && detail.transactions.length}
    <h2>Transactions from its earlier import</h2>
    <p class="hint">These stay until this file is read again and imported, which then replaces them.</p>
    <TransactionsTable rows={detail.transactions} />
    {@render more()}
  {/if}
{/snippet}

{#snippet again()}
  <div class="again">
    <button onclick={retry} disabled={busy}>Try again</button>
    <span class="hint">Reads the file again from scratch. If it needs your AI model, that may cost another read. {KEPT_NOTE}</span>
    {#if !wrongAccount}
      <button class="link" onclick={() => (wrongAccount = true)}>Wrong account?</button>
    {:else}
      <p class="hint">{WRONG_ACCOUNT_NOTE}</p>
      {#if detail}<AccountQuestion statement={detail} change onanswered={() => navigate('/statements')} />{/if}
    {/if}
  </div>
{/snippet}

<section>
  <p><a href="/statements" onclick={link}>← All statements</a></p>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />
  {#if detail}
    <h1>{detail.filename}</h1>
    <p class="status">{detail.status_label}{detail.account_name ? ` · ${detail.account_name}` : ''}</p>
    {#if detail.status === 'imported'}
      <dl class="facts">
        {#if detail.period_start}<dt>Period</dt><dd>{dmyDate(detail.period_start)} to {dmyDate(detail.period_end)}</dd>{/if}
        {#if detail.opening_balance}<dt>Opening balance</dt><dd>{formatGBP(detail.opening_balance)}</dd>{/if}
        {#if detail.closing_balance}<dt>Closing balance</dt><dd>{formatGBP(detail.closing_balance)}</dd>{/if}
        <dt>Balance check</dt><dd>{balanceCheck(detail)}</dd>
      </dl>
      <TransactionsTable rows={detail.transactions} />
      {@render more()}
    {:else if detail.status === 'needs_review'}
      <h2>What didn't add up</h2>
      {#if detail.check_errors.length}
        <ul class="warn">{#each detail.check_errors as e}<li>{e}</li>{/each}</ul>
      {:else}<p>Problems are shown next to the lines below.</p>{/if}
      {#if heldLeft.length}
        <h2>Lines held back</h2>
        <p>These lines were not sent to the AI. Say what each one is.</p>
        <ul class="held">
          {#each heldLeft as held (held.ref)}
            <li>
              <span><strong>{held.ref}</strong> {held.text}</span>
              <button type="button" disabled={busy} onclick={() => addHeld(held.ref, held.text)} aria-label={`Add ${held.ref} as a transaction`}>Add as a transaction</button>
              <button type="button" disabled={busy} onclick={() => (heldSkipped = [...heldSkipped, held.ref])} aria-label={`${held.ref} is not a transaction`}>Not a transaction</button>
            </li>
          {/each}
        </ul>
      {/if}
      <p>Correct the lines below, or tick “Not a transaction”, then check again. You can import even if a check still fails.</p>
      <div class="scroll">
        <table>
          <caption>Transactions read from the file</caption>
          <thead><tr><th scope="col">Line</th><th scope="col">Date</th><th scope="col">Description</th><th scope="col">Amount (£)</th><th scope="col">Not a transaction</th></tr></thead>
          <tbody>
            {#each edits as edit (edit.ref)}
              <tr class:has-errors={edit.errors.length > 0}>
                <td>{edit.ref}</td>
                <td><input aria-label={`Date for ${edit.ref}`} bind:value={edit.date} size="10" disabled={busy} /></td>
                <td><input aria-label={`Description for ${edit.ref}`} bind:value={edit.description} disabled={busy} /></td>
                <td><input aria-label={`Amount for ${edit.ref}`} inputmode="decimal" bind:value={edit.amount} size="10" disabled={busy} /></td>
                <td><input type="checkbox" aria-label={`${edit.ref} is not a transaction`} bind:checked={edit.skip} disabled={busy} /></td>
              </tr>
              {#each edit.errors as e}<tr class="row-error"><td></td><td colspan="4">{e}</td></tr>{/each}
            {/each}
          </tbody>
        </table>
      </div>
      <button onclick={checkAgain} disabled={busy}>Check again</button>
      <button onclick={importRows} disabled={busy}>Import these transactions</button>
      <p class="hint">Import also keeps any new bank layout Tuppence learned from this file, so the next one is read on this device.</p>
    {:else if detail.status === 'needs_account' && detail.question}
      <AccountQuestion statement={detail} onanswered={() => navigate('/statements')} />
    {:else if detail.status === 'failed'}
      <p class="warn">{detail.error}</p>
    {:else if detail.status === 'needs_account'}
      <p>This file is waiting for you to say which account it belongs to. Go back to all statements to answer.</p>
    {:else}
      <p role="status">Still reading this file…</p>
    {/if}
    {@render kept()}
    {#if detail.status === 'failed' || detail.status === 'needs_review'}
      {@render again()}
    {:else if detail.status === 'imported'}
      <details class="again">
        <summary>Something wrong with this import?</summary>
        <p class="warn">This reads the file again. Its transactions stay until the new read is imported, which then replaces them. {KEPT_NOTE}</p>
        <label class="choice"><input type="checkbox" bind:checked={confirmed} /> I understand, read the file again</label>
        {#if confirmed}{@render again()}{/if}
      </details>
    {/if}
  {/if}
</section>

<style>
  .facts { display: grid; grid-template-columns: max-content 1fr; gap: .25rem 1rem; }
  .facts dt { font-weight: 600; }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; }
  caption { text-align: left; font-weight: 600; padding: .5rem 0; }
  th, td { padding: .3rem .4rem; border-bottom: 1px solid var(--line); text-align: left; }
  .has-errors td { background: var(--danger-bg); }
  .row-error td { color: var(--danger); font-size: .9rem; border-bottom: none; }
  .held { list-style: none; padding: 0; }
  .held li { display: flex; gap: .75rem; align-items: center; flex-wrap: wrap; margin: .4rem 0; }
  .held button { margin-top: 0; }
  .choice { display: flex; gap: .5rem; align-items: center; font-weight: 400; }
  .again { margin-top: 1.5rem; border-top: 1px solid var(--line); padding-top: .5rem; }
</style>
