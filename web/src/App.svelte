<script lang="ts">
  import { onMount } from 'svelte'
  import { fetchHealth, type Health } from './lib/health'

  let health = $state<Health | null>(null)
  let error = $state(false)

  onMount(async () => {
    try {
      health = await fetchHealth()
    } catch {
      error = true
    }
  })
</script>

<main>
  <h1>Tuppence</h1>
  <p class="tagline">A private AI money coach for UK households. Your statements never leave your machine.</p>
  {#if health}
    <p class="status ok">Connected · v{health.version} · {health.mode}</p>
  {:else if error}
    <p class="status err">Can't reach the Tuppence service. Is it running?</p>
  {:else}
    <p class="status">Connecting…</p>
  {/if}
</main>

<style>
  main { font-family: system-ui, sans-serif; max-width: 44rem; margin: 4rem auto; padding: 0 1rem; }
  h1 { font-size: 2.5rem; margin: 0 0 .5rem; }
  .tagline { color: #555; }
  .status { margin-top: 2rem; font-size: .95rem; }
  .ok { color: #1b7a3a; }
  .err { color: #a02020; }
</style>
