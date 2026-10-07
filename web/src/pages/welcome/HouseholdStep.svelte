<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { errorText } from '../../lib/form'
  import type { Person } from '../../lib/types'

  const uid = $props.id()
  const ROLES = { adult: 'Adult', child: 'Child', dependent_adult: 'Dependent adult' }
  let people = $state<Person[]>([])
  let loaded = $state(false)
  let family = $state(false)
  let youName = $state('You')
  let partnerName = $state('')
  let depName = $state('')
  let depRole = $state<'child' | 'dependent_adult'>('child')
  let depYear = $state('')
  let yearError = $state('')
  let error = $state('')
  let saved = $state('')
  let busy = $state(false)

  const adults = $derived(people.filter((p) => p.role === 'adult'))
  const others = $derived(people.filter((p) => p.role !== 'adult'))

  onMount(async () => {
    try {
      people = (await api<{ people: Person[] }>('/api/household/people')).people
      family = people.filter((p) => p.role === 'adult').length > 1
      loaded = true
    } catch (err) { error = errorText(err) }
  })

  async function add(body: { display_name: string; role: Person['role']; birth_year?: number }): Promise<boolean> {
    error = ''; saved = ''
    busy = true
    try {
      const p = await api<Person>('/api/household/people', { method: 'POST', body })
      people = [...people, p]
      saved = `${p.display_name} added.`
      return true
    } catch (err) { error = errorText(err); return false } finally { busy = false }
  }

  async function addYou(e: SubmitEvent) {
    e.preventDefault()
    await add({ display_name: youName.trim() || 'You', role: 'adult' })
  }

  async function addPartnerNow(): Promise<boolean> {
    if (!partnerName.trim()) return true
    const ok = await add({ display_name: partnerName.trim(), role: 'adult' })
    if (ok) partnerName = ''
    return ok
  }
  async function addPartner(e: SubmitEvent) { e.preventDefault(); await addPartnerNow() }

  async function addDependantNow(): Promise<boolean> {
    yearError = ''
    if (!depName.trim()) return true
    if (depYear && !/^\d{4}$/.test(depYear.trim())) { yearError = 'Enter a 4-digit year'; return false }
    const body: { display_name: string; role: Person['role']; birth_year?: number } = { display_name: depName.trim(), role: depRole }
    if (depYear) body.birth_year = Number(depYear.trim())
    const ok = await add(body)
    if (ok) { depName = ''; depYear = '' }
    return ok
  }
  async function addDependant(e: SubmitEvent) { e.preventDefault(); await addDependantNow() }

  /**
   * Continue: the signed-in adult always ends up in the household, and a partner or dependant typed but not added is
   * added. Back only keeps what was typed (it never creates "You").
   */
  export async function save(intent: 'continue' | 'back' = 'continue'): Promise<boolean> {
    if (!loaded) return intent === 'back'
    if (intent === 'continue' && adults.length === 0 && !(await add({ display_name: youName.trim() || 'You', role: 'adult' }))) return false
    if (family && !(await addPartnerNow())) return false
    return addDependantNow()
  }
</script>

<Notice message={error} />
<Notice message={saved} kind="ok" />

<fieldset class="bare" disabled={!loaded || busy}>
  <legend>Is this just you, or a family?</legend>
  <div class="toggle"><label><input type="radio" name={`${uid}-who`} checked={!family} onchange={() => (family = false)} /> Just me</label></div>
  <div class="toggle"><label><input type="radio" name={`${uid}-who`} checked={family} onchange={() => (family = true)} /> Family</label></div>
</fieldset>

<div class="card">
  <h2>Adults</h2>
  <ul class="people">
    {#each adults as p (p.id)}<li><span class="name">{p.display_name}</span><span class="meta">{ROLES[p.role]}</span></li>{/each}
  </ul>
  {#if loaded && adults.length === 0}
    <form onsubmit={addYou}>
      <fieldset class="row bare" disabled={busy}>
        <div><label for={`${uid}-you`}>Your name</label><input id={`${uid}-you`} maxlength="60" bind:value={youName} /></div>
        <button type="submit">Add me</button>
      </fieldset>
    </form>
  {/if}
  {#if family && adults.length > 0}
    <form onsubmit={addPartner}>
      <fieldset class="row bare" disabled={busy}>
        <div><label for={`${uid}-partner`}>Partner's name</label><input id={`${uid}-partner`} required maxlength="60" bind:value={partnerName} /></div>
        <button type="submit">Add partner</button>
      </fieldset>
    </form>
  {/if}
</div>

<div class="card">
  <h2>Any children or dependants?</h2>
  {#if others.length}
    <ul class="people">
      {#each others as p (p.id)}<li><span class="name">{p.display_name}</span><span class="meta">{ROLES[p.role]}{p.birth_year ? ` · born ${p.birth_year}` : ''}</span></li>{/each}
    </ul>
  {/if}
  <form onsubmit={addDependant}>
    <fieldset class="row bare" disabled={busy || adults.length === 0}>
      <div><label for={`${uid}-dname`}>Name</label><input id={`${uid}-dname`} required maxlength="60" bind:value={depName} /></div>
      <div>
        <label for={`${uid}-drole`}>Role</label>
        <select id={`${uid}-drole`} bind:value={depRole}>
          <option value="child">Child</option>
          <option value="dependent_adult">Dependent adult</option>
        </select>
      </div>
      <div>
        <label for={`${uid}-dyear`}>Birth year (children)</label>
        <input id={`${uid}-dyear`} inputmode="numeric" bind:value={depYear} aria-invalid={yearError ? 'true' : undefined} />
        {#if yearError}<span class="warn" role="alert">{yearError}</span>{/if}
      </div>
      <button type="submit">Add dependant</button>
    </fieldset>
  </form>
  <p class="hint">Add your first adult above before adding dependants. You can change who is in the household any time in Settings.</p>
</div>
