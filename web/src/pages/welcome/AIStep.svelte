<script lang="ts">
  import { onMount } from 'svelte'
  import CloudNoticeModal from '../../components/CloudNoticeModal.svelte'
  import ConnectionQuickAdd from '../../components/ConnectionQuickAdd.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { errorText } from '../../lib/form'
  import type { Connection, Preset } from '../../lib/llm'
  import { session } from '../../lib/session.svelte'

  type Ref = { connection_id: string; model_id: string }
  type Model = { connection_id: string; model_id: string }
  type Routing = { mode: string; simple_model: Ref | null; simple_version: number }
  type Setting = { key: string; value: unknown; version: number }

  const RESEARCH = 'privacy.research_lookups'
  const PRESET = 'config.preset'
  const PRESET_HELP: Record<string, string> = {
    frugal: 'Fewest AI calls and the lowest cost; lighter research and shorter answers.',
    balanced: 'A sensible mix of cost and care. The default.',
    thorough: 'Most careful: more lookups and review passes, at a higher cost.',
  }
  let presets = $state<Preset[]>([])
  let connections = $state<Connection[]>([])
  let models = $state<Model[]>([])
  let routing = $state<Routing | null>(null)
  let research = $state(true)
  let researchSetting = $state<Setting | null>(null)
  let agentPresets = $state<string[]>([])
  let presetSetting = $state<Setting | null>(null)
  let agentPreset = $state('balanced')
  let loaded = $state(false)
  let error = $state('')
  let saved = $state('')
  let testing = $state<Record<string, string>>({})
  let noticeFor = $state<Connection | null>(null)

  const canEdit = $derived(session.mode !== 'server' || session.user?.is_admin === true)
  const refKey = (r: Ref | null | undefined) => (r ? JSON.stringify([r.connection_id, r.model_id]) : '')
  const nameOf = (id: string) => connections.find((c) => c.id === id)?.name ?? id
  const fail = (e: unknown) => { saved = ''; error = errorText(e) }

  async function loadModels() { models = (await api<{ models: Model[] }>('/api/llm/models')).models }
  async function loadRouting() { routing = await api<Routing>('/api/llm/routing') }

  async function load() {
    presets = (await api<{ presets: Preset[] }>('/api/llm/presets')).presets
    connections = (await api<{ connections: Connection[] }>('/api/llm/connections')).connections
    await Promise.all([loadModels(), loadRouting()])
    const all = (await api<{ settings: Setting[] }>('/api/settings')).settings
    researchSetting = all.find((s) => s.key === RESEARCH) ?? null
    // The stored default is off, but the wizard recommends on and pre-selects it, for the user to confirm. Once the
    // setting has ever been changed (version above 0) the stored choice is respected, including an opt-out.
    if (researchSetting && researchSetting.version > 0) research = researchSetting.value === true
    presetSetting = all.find((s) => s.key === PRESET) ?? null
    if (typeof presetSetting?.value === 'string') agentPreset = presetSetting.value
    agentPresets = (await api<{ presets: string[] }>('/api/config/presets')).presets
    loaded = true
  }
  onMount(() => { load().catch(fail) })

  const created = (c: Connection) => { connections = [...connections, c]; saved = `${c.name} added. Press Test to find its models.` }

  async function test(c: Connection) {
    error = ''; testing[c.id] = 'Testing…'
    try {
      const r = await api<{ ok: boolean; error?: string; models?: unknown[]; connection: Connection }>(`/api/llm/connections/${c.id}/test`, { method: 'POST' })
      connections = connections.map((x) => (x.id === c.id ? r.connection : x))
      if (r.ok) {
        const n = r.models?.length ?? 0
        testing[c.id] = `${n} ${n === 1 ? 'model' : 'models'} found`
        await loadModels()
      } else testing[c.id] = r.error ?? 'The test failed.'
    } catch (e) { testing[c.id] = errorText(e) }
  }

  async function acknowledge() {
    const c = noticeFor
    if (!c) return
    try {
      const updated = await api<Connection>(`/api/llm/connections/${c.id}/acknowledge-notice`, { method: 'POST', body: { expected_version: c.version } })
      connections = connections.map((x) => (x.id === c.id ? updated : x))
      noticeFor = null
    } catch (e) { noticeFor = null; fail(e) }
  }

  async function chooseModel(value: string) {
    if (!value || !routing) return
    error = ''; saved = ''
    const [connection_id, model_id] = JSON.parse(value) as [string, string]
    try {
      await api(`/api/settings/llm.simple_model`, { method: 'PATCH', body: { value: { connection_id, model_id }, expected_version: routing.simple_version } })
      saved = 'Model chosen.'
    } catch (e) { fail(e) }
    await loadRouting().catch(() => {})
  }

  /** Called by the wizard before it records this step: applies the agent preset and the research-lookups choice. */
  export async function save(): Promise<boolean> {
    if (!canEdit) return true
    error = ''
    try {
      if (presetSetting && presetSetting.value !== agentPreset) {
        presetSetting = await api<Setting>(`/api/settings/${PRESET}`, { method: 'PATCH', body: { value: agentPreset, expected_version: presetSetting.version } })
      }
      if (researchSetting && (researchSetting.version === 0 || researchSetting.value !== research)) {
        researchSetting = await api<Setting>(`/api/settings/${RESEARCH}`, { method: 'PATCH', body: { value: research, expected_version: researchSetting.version } })
      }
      return true
    } catch (e) { fail(e); return false }
  }
</script>

<p>Tuppence uses an AI model to read statements and answer questions. <strong>Local</strong> models run on your own computer or network, so nothing leaves it. <strong>Internet</strong> models run at a provider you choose, who see the statement text sent to them. You can skip this and set it up later in Settings.</p>
<Notice message={error} />
<Notice message={saved} kind="ok" />

{#if !canEdit}
  <p class="hint">Only the household admin can change AI connections and privacy settings. Ask them to set this up in Settings › AI.</p>
{:else}
  <div class="card">
    <h2>Connections</h2>
    {#if connections.length === 0}<p>No AI connections yet. Find a local one or add one below.</p>{/if}
    {#each connections as c (c.id)}
      <section class="card" aria-label={c.name}>
        <h3>{c.name} <span class="badge" class:cloud={!c.is_local}>{c.is_local ? 'Local' : 'Internet'}</span></h3>
        <p class="meta">{c.base_url}</p>
        {#if testing[c.id]}<p role="status">{testing[c.id]}</p>{/if}
        <div class="row">
          <button type="button" onclick={() => test(c)}>Test</button>
          {#if c.needs_notice}<button type="button" onclick={() => (noticeFor = c)}>Review what's sent</button>{/if}
        </div>
      </section>
    {/each}
    <ConnectionQuickAdd {presets} onstart={() => { error = ''; saved = '' }} oncreated={created} onerror={fail} />
  </div>

  <div class="card">
    <h2>Model choice</h2>
    {#if models.length === 0}
      <p>No models yet. Press Test on a connection to find its models.</p>
    {:else if routing?.mode === 'advanced'}
      <p>You've chosen models per task. Change them in Settings › AI.</p>
    {:else if routing}
      <label for="simple-model">Model for everything</label>
      <select id="simple-model" value={refKey(routing.simple_model)} onchange={(e) => chooseModel(e.currentTarget.value)}>
        <option value="">Choose a model</option>
        {#each models as m (refKey(m))}<option value={refKey(m)}>{nameOf(m.connection_id)} — {m.model_id}</option>{/each}
      </select>
    {/if}
  </div>

  <div class="card">
    <h2>How careful should the agents be?</h2>
    <fieldset class="bare" disabled={!loaded || presetSetting === null}>
      <label for="agent-preset">Agent preset</label>
      <select id="agent-preset" bind:value={agentPreset}>
        {#each agentPresets as p}<option value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>{/each}
      </select>
      <p class="hint">{PRESET_HELP[agentPreset] ?? ''}</p>
      <ul class="hint">
        {#each agentPresets as p}<li><strong>{p.charAt(0).toUpperCase() + p.slice(1)}:</strong> {PRESET_HELP[p] ?? ''}</li>{/each}
      </ul>
    </fieldset>
  </div>

  <div class="card">
    <h2>Privacy</h2>
    <fieldset class="bare" disabled={!loaded || researchSetting === null}>
      <div class="toggle">
        <label><input type="checkbox" bind:checked={research} /> Research lookups (recommended: on)</label>
        <p class="hint">Look up unknown merchant names online so more transactions are understood. Only merchant names are looked up — never amounts or your details. You can change this any time in Settings › Privacy.</p>
      </div>
    </fieldset>
  </div>
{/if}

<CloudNoticeModal connection={noticeFor} onconfirm={acknowledge} oncancel={() => (noticeFor = null)} />
