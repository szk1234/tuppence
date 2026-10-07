<script lang="ts">
  let { message = '', kind = 'error' }: { message?: string; kind?: 'error' | 'ok' } = $props()
</script>

{#if kind === 'error'}
  <div class="notice error" role="alert">{#if message}{message}{/if}</div>
{:else}
  <div class="notice ok" role="status" aria-live="polite">{#if message}{message}{/if}</div>
{/if}

<style>
  .notice:empty { display: none; }
  .notice { margin: .75rem 0; padding: .6rem .8rem; border-radius: 8px; }
  .error { background: var(--danger-bg); color: var(--danger); }
  .ok { background: var(--ok-bg); color: var(--ok); }
</style>
