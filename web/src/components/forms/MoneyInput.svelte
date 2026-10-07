<script lang="ts">
  import { parsePoundsInput } from '../../lib/money'
  let { label, value = $bindable(''), required = false, hint = '' }: { label: string; value?: string; required?: boolean; hint?: string } = $props()
  const uid = $props.id()
  const invalid = $derived(value.trim() !== '' && parsePoundsInput(value) === null)
</script>

<div class="money">
  <label for={`${uid}-in`}>{label}</label>
  <span class="wrap">
    <span class="prefix" aria-hidden="true">£</span>
    <input id={`${uid}-in`} inputmode="decimal" autocomplete="off" {required} bind:value
      aria-invalid={invalid ? 'true' : undefined} aria-describedby={invalid ? `${uid}-err` : hint ? `${uid}-hint` : undefined} />
  </span>
  {#if invalid}<span id={`${uid}-err`} class="warn" role="alert">Enter an amount like 1450 or 1,450.50.</span>
  {:else if hint}<span id={`${uid}-hint`} class="hint">{hint}</span>{/if}
</div>

<style>
  .wrap { display: inline-flex; align-items: center; gap: .35rem; }
  .prefix { color: var(--muted); }
  .money { display: flex; flex-direction: column; align-items: flex-start; }
</style>
