<script lang="ts">
  import Notice from '../components/Notice.svelte'
  import { api, ApiError } from '../lib/api'
  import { applySession, type SessionInfo } from '../lib/session.svelte'
  let username = $state('')
  let password = $state('')
  let error = $state('')
  let busy = $state(false)
  async function submit(e: SubmitEvent) {
    e.preventDefault(); error = ''; busy = true
    try { applySession(await api<SessionInfo>('/api/auth/setup', { method: 'POST', body: { username, password } })) }
    catch (err) { error = err instanceof ApiError ? err.detail : 'Something went wrong.' }
    finally { busy = false }
  }
</script>

<section class="card narrow">
  <h1>Set up Tuppence</h1>
  <p>Create the administrator account for this household. There are no default passwords.</p>
  <form onsubmit={submit}>
    <label for="su-user">Username</label>
    <input id="su-user" autocomplete="username" required bind:value={username} />
    <label for="su-pass">Password</label>
    <input id="su-pass" type="password" autocomplete="new-password" minlength="10" required bind:value={password} />
    <p class="hint">At least 10 characters. A short sentence works well.</p>
    <Notice message={error} />
    <button type="submit" disabled={busy}>Create account</button>
  </form>
</section>
