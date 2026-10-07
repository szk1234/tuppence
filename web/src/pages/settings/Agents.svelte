<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../../components/Notice.svelte'
  import { api, ApiError } from '../../lib/api'

  type Manifest = {
    name: string; description: string; enabled: boolean; task: string | null
    budgets: Record<string, number>; thresholds: Record<string, number>; limits: Record<string, number>
    [key: string]: unknown
  }
  type View = { manifest: Manifest; overridden: string[]; user_file_error: string | null; version: number }

  let agents = $state<View[]>([])
  let presets = $state<string[]>([])
  let preset = $state({ value: 'balanced', version: 0 })
  let drafts = $state<Record<string, Record<string, unknown>>>({})
  let invalid = $state<Record<string, boolean>>({})
  let selected = $state('balanced')
  let error = $state('')
  let saved = $state('')

  const fail = (err: unknown) => { saved = ''; error = err instanceof ApiError ? err.detail : 'Something went wrong.' }

  async function load() {
    const res = await api<{ agents: View[]; preset: { value: string; version: number } }>('/api/config/agents')
    agents = res.agents; preset = res.preset; selected = res.preset.value; drafts = {}; invalid = {}
    presets = (await api<{ presets: string[] }>('/api/config/presets')).presets
  }
  onMount(() => { load().catch(fail) })

  const hasInvalid = (agent: string) => Object.keys(invalid).some((k) => k.startsWith(`${agent}.`) && invalid[k])
  const hasChanges = (agent: string) => {
    const d = drafts[agent]
    if (!d) return false
    return Object.values(d).some((v) => typeof v !== 'object' || v === null || Object.keys(v).length > 0)
  }

  function setNumber(agent: string, section: string, key: string, raw: string) {
    const id = `${agent}.${section}.${key}`
    const n = Number(raw)
    if (raw.trim() === '' || !Number.isFinite(n)) {
      invalid[id] = true
      const d = drafts[agent]
      const s = d?.[section] as Record<string, unknown> | undefined
      if (s && key in s) {
        const { [key]: _gone, ...rest } = s
        drafts[agent] = { ...d, [section]: rest }
      }
      return
    }
    invalid[id] = false
    setDraft(agent, section, key, n)
  }

  function setDraft(agent: string, section: string, key: string, value: unknown) {
    const d = drafts[agent] ?? {}
    const s = (d[section] as Record<string, unknown>) ?? {}
    drafts[agent] = { ...d, [section]: { ...s, [key]: value } }
  }

  async function save(view: View) {
    if (!hasChanges(view.manifest.name) || hasInvalid(view.manifest.name)) return
    const changes = $state.snapshot(drafts[view.manifest.name])
    error = ''
    try {
      const updated = await api<View>(`/api/config/agents/${view.manifest.name}`, {
        method: 'PATCH', body: { changes, expected_version: view.version },
      })
      agents = agents.map((a) => (a.manifest.name === updated.manifest.name ? updated : a))
      delete drafts[view.manifest.name]
      saved = `${view.manifest.name} saved.`
    } catch (err) { fail(err) }
  }

  async function reset(view: View, path: string) {
    error = ''
    try {
      const updated = await api<View>(`/api/config/agents/${view.manifest.name}/reset`, {
        method: 'POST', body: { path, expected_version: view.version },
      })
      agents = agents.map((a) => (a.manifest.name === updated.manifest.name ? updated : a))
    } catch (err) { fail(err) }
  }

  async function choosePreset(value: string) {
    error = ''
    try {
      const entry = await api<{ value: string; version: number }>('/api/settings/config.preset', {
        method: 'PATCH', body: { value, expected_version: preset.version },
      })
      preset = { value: entry.value, version: entry.version }
      await load()
      saved = `Preset changed to ${value}.`
    } catch (err) { selected = preset.value; fail(err) }
  }
</script>

<section>
  <h1>Agents</h1>
  <p>Tuppence is open source: every agent's limits and budgets can be tuned here.</p>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />

  <div class="card">
    <label for="preset">Preset</label>
    <select id="preset" bind:value={selected} onchange={(e) => choosePreset((e.currentTarget as HTMLSelectElement).value)}>
      {#each presets as p}<option value={p}>{p}</option>{/each}
    </select>
    <a href="/api/config/export" download>Export settings</a>
  </div>

  {#each agents as view (view.manifest.name)}
    {@const m = view.manifest}
    <section class="card" aria-label={m.name}>
      <h2>{m.name}</h2>
      <p>{m.description}</p>
      {#if view.user_file_error}<p class="warn">Your config file was ignored: {view.user_file_error}</p>{/if}
      <label><input type="checkbox" checked={m.enabled} onchange={(e) => {
        const d = drafts[m.name] ?? {}
        drafts[m.name] = { ...d, enabled: (e.currentTarget as HTMLInputElement).checked }
      }} /> Enabled</label>
      {#each ['budgets', 'limits', 'thresholds'] as section}
        {#if Object.keys(m[section] as Record<string, number>).length}
          <fieldset>
            <legend>{section}</legend>
            {#each Object.entries(m[section] as Record<string, number>) as [key, value]}
              {@const path = `${section}.${key}`}
              <div class="field">
                <label for={`${m.name}-${path}`}>{key}</label>
                <input id={`${m.name}-${path}`} type="number" step="any" value={value}
                  aria-invalid={invalid[`${m.name}.${path}`] ? 'true' : undefined}
                  oninput={(e) => setNumber(m.name, section, key, (e.currentTarget as HTMLInputElement).value)} />
                {#if invalid[`${m.name}.${path}`]}<span class="warn" role="alert">Enter a number</span>{/if}
                {#if view.overridden.includes(path)}
                  <span class="badge">Changed by you</span>
                  <button class="link" onclick={() => reset(view, path)}>Reset</button>
                {/if}
              </div>
            {/each}
          </fieldset>
        {/if}
      {/each}
      <button onclick={() => save(view)} disabled={!hasChanges(m.name) || hasInvalid(m.name)}>Save {m.name}</button>
    </section>
  {/each}
</section>
