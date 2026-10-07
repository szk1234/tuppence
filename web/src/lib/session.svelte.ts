import { api, onUnauthorised, setCsrf } from './api'

export type SessionInfo = {
  authenticated: boolean; mode: 'local' | 'desktop' | 'server'; needs_setup: boolean
  user: { username: string; is_admin: boolean } | null; csrf_token: string | null
}

export const session = $state({
  loaded: false, authenticated: false, mode: 'local' as SessionInfo['mode'], needsSetup: false,
  user: null as SessionInfo['user'], csrfToken: null as string | null,
})

export function applySession(info: SessionInfo) {
  session.loaded = true
  session.authenticated = info.authenticated
  session.mode = info.mode
  session.needsSetup = info.needs_setup
  session.user = info.user
  session.csrfToken = info.csrf_token
  setCsrf(info.csrf_token)
}

export async function loadSession() {
  applySession(await api<SessionInfo>('/api/auth/session'))
}

export async function signOut() {
  await api('/api/auth/logout', { method: 'POST' })
  await loadSession()
}

onUnauthorised(() => { session.authenticated = false; setCsrf(null) })
