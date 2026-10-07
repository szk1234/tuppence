<script lang="ts">
  import { onMount } from 'svelte'
  import HomeDetailsForm from '../../components/forms/HomeDetailsForm.svelte'
  import { api } from '../../lib/api'
  import type { Household } from '../../lib/types'

  let form = $state<{ save: () => Promise<boolean> }>()
  let nationLoaded = $state(false)
  let nation = $state<string | null>(null)
  onMount(async () => {
    try { nation = (await api<Household>('/api/household')).nation } catch { /* Without a nation, all council tax bands are offered. */ }
    nationLoaded = true
  })
  /** Continue saves whatever is typed; nothing typed means nothing to change. */
  export async function save(): Promise<boolean> {
    return form ? form.save() : true
  }
</script>

<p>Where you live shapes your bills and benefits. These details apply from the date you choose, so moving home later keeps your history.</p>
{#if nationLoaded}<HomeDetailsForm bind:this={form} {nation} />{/if}
