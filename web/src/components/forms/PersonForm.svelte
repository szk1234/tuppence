<script lang="ts">
  import { untrack } from 'svelte'
  import { changes } from '../../lib/form'
  import type { Person } from '../../lib/types'

  type Body = { display_name?: string; role?: Person['role']; birth_year?: number | null }
  /** Adds a person, or with `initial` edits one: then only the changed fields are sent (a blank birth year clears it). */
  let { initial = null, disabled = false, submitLabel, onsubmit, oncancel }: {
    initial?: Person | null; disabled?: boolean; submitLabel?: string; onsubmit: (body: Body) => Promise<boolean>; oncancel?: () => void
  } = $props()
  const uid = $props.id()
  const p = untrack(() => initial)

  const ROLES = { adult: 'Adult', child: 'Child', dependent_adult: 'Dependent adult' }
  let name = $state(p?.display_name ?? '')
  let role = $state<Person['role']>(p?.role ?? 'adult')
  let birthYear = $state(p?.birth_year != null ? String(p.birth_year) : '')
  let yearError = $state('')
  let problem = $state('')
  let saving = $state(false)

  async function submit(e: SubmitEvent) {
    e.preventDefault(); yearError = ''; problem = ''
    if (birthYear.trim() && !/^\d{4}$/.test(birthYear.trim())) { yearError = 'Enter a 4-digit year'; return }
    if (p && !name.trim()) { problem = 'Enter a name.'; return }
    const year = birthYear.trim() ? Number(birthYear.trim()) : null
    saving = true
    try {
      if (p) {
        const diff = changes({ display_name: p.display_name, role: p.role, birth_year: p.birth_year },
          { display_name: name.trim(), role, birth_year: year })
        await onsubmit(diff as Body)
        return
      }
      const body: Body = { display_name: name, role }
      if (year !== null) body.birth_year = year
      if (await onsubmit(body)) { name = ''; birthYear = ''; role = 'adult' }
    } finally { saving = false }
  }
</script>

<form onsubmit={submit} aria-busy={saving}>
  {#if problem}<p class="warn" role="alert">{problem}</p>{/if}
  <fieldset disabled={saving || disabled} class="row bare">
    <div><label for={`${uid}-name`}>Name</label><input id={`${uid}-name`} required maxlength="60" bind:value={name} /></div>
    <div>
      <label for={`${uid}-role`}>Role</label>
      <select id={`${uid}-role`} bind:value={role}>
        {#each Object.entries(ROLES) as [value, text]}<option {value}>{text}</option>{/each}
      </select>
    </div>
    <div>
      <label for={`${uid}-year`}>Birth year (children)</label>
      <input id={`${uid}-year`} inputmode="numeric" bind:value={birthYear} aria-invalid={yearError ? 'true' : undefined} aria-describedby={yearError ? `${uid}-year-err` : undefined} />
      {#if yearError}<span id={`${uid}-year-err`} class="warn" role="alert">{yearError}</span>{/if}
    </div>
    <button type="submit">{submitLabel ?? (p ? 'Save person' : 'Add person')}</button>
    {#if oncancel}<button type="button" class="link" onclick={oncancel}>Cancel</button>{/if}
  </fieldset>
</form>
