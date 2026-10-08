<script lang="ts">
  import { formatGBP } from '../lib/money'
  import { dmyDate } from '../lib/dates'
  import type { Transaction } from '../lib/statements'

  let { rows, caption = 'Transactions' }: { rows: Transaction[]; caption?: string } = $props()
</script>

<div class="scroll">
  <table>
    <caption>{caption}</caption>
    <thead>
      <tr><th scope="col">Date</th><th scope="col">Description</th><th scope="col" class="num">Amount</th><th scope="col" class="num">Balance</th></tr>
    </thead>
    <tbody>
      {#each rows as row (row.id)}
        <tr>
          <td>{dmyDate(row.date)}</td>
          <td>{row.description}</td>
          <td class="num" class:out={row.amount.startsWith('-')}>{formatGBP(row.amount)}</td>
          <td class="num">{row.balance_after ? formatGBP(row.balance_after) : ''}</td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; }
  caption { text-align: left; font-weight: 600; padding: .5rem 0; }
  th, td { padding: .35rem .5rem; border-bottom: 1px solid var(--line); text-align: left; }
  .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .out { color: var(--ink); }
</style>
