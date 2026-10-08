<script lang="ts">
  import { onMount } from 'svelte'
  import { fetchHealth, type Health } from '../lib/health'
  import CompletenessCard from '../components/CompletenessCard.svelte'
  import HomeCards from '../components/HomeCards.svelte'
  import { link } from '../lib/router.svelte'
  let health = $state<Health | null>(null)
  let error = $state(false)
  onMount(async () => { try { health = await fetchHealth() } catch { error = true } })
</script>

<section>
  <h1>Tuppence</h1>
  <p class="tagline">A private AI money coach for UK households. With a local model, your statements never leave your machine.</p>
  {#if health}<p class="status ok">Connected · v{health.version} · {health.mode}</p>
  {:else if error}<p class="status err">Can't reach the Tuppence service. Is it running?</p>
  {:else}<p class="status">Connecting…</p>{/if}
  <HomeCards />
  <CompletenessCard />
  <p>Start by adding the people in your household in <a href="/settings/household" onclick={link}>Household</a>.</p>
</section>
