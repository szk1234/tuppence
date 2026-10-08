<script lang="ts">
  import UploadDropzone from '../../components/UploadDropzone.svelte'
  import { link } from '../../lib/router.svelte'

  let added = $state(0)
</script>

<p>Drop in your last 3 months of statements. Bank and card exports are read on this device; PDFs and screenshots are read by the AI model you chose in the last step.</p>
<UploadDropzone onuploaded={(statements) => (added += statements.filter((s) => !s.duplicate).length)} />
{#if added}
  <p role="status">
    {added} file{added === 1 ? '' : 's'} added and being read now. Follow their progress on the
    <a href="/statements" onclick={link}>Statements page</a>.
  </p>
{/if}
