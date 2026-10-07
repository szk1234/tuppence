<script lang="ts">
  import type { Person } from '../../lib/types'

  type Body = { display_name: string; role: Person['role']; birth_year?: number }
  let { disabled = false, submitLabel = 'Add person', onsubmit }: {
    disabled?: boolean; submitLabel?: string; onsubmit: (body: Body) => Promise<boolean>
  } = $props()
  const uid = $props.id()

  const ROLES = { adult: 'Adult', child: 'Child', dependent_adult: 'Dependent adult' }
  let name = $state('')
  let role = $state<Person['role']>('adult')
  let birthYear = $state('')
  let yearError = $state('')
  let saving = $state(false)

  async function submit(e: SubmitEvent) {
    e.preventDefault(); yearError = ''
    if (birthYear && !/^\d{4}$/.test(birthYear.trim())) { yearError = 'Enter a 4-digit year'; return }
    saving = true
    try {
      const body: Body = { display_name: name, role }
      if (birthYear) body.birth_year = Number(birthYear.trim())
      if (await onsubmit(body)) { name = ''; birthYear = ''; role = 'adult' }
    } finally { saving = false }
  }
</script>

<form onsubmit={submit} aria-busy={saving}>
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
    <button type="submit">{submitLabel}</button>
  </fieldset>
</form>
