<script lang="ts">
  import Notice from '../components/Notice.svelte'
  import { api, ApiError } from '../lib/api'
  import { navigate } from '../lib/router.svelte'
  import { applySession, type SessionInfo } from '../lib/session.svelte'
  let username = $state('')
  let password = $state('')
  let error = $state('')
  let busy = $state(false)
  async function submit(e: SubmitEvent) {
    e.preventDefault(); error = ''; busy = true
    try { applySession(await api<SessionInfo>('/api/auth/login', { method: 'POST', body: { username, password } })); navigate('/') }
    catch (err) { error = err instanceof ApiError ? err.detail : 'Something went wrong.' }
    finally { busy = false }
  }
</script>

<section class="card narrow">
  <h1>Sign in</h1>
  <form onsubmit={submit}>
    <label for="li-user">Username</label>
    <input id="li-user" autocomplete="username" maxlength="64" required bind:value={username} />
    <label for="li-pass">Password</label>
    <input id="li-pass" type="password" autocomplete="current-password" required bind:value={password} />
    <Notice message={error} />
    <button type="submit" disabled={busy}>Sign in</button>
  </form>
</section>
