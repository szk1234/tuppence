<script lang="ts">
  import { formatGBP } from '../lib/money'
  import { squarify } from '../lib/treemap'
  import type { Tile } from '../lib/understanding'

  let { tiles, total, onopen }: { tiles: Tile[]; total: string; onopen: (tile: Tile) => void } = $props()

  const placed = $derived(squarify(tiles, (t) => Number(t.amount)))
  const share = (t: Tile) => (Number(total) > 0 ? Math.round((Number(t.amount) / Number(total)) * 100) : 0)
</script>

<div class="treemap" role="group" aria-label="Spending by category">
  {#each placed as p (p.item.id)}
    <button
      class="tile"
      class:unsorted={p.item.id === 'unsorted'}
      style={`left:${p.x}%;top:${p.y}%;width:${p.w}%;height:${p.h}%`}
      title={`${p.item.label}: ${formatGBP(p.item.amount)} (${share(p.item)}%)`}
      aria-label={`${p.item.label}, ${formatGBP(p.item.amount)}, ${share(p.item)}% of spending`}
      onclick={() => onopen(p.item)}
    >
      {#if p.w > 12 && p.h > 10}
        <span class="name">{p.item.label}</span>
        <span class="amount">{formatGBP(p.item.amount)}</span>
      {/if}
    </button>
  {/each}
</div>

<style>
  .treemap { position: relative; width: 100%; aspect-ratio: 16 / 9; background: var(--panel); border-radius: 12px; overflow: hidden; }
  @media (max-width: 40rem) { .treemap { aspect-ratio: 1 / 1; } }
  .tile {
    position: absolute; margin: 0; padding: .4rem .5rem; border: 2px solid var(--panel); border-radius: 6px;
    background: color-mix(in srgb, var(--accent) 22%, var(--panel)); color: var(--ink);
    display: flex; flex-direction: column; justify-content: flex-start; align-items: flex-start;
    text-align: left; overflow: hidden; cursor: pointer;
  }
  .tile:hover, .tile:focus-visible { background: color-mix(in srgb, var(--accent) 40%, var(--panel)); }
  .tile.unsorted { background: repeating-linear-gradient(45deg, var(--panel), var(--panel) 6px, var(--line) 6px, var(--line) 8px); }
  .name { font-weight: 600; font-size: .9rem; }
  .amount { font-size: .85rem; font-variant-numeric: tabular-nums; }
</style>
