<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../../components/Notice.svelte'
  import { api, ApiError } from '../../lib/api'

  type Person = { id: string; display_name: string; role: 'adult' | 'child' | 'dependent_adult'; birth_year: number | null; status: string; version: number }
  type Household = { nation: string | null; postcode_district: string | null; currency: string; period_mode: string; period_anchor_person_id: string | null; version: number }

  const ROLES = { adult: 'Adult', child: 'Child', dependent_adult: 'Dependent adult' }
  const NATIONS = { england: 'England', wales: 'Wales', scotland: 'Scotland', northern_ireland: 'Northern Ireland' }

  let household = $state<Household | null>(null)
  let people = $state<Person[]>([])
  let nation = $state('')
  let district = $state('')
  let name = $state('')
  let role = $state<Person['role']>('adult')
  let birthYear = $state('')
  let error = $state('')
  let saved = $state('')
  let yearError = $state('')
  let adding = $state(false)
  let loaded = $state(false)

  const fail = (err: unknown) => { saved = ''; error = err instanceof ApiError ? err.detail : 'Something went wrong.' }

  async function load() {
    household = await api<Household>('/api/household')
    nation = household.nation ?? ''
    district = household.postcode_district ?? ''
    people = (await api<{ people: Person[] }>('/api/household/people')).people
    loaded = true
  }
  onMount(() => { load().catch(fail) })

  async function saveHousehold(e: SubmitEvent) {
    e.preventDefault(); error = ''
    try {
      household = await api<Household>('/api/household', {
        method: 'PATCH',
        body: { changes: { nation: nation || null, postcode_district: district || null }, expected_version: household!.version },
      })
      district = household.postcode_district ?? ''
      saved = 'Household saved.'
    } catch (err) { fail(err) }
  }

  async function addPerson(e: SubmitEvent) {
    e.preventDefault(); error = ''; yearError = ''
    if (birthYear && !/^\d{4}$/.test(birthYear.trim())) { yearError = 'Enter a 4-digit year'; return }
    adding = true
    try {
      const body: Record<string, unknown> = { display_name: name, role }
      if (birthYear) body.birth_year = Number(birthYear.trim())
      const p = await api<Person>('/api/household/people', { method: 'POST', body })
      people = [...people, p]; name = ''; birthYear = ''; role = 'adult'
      saved = `${p.display_name} added.`
    } catch (err) { fail(err) } finally { adding = false }
  }

  async function retire(p: Person) {
    error = ''
    try {
      await api(`/api/household/people/${p.id}/retire`, { method: 'POST', body: { expected_version: p.version } })
      people = people.filter((x) => x.id !== p.id)
      saved = `${p.display_name} removed from the household.`
    } catch (err) { fail(err) }
  }
</script>

<section>
  <h1>Household</h1>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />

  <form class="card" onsubmit={saveHousehold} aria-busy={!loaded}>
    <h2>Where you live</h2>
    <fieldset class="bare" disabled={!loaded}>
    <label for="hh-nation">Nation</label>
    <select id="hh-nation" bind:value={nation}>
      <option value="">Choose…</option>
      {#each Object.entries(NATIONS) as [value, label]}<option {value}>{label}</option>{/each}
    </select>
    <label for="hh-district">Postcode district</label>
    <input id="hh-district" placeholder="e.g. LS6" bind:value={district} autocomplete="off" />
    <p class="hint">Only the first part of your postcode. Tuppence never needs your full address.</p>
    <button type="submit">Save household</button>
    </fieldset>
  </form>

  <div class="card">
    <h2>People</h2>
    {#if people.length === 0}<p>No one added yet.</p>{/if}
    <ul class="people">
      {#each people as p (p.id)}
        <li>
          <span class="name">{p.display_name}</span>
          <span class="meta">{ROLES[p.role]}{p.birth_year ? ` · born ${p.birth_year}` : ''}</span>
          <button class="link" onclick={() => retire(p)} aria-label={`Remove ${p.display_name}`}>Remove</button>
        </li>
      {/each}
    </ul>
    <form onsubmit={addPerson} aria-busy={adding}>
      <fieldset disabled={adding || !loaded} class="row bare">
      <div><label for="np-name">Name</label><input id="np-name" required maxlength="60" bind:value={name} /></div>
      <div>
        <label for="np-role">Role</label>
        <select id="np-role" bind:value={role}>
          {#each Object.entries(ROLES) as [value, label]}<option {value}>{label}</option>{/each}
        </select>
      </div>
      <div><label for="np-year">Birth year (children)</label><input id="np-year" inputmode="numeric" bind:value={birthYear} aria-invalid={yearError ? 'true' : undefined} aria-describedby={yearError ? 'np-year-err' : undefined} />{#if yearError}<span id="np-year-err" class="warn" role="alert">{yearError}</span>{/if}</div>
      <button type="submit">Add person</button>
      </fieldset>
    </form>
  </div>
</section>
