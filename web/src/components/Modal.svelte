<script lang="ts">
  import type { Snippet } from 'svelte'
  let { open = false, title, children, onclose }: { open?: boolean; title: string; children: Snippet; onclose: () => void } = $props()
  let dialog: HTMLDialogElement
  const titleId = `modal-title-${Math.random().toString(36).slice(2, 8)}`

  let opener: HTMLElement | null = null
  let heading = $state<HTMLElement | undefined>()

  $effect(() => {
    if (!dialog) return
    const canShow = typeof dialog.showModal === 'function'
    if (open && !dialog.hasAttribute('open')) {
      opener = document.activeElement as HTMLElement | null
      if (canShow) dialog.showModal()
      else dialog.setAttribute('open', '')
      queueMicrotask(() => heading?.focus())
    } else if (!open && dialog.hasAttribute('open')) {
      if (typeof dialog.close === 'function') dialog.close()
      else dialog.removeAttribute('open')
      if (opener && opener.isConnected) opener.focus()
      opener = null
    }
  })

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.preventDefault(); onclose() }
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<dialog bind:this={dialog} aria-labelledby={titleId} onkeydown={onKey} oncancel={(e) => { e.preventDefault(); onclose() }}>
  {#if open}
    <h2 id={titleId} tabindex="-1" bind:this={heading}>{title}</h2>
    {@render children()}
  {/if}
</dialog>

<style>
  dialog { border: 1px solid var(--line); border-radius: 12px; padding: 1.25rem; max-width: 32rem; background: var(--panel); color: var(--ink); }
  dialog::backdrop { background: rgb(0 0 0 / .4); }
</style>
