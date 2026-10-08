<script lang="ts">
  import { onMount } from 'svelte'
  import CategorySelect from '../components/CategorySelect.svelte'
  import Notice from '../components/Notice.svelte'
  import Treemap from '../components/Treemap.svelte'
  import WhyPanel from '../components/WhyPanel.svelte'
  import { api, ApiError } from '../lib/api'
  import { formatGBP } from '../lib/money'
  import { link } from '../lib/router.svelte'
  import { dmyDate } from '../lib/dates'
  import {
    correct, createRule, getAnalysis, getCategories, getRefiles, getSpending, getTransactions, runAnalysis,
    STATUS_LABELS, undoRefile, type AnalysisStatus, type Category, type Filters, type Refile, type RuleOffer,
    type SpendingView, type Tile, type Txn,
  } from '../lib/understanding'

  type Account = { id: string; nickname: string; provider_name: string }
  type Person = { id: string; display_name: string }

  let on = $state<string | null>(null)
  let category = $state<string | null>(null)
  let filters = $state<Filters>({ account_id: '', who: '', status: '' })
  let view = $state<SpendingView | null>(null)
  let rows = $state<Txn[]>([])
  let categories = $state<Category[]>([])
  let accounts = $state<Account[]>([])
  let people = $state<Person[]>([])
  let analysis = $state<AnalysisStatus | null>(null)
  let why = $state<string | null>(null)
  let unpairing = $state<string | null>(null)  // the row whose "Not a transfer" chooser is open
  let refiles = $state<Refile[]>([])
  let offer = $state<{ offer: RuleOffer; transactionId: string } | null>(null)
  let error = $state('')
  let saved = $state('')
  let busy = $state(false)  // a change is being saved
  let ready = $state(false)  // the lists and the first view have loaded
  const locked = $derived(busy || !ready)

  const fail = (err: unknown) => { saved = ''; error = err instanceof ApiError ? err.detail : 'Something went wrong.' }

  const kindOf = $derived(new Map(categories.map((c) => [c.id, c.kind])))
  const notTransfers = $derived(categories.filter((c) => c.kind !== 'transfer'))
  // Income, savings and transfers aren't spending: their lists open from the top-level figures.
  const outside = $derived(view && view.path.length > 1 ? kindOf.get(view.path[1].id ?? '') : undefined)
  const OUTSIDE: Record<string, string> = {
    income: "Money that came in isn't spending. If something here isn't income (a shop refund, say), change its category.",
    transfer: "Money moved to your own accounts, to savings or taken as cash isn't spending. If a payment here isn't one of these, choose “Not a transfer”.",
  }
  const named = (row: Txn) => `${row.merchant ?? row.description} on ${dmyDate(row.date)}`

  let token = 0  // the latest load wins: an older response never overwrites a newer one
  let epoch = $state(0)  // bumped after a failed save so each category select shows the saved value
  async function load() {
    const mine = ++token
    try {
      const [v, a, r] = await Promise.all([getSpending(on, category, filters), getAnalysis(), getRefiles()])
      const list = v.period && category ? await getTransactions(v.period.start, category, filters) : []
      if (mine !== token) return
      view = v
      analysis = a
      refiles = r
      on = v.period.start
      rows = list
    } catch (err) { if (mine === token) fail(err) }
  }

  /** A failed write: say why, then reload so versions and the shown values are the saved ones. */
  async function failed(err: unknown) {
    fail(err)
    await load()
    epoch += 1
  }

  onMount(async () => {
    const params = new URLSearchParams(window.location.search)  // e.g. /spending?on=2026-10-01
    on = params.get('on')
    category = params.get('category')
    try {
      const [c, a, p] = await Promise.all([
        getCategories(),
        api<{ accounts: Account[] }>('/api/accounts'),
        api<{ people: Person[] }>('/api/household/people'),
      ])
      categories = c
      accounts = a.accounts
      people = p.people
    } catch (err) { fail(err) }
    await load()
    ready = true
  })

  $effect(() => {
    if (!analysis || !(analysis.running || analysis.queued)) return
    const timer = setTimeout(load, 2000)
    return () => clearTimeout(timer)
  })

  function open(tile: Tile) {
    category = tile.id
    why = null
    unpairing = null
    offer = null
    error = ''
    load()
  }

  function goTo(id: string | null) {
    category = id
    why = null
    unpairing = null
    offer = null
    error = ''
    load()
  }

  function shift(day: string) {
    on = day
    offer = null
    error = ''
    load()
  }

  async function recategorise(row: Txn, categoryId: string) {
    if (locked) return
    saved = ''
    error = ''
    busy = true
    try {
      const result = await correct(row.id, { category_id: categoryId, expected_version: row.version })
      offer = result.rule_offer ? { offer: result.rule_offer, transactionId: row.id } : null
      const label = categories.find((c) => c.id === categoryId)?.label ?? 'that category'
      saved = `Filed under ${label}.`
      await load()
    } catch (err) { await failed(err) } finally { busy = false }
  }

  /** "Not a transfer": the person files it as what it really is; its pair is released. */
  async function notTransfer(row: Txn, categoryId: string) {
    if (locked) return
    saved = ''
    error = ''
    busy = true
    try {
      const result = await correct(row.id, { category_id: categoryId, is_transfer: false, expected_version: row.version })
      offer = result.rule_offer ? { offer: result.rule_offer, transactionId: row.id } : null
      unpairing = null
      const label = categories.find((c) => c.id === categoryId)?.label ?? 'that category'
      saved = `Filed under ${label}. It's no longer counted as a transfer.`
      await load()
    } catch (err) { await failed(err) } finally { busy = false }
  }

  /** Undo a split Tuppence made: the payments go back and the new sub-categories go. */
  async function undo(r: Refile) {
    if (locked) return
    saved = ''
    error = ''
    busy = true
    try {
      const out = await undoRefile(r.id)
      saved = `Undone: ${out.moved} payment${out.moved === 1 ? ' is' : 's are'} back in ${r.parent}.`
      categories = await getCategories()
      await load()
    } catch (err) { await failed(err) } finally { busy = false }
  }

  async function acceptOffer() {
    if (!offer || locked) return
    saved = ''
    error = ''
    busy = true
    try {
      const made = await createRule({
        merchant_id: offer.offer.merchant_id, set_category_id: offer.offer.category_id,
        created_from_transaction_id: offer.transactionId, apply_to_past: true,
      })
      saved = `Rule saved: ${made.changed} more payment${made.changed === 1 ? '' : 's'} now follow${made.changed === 1 ? 's' : ''} it.`
      offer = null
      await load()
    } catch (err) { await failed(err) } finally { busy = false }
  }

  async function runNow() {
    if (locked) return
    saved = ''
    error = ''
    busy = true
    try { await runAnalysis(); analysis = await getAnalysis() } catch (err) { fail(err) } finally { busy = false }
  }

  function setFilter(key: keyof Filters, value: string) {
    filters = { ...filters, [key]: value }
    error = ''
    load()
  }
</script>

<section>
  <h1>Spending</h1>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />
  {#if view}
    <div class="period">
      <button class="link" onclick={() => shift(view!.period.previous)} aria-label="Previous period">‹ Previous</button>
      <h2>{view.period.label}</h2>
      <button class="link" onclick={() => shift(view!.period.next)} aria-label="Next period">Next ›</button>
      <span class="meta">{view.period.mode === 'pay_cycle' ? 'Payday to payday' : 'Calendar month'}</span>
    </div>
    {#if analysis?.running}
      <p role="status">Sorting your transactions…</p>
    {:else}
      <p class="meta" role="status">
        {analysis?.queued ? 'New transactions are waiting to be sorted.' : (analysis?.last_run?.summary ?? '')}
        <button class="link" onclick={runNow} disabled={locked}>Run analysis now</button>
      </p>
    {/if}
    {#if view.waiting_for_ai}
      <p class="notice error" role="alert">{view.waiting_for_ai} transactions are waiting for an AI model. Choose one in <a href="/settings/ai" onclick={link}>Settings › AI</a>.</p>
    {/if}
    <div class="row filters">
      <div><label for="f-account">Account</label>
        <select id="f-account" disabled={locked} value={filters.account_id} onchange={(e) => setFilter('account_id', e.currentTarget.value)}>
          <option value="">All accounts</option>
          {#each accounts as a (a.id)}<option value={a.id}>{a.nickname} ({a.provider_name})</option>{/each}
        </select></div>
      <div><label for="f-who">Who for</label>
        <select id="f-who" disabled={locked} value={filters.who} onchange={(e) => setFilter('who', e.currentTarget.value)}>
          <option value="">Everyone</option>
          <option value="household">The household</option>
          {#each people as p (p.id)}<option value={p.id}>{p.display_name}</option>{/each}
        </select></div>
      <div><label for="f-status">Show</label>
        <select id="f-status" disabled={locked} value={filters.status} onchange={(e) => setFilter('status', e.currentTarget.value)}>
          <option value="">Everything</option>
          <option value="unknown">Not sorted yet</option>
          <option value="guessed">Best guesses</option>
        </select></div>
    </div>
    <nav aria-label="Breadcrumb">
      <ol class="crumbs">
        {#each view.path as crumb, i (crumb.id ?? 'all')}
          <li>{#if i < view.path.length - 1}<button class="link" onclick={() => goTo(crumb.id)}>{crumb.label}</button>{:else}<span aria-current="page">{crumb.label}</span>{/if}</li>
        {/each}
      </ol>
    </nav>
    {#if view.path.length === 1}
      <p><strong>{formatGBP(view.total)}</strong> spent
        · <button class="link" onclick={() => goTo('income')}>{formatGBP(view.money_in)} came in</button>
        · <button class="link" onclick={() => goTo('savings')}>{formatGBP(view.saved)} saved or invested</button>
        · <button class="link" onclick={() => goTo('transfers')}>{formatGBP(view.moved)} moved between your accounts or taken as cash</button></p>
    {:else if outside === 'income' || outside === 'transfer'}
      <p>{OUTSIDE[outside]}</p>
    {:else}
      <p><strong>{formatGBP(view.total)}</strong> spent</p>
    {/if}
    {#if view.tiles.length}
      <Treemap tiles={view.tiles} total={view.total} onopen={open} />
      <table class="list">
        <caption class="visually-hidden">Spending by category, as a table</caption>
        <thead><tr><th scope="col">Category</th><th scope="col" class="num">Spent</th><th scope="col" class="num">Payments</th></tr></thead>
        <tbody>
          {#each view.tiles as t (t.id)}
            <tr><td><button class="link" onclick={() => open(t)}>{t.label}</button></td><td class="num">{formatGBP(t.amount)}</td><td class="num">{t.count}</td></tr>
          {/each}
        </tbody>
      </table>
    {:else if !category}
      <p>No spending in this period yet. Add statements on the <a href="/statements" onclick={link}>Statements page</a>.</p>
    {/if}
    {#if offer}
      <div class="card" role="status">
        <p>{offer.offer.will_change} other payment{offer.offer.will_change === 1 ? '' : 's'} to {offer.offer.merchant_name} would change too.</p>
        <button onclick={acceptOffer} disabled={locked}>Apply to all from {offer.offer.merchant_name}</button>
        <button class="link" onclick={() => (offer = null)}>Not now</button>
      </div>
    {/if}
    {#if category}
      <div class="scroll">
        <table>
          <caption>Transactions</caption>
          <thead><tr><th scope="col">Date</th><th scope="col">Description</th><th scope="col" class="num">Amount</th><th scope="col">Category</th><th scope="col"><span class="visually-hidden">Details</span></th></tr></thead>
          <tbody>
            {#each rows as row (row.id)}
              <tr>
                <td>{dmyDate(row.date)}</td>
                <td>{row.merchant ?? row.description}<br /><span class="meta">{STATUS_LABELS[row.status]}</span></td>
                <td class="num">{formatGBP(row.amount)}</td>
                <td>{#key epoch}<CategorySelect {categories} disabled={locked} value={row.category_id} label={`Category for ${named(row)}`} onchange={(id) => recategorise(row, id)} />{/key}
                  {#if kindOf.get(row.category_id ?? '') === 'transfer'}
                    <br /><button class="link" disabled={locked} onclick={() => (unpairing = unpairing === row.id ? null : row.id)} aria-label={`Not a transfer: ${named(row)}`}>Not a transfer</button>
                  {/if}</td>
                <td><button class="link" onclick={() => (why = row.id)} aria-label={`Why? ${row.merchant ?? row.description} on ${dmyDate(row.date)}`}>Why?</button></td>
              </tr>
              {#if unpairing === row.id}
                <tr><td colspan="5"><div class="card">
                  <p>What is it instead? Tuppence will stop counting it as money moving between your accounts.</p>
                  {#key epoch}<CategorySelect categories={notTransfers} disabled={locked} value={null} label={`What is ${named(row)} instead?`} onchange={(id) => notTransfer(row, id)} />{/key}
                  <button class="link" onclick={() => (unpairing = null)}>Cancel</button>
                </div></td></tr>
              {/if}
              {#if why === row.id}
                <tr><td colspan="5"><WhyPanel id={row.id} onclose={() => (why = null)} onchanged={() => { why = null; load() }} /></td></tr>
              {/if}
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
    {#if view.path.length === 1 && refiles.length}
      <section class="card" aria-labelledby="refiles-heading">
        <h2 id="refiles-heading">Sub-categories Tuppence added</h2>
        <p class="meta">When a category gets crowded, Tuppence splits it. Undo puts the payments back and removes the new sub-categories (one you've filed a payment into yourself stays).</p>
        <ul class="refiles">
          {#each refiles as r (r.id)}
            <li>Split {r.parent} into {r.created.join(', ')} · moved {r.moved} payment{r.moved === 1 ? '' : 's'} · {dmyDate(r.created_at)}
              <button class="link" disabled={locked} onclick={() => undo(r)} aria-label={`Undo: the split of ${r.parent}`}>Undo</button></li>
          {/each}
        </ul>
      </section>
    {/if}
    <p class="meta"><a href="/settings/rules" onclick={link}>Your rules</a></p>
  {/if}
</section>

<style>
  .period { display: flex; gap: 1rem; align-items: baseline; flex-wrap: wrap; }
  .period h2 { margin: 0; }
  .crumbs { display: flex; flex-wrap: wrap; gap: .25rem; list-style: none; padding: 0; }
  .crumbs li + li::before { content: '›'; margin-right: .25rem; color: var(--muted); }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; }
  th, td { padding: .35rem .5rem; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
  .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  caption { text-align: left; font-weight: 600; padding: .5rem 0; }
  .list { margin-top: .75rem; }
  .filters > div { min-width: 10rem; }
  .refiles { list-style: none; padding: 0; }
  .refiles li { padding: .4rem 0; border-bottom: 1px solid var(--line); }
</style>
