<script lang="ts">
  import Notice from './Notice.svelte'
  import { ApiError } from '../lib/api'
  import { uploadStatements, type StatementView } from '../lib/statements'

  let { onuploaded = () => {} }: { onuploaded?: (statements: StatementView[]) => void } = $props()

  const ACCEPT = '.csv,.txt,.ofx,.qfx,.qif,.xml,.xlsx,.pdf,.png,.jpg,.jpeg'
  let dragging = $state(false)
  let busy = $state(false)
  let error = $state('')
  let rejected = $state<{ filename: string; reason: string }[]>([])
  let input = $state<HTMLInputElement>()

  async function send(list: FileList | null | undefined) {
    const files = Array.from(list ?? [])
    if (!files.length) return
    busy = true
    error = ''
    rejected = []
    try {
      const result = await uploadStatements(files)
      rejected = result.rejected
      onuploaded(result.statements)
    } catch (err) {
      error = err instanceof ApiError ? err.detail : 'The upload failed. Try again.'
    } finally {
      busy = false
      if (input) input.value = ''
    }
  }

  function drop(event: DragEvent) {
    event.preventDefault()
    dragging = false
    send(event.dataTransfer?.files)
  }
</script>

<section
  class="drop"
  class:dragging
  aria-label="Upload statements"
  ondragover={(e) => { e.preventDefault(); dragging = true }}
  ondragleave={() => (dragging = false)}
  ondrop={drop}
>
  <p class="lead">Drop your statements here</p>
  <p class="hint">CSV, OFX, QIF, CAMT.053, Excel (.xlsx), PDF or a screenshot. Up to 20 files at a time.</p>
  <label class="button" for="statement-files">Choose files</label>
  <input
    id="statement-files"
    class="visually-hidden"
    type="file"
    multiple
    accept={ACCEPT}
    disabled={busy}
    bind:this={input}
    onchange={(e) => send((e.currentTarget as HTMLInputElement).files)}
  />
  {#if busy}<p role="status">Uploading…</p>{/if}
</section>
<Notice message={error} />
{#if rejected.length}
  <div class="notice error" role="alert">
    <p>These files weren't added:</p>
    <ul>
      {#each rejected as item}<li><strong>{item.filename}</strong>: {item.reason}</li>{/each}
    </ul>
  </div>
{/if}

<style>
  .drop { border: 2px dashed var(--line); border-radius: 12px; padding: 1.5rem; text-align: center; background: var(--panel); }
  .drop.dragging { border-color: var(--accent); }
  .lead { font-weight: 600; font-size: 1.1rem; margin: 0; }
</style>
