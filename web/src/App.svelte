<script lang="ts">
  import { onMount } from 'svelte'
  import Nav from './components/Nav.svelte'
  import { api } from './lib/api'
  import { navigate, router } from './lib/router.svelte'
  import { loadSession, session } from './lib/session.svelte'
  import Home from './pages/Home.svelte'
  import LaunchExpired from './pages/LaunchExpired.svelte'
  import Login from './pages/Login.svelte'
  import NotFound from './pages/NotFound.svelte'
  import Setup from './pages/Setup.svelte'
  import Agents from './pages/settings/Agents.svelte'
  import AI from './pages/settings/AI.svelte'
  import Accounts from './pages/settings/Accounts.svelte'
  import Debts from './pages/settings/Debts.svelte'
  import Goals from './pages/settings/Goals.svelte'
  import Household from './pages/settings/Household.svelte'
  import Income from './pages/settings/Income.svelte'
  import Privacy from './pages/settings/Privacy.svelte'
  import Timeline from './pages/settings/Timeline.svelte'
  import StatementDetail from './pages/StatementDetail.svelte'
  import Statements from './pages/Statements.svelte'
  import { statementIdFromPath } from './lib/statements'
  import Usage from './pages/Usage.svelte'
  import Welcome from './pages/Welcome.svelte'

  const routes: Record<string, typeof Home> = { '/': Home, '/login': Home, '/setup': Home, '/settings/household': Household, '/settings/accounts': Accounts,
    '/settings/income': Income, '/settings/debts': Debts, '/settings/goals': Goals, '/settings/timeline': Timeline, '/settings/agents': Agents,
    '/settings/ai': AI, '/settings/privacy': Privacy, '/usage': Usage, '/welcome': Welcome, '/statements': Statements }
  let failed = $state(false)
  onMount(() => { loadSession().catch(() => { failed = true }) })

  // Once per sign-in: a household that hasn't started onboarding lands on the wizard, but only when it arrives at Home
  // (a deep link stays put, and a failed lookup leaves the user where they asked to be).
  let gateChecked = false
  $effect(() => {
    if (!session.authenticated) { gateChecked = false; return }
    const path = router.path
    if (gateChecked || path === '/login' || path === '/setup') return
    gateChecked = true
    if (path !== '/') return
    api<{ started?: unknown }>('/api/onboarding')
      .then((o) => { if (o?.started === false && router.path === '/') navigate('/welcome') })
      .catch(() => {})
  })
  const statementPath = $derived(statementIdFromPath(router.path))
  const Page = $derived(statementPath === 'bad' ? NotFound : (routes[router.path] ?? NotFound))
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
  <main>
    {#if typeof statementPath === 'object'}
      {#key statementPath.id}<StatementDetail id={statementPath.id} />{/key}
    {:else}
      <Page />
    {/if}
  </main>
{/if}
