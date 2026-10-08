<script lang="ts">
  import { categoryOptions, type Category } from '../lib/understanding'

  let { categories, value = '', label, disabled = false, onchange }: {
    categories: Category[]; value?: string | null; label: string; disabled?: boolean; onchange: (id: string) => void
  } = $props()

  const groups = $derived.by(() => {
    const out = new Map<string, { id: string; label: string }[]>()
    for (const o of categoryOptions(categories)) {
      if (!out.has(o.group)) out.set(o.group, [])
      out.get(o.group)!.push(o)
    }
    return [...out.entries()]
  })
</script>

<select aria-label={label} {disabled} value={value ?? ''} onchange={(e) => onchange((e.currentTarget as HTMLSelectElement).value)}>
  <option value="" disabled>Choose a category…</option>
  {#each groups as [group, options] (group)}
    <optgroup label={group}>
      {#each options as o (o.id)}<option value={o.id}>{o.label}</option>{/each}
    </optgroup>
  {/each}
</select>
