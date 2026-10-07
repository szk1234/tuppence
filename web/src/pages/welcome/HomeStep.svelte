<script lang="ts">
  import { onMount } from 'svelte'
  import HomeDetailsForm from '../../components/forms/HomeDetailsForm.svelte'
  import { api } from '../../lib/api'
  import type { Household } from '../../lib/types'

  let nation = $state<string | null>(null)
  onMount(async () => {
    try { nation = (await api<Household>('/api/household')).nation } catch { /* Without a nation, all council tax bands are offered. */ }
  })
</script>

<p>Where you live shapes your bills and benefits. These details apply from the date you choose, so moving home later keeps your history.</p>
<HomeDetailsForm {nation} />
