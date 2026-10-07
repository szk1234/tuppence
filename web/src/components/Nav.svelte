<script lang="ts">
  import { link, router } from '../lib/router.svelte'
  import { session, signOut } from '../lib/session.svelte'
  import Notice from './Notice.svelte'
  let navError = $state('')
  async function out() {
    navError = ''
    try { await signOut() } catch { navError = 'Could not sign out. Try again.' }
  }
  const items = [
    { href: '/', label: 'Home' },
    { href: '/settings/household', label: 'Household' },
    { href: '/settings/agents', label: 'Agents' },
    { href: '/settings/ai', label: 'AI' },
    { href: '/settings/privacy', label: 'Privacy' },
    { href: '/usage', label: 'Usage' },
  ]
</script>

<nav aria-label="Main">
  <a class="brand" href="/" onclick={link}>Tuppence</a>
  <ul>
    {#each items as item}
      <li><a href={item.href} onclick={link} aria-current={router.path === item.href ? 'page' : undefined}>{item.label}</a></li>
    {/each}
  </ul>
  {#if session.mode === 'server' && session.user}
    <button class="link" onclick={out}>Sign out {session.user.username}</button>
  {/if}
</nav>
<Notice message={navError} />

<style>
  nav { display: flex; gap: 1.5rem; align-items: center; padding: .75rem 1.25rem; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .brand { font-weight: 700; font-size: 1.15rem; text-decoration: none; color: var(--ink); }
  ul { display: flex; gap: 1rem; list-style: none; margin: 0; padding: 0; flex: 1; }
  a[aria-current='page'] { font-weight: 600; text-decoration-thickness: 2px; }
  .link { background: none; border: none; color: var(--accent); cursor: pointer; font: inherit; }
</style>
