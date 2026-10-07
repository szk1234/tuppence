<script lang="ts">
  import { onMount } from 'svelte'
  import Modal from '../../components/Modal.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api, ApiError } from '../../lib/api'
  import { session } from '../../lib/session.svelte'

  type Preset = { id: string; label: string; api_style: string; base_url: string; kind: string; key_required: boolean }
  type Connection = {
    id: string; preset: string; name: string; api_style: string; base_url: string; is_local: boolean; has_key: boolean
    headers: Array<{ name: string; has_value: boolean }>; needs_notice: boolean; notice_acknowledged_at: string | null
    enabled: boolean; version: number
  }
  type Model = { connection_id: string; model_id: string; display_name: string }
  type Ref = { connection_id: string; model_id: string }
  type TaskRoute = { chain: Ref[]; local_only: boolean; version: number }
  type Routing = { mode: string; mode_version: number; simple_model: Ref | null; simple_version: number; tasks: Record<string, TaskRoute> }
  type Detected = { preset: string; base_url: string; model_count: number }

  let presets = $state<Preset[]>([])
  let connections = $state<Connection[]>([])
  let models = $state<Model[]>([])
  let routing = $state<Routing | null>(null)
  let detected = $state<Detected[] | null>(null)
  let error = $state('')
  let saved = $state('')
  let busy = $state(false)

  // add form
  let presetId = $state('')
  let newName = $state('')
  let newUrl = $state('')
  let newKey = $state('')

  // per-connection UI state
  let testResult = $state<Record<string, string>>({})
  let testBad = $state<Record<string, boolean>>({})
  let editing = $state<string | null>(null)
  let editName = $state('')
  let editUrl = $state('')
  let editKey = $state('')

  // modals
  let noticeFor = $state<Connection | null>(null)
  let afterNotice: (() => Promise<void>) | null = null
  let confirmForget = $state(false)

  // try it
  let message = $state('')
  let reply = $state('')
  let tryError = $state('')
  let sending = $state(false)

  const canEdit = $derived(session.mode !== 'server' || session.user?.is_admin === true)
  const preset = $derived(presets.find((p) => p.id === presetId))
  const isLocalPreset = $derived(preset?.kind === 'local')
  const nameOf = (id: string) => connections.find((c) => c.id === id)?.name ?? id
  const refKey = (r: Ref | null | undefined) => (r ? JSON.stringify([r.connection_id, r.model_id]) : '')
  const parseKey = (v: string): Ref | null => {
    if (!v) return null
    const [connection_id, model_id] = JSON.parse(v) as [string, string]
    return { connection_id, model_id }
  }

  const fail = (e: unknown) => { saved = ''; error = e instanceof ApiError ? e.detail : 'Something went wrong.' }
  const replaceConn = (c: Connection) => { connections = connections.map((x) => (x.id === c.id ? c : x)) }

  async function loadModels() { models = (await api<{ models: Model[] }>('/api/llm/models')).models }
  async function loadRouting() { routing = await api<Routing>('/api/llm/routing') }
  async function load() {
    presets = (await api<{ presets: Preset[] }>('/api/llm/presets')).presets
    if (!presetId && presets.length) pickPreset(presets[0].id)
    connections = (await api<{ connections: Connection[] }>('/api/llm/connections')).connections
    await Promise.all([loadModels(), loadRouting()])
  }
  onMount(() => { load().catch(fail) })

  function pickPreset(id: string) {
    presetId = id
    const p = presets.find((x) => x.id === id)
    newUrl = p?.base_url ?? ''
    newName = p?.label ?? ''
    newKey = ''
  }

  /** Run a call; if the server says the cloud notice is needed, show it, then retry once acknowledged. */
  async function guard(fn: () => Promise<void>) {
    try { await fn() } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.code === 'notice_required') {
        const c = connections.find((x) => x.id === e.connectionId)
        if (c) { noticeFor = c; afterNotice = () => guard(fn); return }
      }
      throw e
    }
  }

  async function detect() {
    error = ''; busy = true
    try { detected = (await api<{ servers: Detected[] }>('/api/llm/detect')).servers } catch (e) { fail(e) } finally { busy = false }
  }

  async function create(body: Record<string, unknown>) {
    const c = await api<Connection>('/api/llm/connections', { method: 'POST', body })
    connections = [...connections, c]
    saved = `${c.name} added. Press Test to fetch its models.`
  }

  async function addDetected(d: Detected) {
    error = ''; saved = ''; busy = true
    try { await create({ preset: d.preset, base_url: d.base_url }); detected = detected?.filter((x) => x !== d) ?? null } catch (e) { fail(e) } finally { busy = false }
  }

  async function addConnection(e: SubmitEvent) {
    e.preventDefault(); error = ''; saved = ''; busy = true
    const body: Record<string, unknown> = { preset: presetId, name: newName.trim() || undefined, base_url: newUrl.trim() || undefined }
    if (!isLocalPreset && newKey) body.api_key = newKey
    try { await create(body); newKey = '' } catch (err) { fail(err) } finally { busy = false }
  }

  async function test(c: Connection) {
    error = ''; testResult[c.id] = 'Testing…'; testBad[c.id] = false
    try {
      const r = await api<{ ok: boolean; reason: string; error?: string; models?: unknown[]; connection: Connection }>(`/api/llm/connections/${c.id}/test`, { method: 'POST' })
      replaceConn(r.connection)
      if (r.ok) {
        const n = r.models?.length ?? 0
        testResult[c.id] = `${n} ${n === 1 ? 'model' : 'models'} found`
        await loadModels()
      } else { testResult[c.id] = r.error ?? 'The test failed.'; testBad[c.id] = true }
    } catch (e) { testResult[c.id] = e instanceof ApiError ? e.detail : 'Something went wrong.'; testBad[c.id] = true }
  }

  async function remove(c: Connection) {
    error = ''; saved = ''
    try {
      await api(`/api/llm/connections/${c.id}`, { method: 'DELETE' })
      connections = connections.filter((x) => x.id !== c.id)
      await Promise.all([loadModels(), loadRouting()])
      saved = `${c.name} removed.`
    } catch (e) { fail(e) }
  }

  function startEdit(c: Connection) { editing = c.id; editName = c.name; editUrl = c.base_url; editKey = '' }

  async function saveEdit(e: SubmitEvent, c: Connection) {
    e.preventDefault(); error = ''; saved = ''; busy = true
    const changes: Record<string, unknown> = {}
    if (editName.trim() && editName.trim() !== c.name) changes.name = editName.trim()
    if (editUrl.trim() && editUrl.trim() !== c.base_url) changes.base_url = editUrl.trim()
    if (editKey) changes.api_key = editKey
    if (Object.keys(changes).length === 0) { editing = null; busy = false; return }
    try {
      replaceConn(await api<Connection>(`/api/llm/connections/${c.id}`, { method: 'PATCH', body: { changes, expected_version: c.version } }))
      editing = null; saved = 'Connection saved.'
    } catch (err) { fail(err) } finally { busy = false }
  }

  async function acknowledge() {
    const c = noticeFor
    if (!c) return
    error = ''
    try {
      replaceConn(await api<Connection>(`/api/llm/connections/${c.id}/acknowledge-notice`, { method: 'POST' }))
      noticeFor = null
      const next = afterNotice; afterNotice = null
      if (next) await next()
    } catch (e) { noticeFor = null; fail(e) }
  }
  function cancelNotice() { noticeFor = null; afterNotice = null }

  async function forgetKeys() {
    confirmForget = false; error = ''; saved = ''
    try {
      await api('/api/llm/secrets/forget', { method: 'POST' })
      connections = (await api<{ connections: Connection[] }>('/api/llm/connections')).connections
      saved = 'Saved AI keys forgotten. Enter a key again to use a cloud connection.'
    } catch (e) { fail(e) }
  }

  async function setting(key: string, value: unknown, version: number) {
    error = ''; saved = ''
    try {
      await api(`/api/settings/${key}`, { method: 'PATCH', body: { value, expected_version: version } })
    } catch (e) { fail(e) }
    await loadRouting().catch(() => {})
  }
  const setSimple = (v: string) => { const r = parseKey(v); if (r && routing) return setting('llm.simple_model', r, routing.simple_version) }
  const setMode = (advanced: boolean) => routing && setting('llm.mode', advanced ? 'advanced' : 'simple', routing.mode_version)

  async function saveTask(task: string, primary: string, fallback: string, localOnly: boolean) {
    error = ''; saved = ''
    const chain = [parseKey(primary), parseKey(fallback)].filter((r): r is Ref => r !== null)
    try {
      routing = await api<Routing>(`/api/llm/routing/tasks/${task}`, { method: 'PUT', body: { chain, local_only: localOnly, expected_version: routing!.tasks[task].version } })
    } catch (e) { fail(e); await loadRouting().catch(() => {}) }
  }

  async function send(e: SubmitEvent) {
    e.preventDefault(); reply = ''; tryError = ''; sending = true
    try {
      await guard(async () => {
        const r = await api<{ text: string }>('/api/llm/try', { method: 'POST', body: { task: 'coach', prompt: message } })
        reply = r.text
      })
    } catch (err) { tryError = err instanceof ApiError ? err.detail : 'Something went wrong.' } finally { sending = false }
  }
</script>

<section>
  <h1>AI</h1>
  <p>Choose which AI models Tuppence can use. With a local model, nothing leaves your machine. With a cloud model, your statement text goes to the provider you chose, and every call is logged in <a href="/settings/privacy">Privacy</a>.</p>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />
  {#if !canEdit}<p class="hint">Only the household admin can change AI connections. You can see what is set up here.</p>{/if}

  <div class="card">
    <h2>Connections</h2>
    {#if connections.length === 0}<p>No AI connections yet. Find a local one or add one below.</p>{/if}
    {#each connections as c (c.id)}
      <section class="card" aria-label={c.name}>
        <h3>{c.name} <span class="badge" class:cloud={!c.is_local}>{c.is_local ? 'Local' : 'Internet'}</span></h3>
        <p class="meta">{c.base_url}{#if !c.is_local || c.has_key} · {c.has_key ? 'API key saved' : 'No API key saved'}{/if}</p>
        {#if c.headers.length}<p class="meta">Custom headers: {c.headers.map((h) => `${h.name} (${h.has_value ? 'saved' : 'no value'})`).join(', ')}</p>{/if}
        {#if testResult[c.id]}<p class:warn={testBad[c.id]} role="status">{testResult[c.id]}</p>{/if}
        {#if canEdit}
          <div class="row">
            <button type="button" onclick={() => test(c)}>Test</button>
            {#if c.needs_notice}<button type="button" onclick={() => { noticeFor = c; afterNotice = null }}>Review what's sent</button>{/if}
            <button type="button" onclick={() => (editing === c.id ? (editing = null) : startEdit(c))}>{editing === c.id ? 'Close editor' : 'Edit'}</button>
            <button type="button" onclick={() => remove(c)}>Remove</button>
          </div>
          {#if editing === c.id}
            <form onsubmit={(e) => saveEdit(e, c)}>
              <fieldset class="bare" disabled={busy}>
                <label for={`en-${c.id}`}>Name</label>
                <input id={`en-${c.id}`} bind:value={editName} />
                <label for={`eu-${c.id}`}>Base URL</label>
                <input id={`eu-${c.id}`} bind:value={editUrl} />
                <p class="hint">Changing the address clears the saved API key and header values. Enter the key again below if you still need it.</p>
                {#if !c.is_local || c.has_key}
                  <label for={`ek-${c.id}`}>API key</label>
                  <input id={`ek-${c.id}`} type="password" autocomplete="off" bind:value={editKey} placeholder={c.has_key ? 'Saved. Leave blank to keep it' : ''} />
                {/if}
                <button type="submit">Save changes</button>
              </fieldset>
            </form>
          {/if}
        {/if}
      </section>
    {/each}

    {#if canEdit}
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
          {#if !isLocalPreset}
            <label for="ai-key">API key</label>
            <input id="ai-key" type="password" autocomplete="off" bind:value={newKey} />
            <p class="hint">Stored in your system keychain, or encrypted on this machine. Never shown again.</p>
          {/if}
          <button type="submit">Save connection</button>
        </fieldset>
      </form>
      <p><button type="button" class="link" onclick={() => (confirmForget = true)}>Forget saved AI keys</button></p>
    {/if}
  </div>

  <div class="card">
    <h2>Model</h2>
    {#if models.length === 0}
      <p>No models yet. Press Test on a connection to fetch its models.</p>
    {:else if routing}
      <fieldset class="bare" disabled={!canEdit}>
        {#if routing.mode !== 'advanced'}
          <label for="simple-model">Model for everything</label>
          <select id="simple-model" value={refKey(routing.simple_model)} onchange={(e) => setSimple((e.currentTarget as HTMLSelectElement).value)}>
            <option value="">Choose a model</option>
            {#each models as m (refKey(m))}<option value={refKey(m)}>{nameOf(m.connection_id)} — {m.model_id}</option>{/each}
          </select>
        {/if}
        <div class="toggle">
          <label><input type="checkbox" checked={routing.mode === 'advanced'} onchange={(e) => setMode((e.currentTarget as HTMLInputElement).checked)} /> Advanced: choose per task</label>
        </div>
        {#if routing.mode === 'advanced'}
          {#each Object.entries(routing.tasks) as [task, route] (task)}
            {@const primary = refKey(route.chain[0])}
            {@const fallback = refKey(route.chain[1])}
            <section class="card" aria-label={`Task ${task}`}>
              <label for={`t-${task}`}>{task}</label>
              <select id={`t-${task}`} value={primary} onchange={(e) => saveTask(task, (e.currentTarget as HTMLSelectElement).value, fallback, route.local_only)}>
                <option value="">Not set</option>
                {#each models as m (refKey(m))}<option value={refKey(m)}>{nameOf(m.connection_id)} — {m.model_id}</option>{/each}
              </select>
              <label for={`f-${task}`}>Fallback for {task}</label>
              <select id={`f-${task}`} value={fallback} onchange={(e) => saveTask(task, primary, (e.currentTarget as HTMLSelectElement).value, route.local_only)}>
                <option value="">None</option>
                {#each models as m (refKey(m))}<option value={refKey(m)}>{nameOf(m.connection_id)} — {m.model_id}</option>{/each}
              </select>
              <div class="toggle">
                <label><input type="checkbox" checked={route.local_only} onchange={(e) => saveTask(task, primary, fallback, (e.currentTarget as HTMLInputElement).checked)} /> Keep this task on this device</label>
              </div>
            </section>
          {/each}
        {/if}
      </fieldset>
    {/if}
  </div>

  {#if canEdit}
    <form class="card" onsubmit={send}>
      <h2>Try it</h2>
      <fieldset class="bare" disabled={sending}>
        <label for="try-message">Message</label>
        <textarea id="try-message" rows="3" bind:value={message}></textarea>
        <button type="submit" disabled={!message.trim()}>Send</button>
      </fieldset>
      <Notice message={tryError} />
      {#if reply}<p class="reply" role="status">{reply}</p>{/if}
    </form>
  {/if}
</section>

<Modal open={noticeFor !== null} title={`Before you use ${noticeFor?.name ?? ''}`} onclose={cancelNotice}>
  <p><strong>{noticeFor?.name} will see the statement text Tuppence sends to it.</strong> It's covered by {noticeFor?.name}'s own privacy terms. Every call is listed in Settings › Privacy.</p>
  <div class="row">
    <button type="button" onclick={acknowledge}>I understand</button>
    <button type="button" onclick={cancelNotice}>Cancel</button>
  </div>
</Modal>

<Modal open={confirmForget} title="Forget saved AI keys?" onclose={() => (confirmForget = false)}>
  <p>Tuppence will delete every saved AI key and header value. Your connections stay, but cloud ones will need a key entering again.</p>
  <div class="row">
    <button type="button" onclick={forgetKeys}>Forget keys</button>
    <button type="button" onclick={() => (confirmForget = false)}>Cancel</button>
  </div>
</Modal>
