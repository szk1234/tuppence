<script lang="ts">
  import type { Snippet } from 'svelte'
  import { api } from '../lib/api'
  import { blankHeader, type Connection, type Detected, type HeaderRow, type Preset } from '../lib/llm'

  /**
   * "Find local AI" detection plus the "Add connection" form. Shared by Settings > AI (full: with custom headers)
   * and the onboarding wizard (compact: no headers). The page owns the connection list and the messages.
   */
  let { presets, headers, onstart, oncreated, onerror }: {
    presets: Preset[]
    /** Renders the custom header rows; leave out for the compact form. */
    headers?: Snippet<[HeaderRow[], string, (i: number) => void]>
    onstart?: () => void
    oncreated: (c: Connection) => void
    onerror: (e: unknown) => void
  } = $props()

  let detected = $state<Detected[] | null>(null)
  let busy = $state(false)
  let presetId = $state('')
  let newName = $state('')
  let newUrl = $state('')
  let newKey = $state('')
  let newHeaders = $state<HeaderRow[]>([])

  const preset = $derived(presets.find((p) => p.id === presetId))
  const isLocalPreset = $derived(preset?.kind === 'local')

  function pickPreset(id: string) {
    presetId = id
    const p = presets.find((x) => x.id === id)
    newUrl = p?.base_url ?? ''
    newName = p?.label ?? ''
    newKey = ''
  }
  $effect(() => { if (!presetId && presets.length) pickPreset(presets[0].id) })

  async function detect() {
    onstart?.(); busy = true
    try { detected = (await api<{ servers: Detected[] }>('/api/llm/detect')).servers } catch (e) { onerror(e) } finally { busy = false }
  }

  async function create(body: Record<string, unknown>) {
    oncreated(await api<Connection>('/api/llm/connections', { method: 'POST', body }))
  }

  async function addDetected(d: Detected) {
    onstart?.(); busy = true
    try { await create({ preset: d.preset, base_url: d.base_url }); detected = detected?.filter((x) => x !== d) ?? null } catch (e) { onerror(e) } finally { busy = false }
  }

  async function addConnection(e: SubmitEvent) {
    e.preventDefault(); onstart?.(); busy = true
    const body: Record<string, unknown> = { preset: presetId, name: newName.trim() || undefined, base_url: newUrl.trim() || undefined }
    if (newKey) body.api_key = newKey
    const extra = Object.fromEntries(newHeaders.filter((h) => h.name.trim()).map((h) => [h.name.trim(), h.value]))
    if (Object.keys(extra).length) body.headers = extra
    try { await create(body); newKey = ''; newHeaders = [] } catch (err) { onerror(err) } finally { busy = false }
  }
</script>

<h3>Find local AI</h3>
<p class="hint">Looks for Ollama, LM Studio and similar servers running on this machine.</p>
<button type="button" onclick={detect} disabled={busy}>Find local AI</button>
{#if detected}
  {#if detected.length === 0}<p>No local AI servers found.</p>{/if}
  <ul class="people">
    {#each detected as d (d.base_url)}
      <li><span>{d.preset} at {d.base_url} ({d.model_count} {d.model_count === 1 ? 'model' : 'models'})</span>
        <button type="button" onclick={() => addDetected(d)} disabled={busy} aria-label={`Add ${d.preset} at ${d.base_url}`}>Add</button></li>
    {/each}
  </ul>
{/if}

<h3>Add connection</h3>
<form onsubmit={addConnection}>
  <fieldset class="bare" disabled={busy}>
    <label for="ai-provider">Provider</label>
    <select id="ai-provider" value={presetId} onchange={(e) => pickPreset((e.currentTarget as HTMLSelectElement).value)}>
      {#each presets as p (p.id)}<option value={p.id}>{p.label}</option>{/each}
    </select>
    <label for="ai-name">Name</label>
    <input id="ai-name" bind:value={newName} />
    <label for="ai-url">Base URL</label>
    <input id="ai-url" bind:value={newUrl} />
    <label for="ai-key">{isLocalPreset || !preset?.key_required ? 'API key (optional)' : 'API key'}</label>
    <input id="ai-key" type="password" autocomplete="off" bind:value={newKey} />
    <p class="hint">{#if isLocalPreset}Only if your server asks for one. {/if}Stored in your system keychain, or encrypted on this machine. Never shown again.</p>
    {#if headers}
      <h4>Custom headers (optional)</h4>
      {@render headers(newHeaders, 'new', (i) => { newHeaders = newHeaders.filter((_, j) => j !== i) })}
      <button type="button" onclick={() => { newHeaders = [...newHeaders, blankHeader()] }}>Add header</button>
    {/if}
    <div><button type="submit">Save connection</button></div>
  </fieldset>
</form>
