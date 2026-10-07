<script lang="ts">
  import { onMount } from 'svelte'
  import Nav from './components/Nav.svelte'
  import { router } from './lib/router.svelte'
  import { loadSession, session } from './lib/session.svelte'
  import Home from './pages/Home.svelte'
  import LaunchExpired from './pages/LaunchExpired.svelte'
  import Login from './pages/Login.svelte'
  import NotFound from './pages/NotFound.svelte'
  import Setup from './pages/Setup.svelte'
  import Agents from './pages/settings/Agents.svelte'
  import Household from './pages/settings/Household.svelte'

  const routes: Record<string, typeof Home> = { '/': Home, '/settings/household': Household, '/settings/agents': Agents }
  let failed = $state(false)
  onMount(() => { loadSession().catch(() => { failed = true }) })
  const Page = $derived(routes[router.path] ?? NotFound)
</script>

{#if failed}
  <main><p class="status err">Can't reach the Tuppence service. Is it running?</p></main>
{:else if !session.loaded}
  <main><p class="status">Loading…</p></main>
{:else if !session.authenticated}
  <main>
    {#if session.mode !== 'server'}<LaunchExpired />
    {:else if session.needsSetup}<Setup />
    {:else}<Login />{/if}
  </main>
{:else}
  <Nav />
  <main><Page /></main>
{/if}
