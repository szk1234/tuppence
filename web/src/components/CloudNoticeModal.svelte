<script lang="ts">
  import type { Connection } from '../lib/llm'
  import Modal from './Modal.svelte'

  /** The one-off notice before a cloud connection first sees statement text. Shared by Settings > AI and the wizard. */
  let { connection, onconfirm, oncancel }: { connection: Connection | null; onconfirm: () => void; oncancel: () => void } = $props()
</script>

<Modal open={connection !== null} title={`Before you use ${connection?.name ?? ''}`} onclose={oncancel}>
  <p><strong>{connection?.name} will see the statement text Tuppence sends to it.</strong> It's covered by {connection?.name}'s own privacy terms. Every call is listed in Settings › Privacy.</p>
  <div class="row">
    <button type="button" onclick={onconfirm}>I understand</button>
    <button type="button" onclick={oncancel}>Cancel</button>
  </div>
</Modal>
