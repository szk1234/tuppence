const here = () => ({ path: window.location.pathname, hash: window.location.hash })
/** `path` picks the page; `hash` (with its "#", or "") is a place on it, such as "#person-p_1". */
export const router = $state(typeof window === 'undefined' ? { path: '/', hash: '' } : here())

export function navigate(target: string) {
  const url = new URL(target, window.location.origin)
  if (url.pathname + url.hash !== window.location.pathname + window.location.hash) {
    window.history.pushState({}, '', url.pathname + url.search + url.hash)
  }
  router.path = url.pathname
  router.hash = url.hash
}

const SIGN_IN_PATHS = new Set(['/login', '/setup'])

/** After signing in, leave /login or /setup for Home; a deep-linked page stays where it is. */
export function leaveSignInPage() {
  if (SIGN_IN_PATHS.has(router.path)) navigate('/')
}

export function link(event: MouseEvent) {
  const anchor = event.currentTarget as HTMLAnchorElement
  if (event.metaKey || event.ctrlKey || event.shiftKey || anchor.target === '_blank') return
  event.preventDefault()
  navigate(anchor.getAttribute('href') ?? '/')
}

if (typeof window !== 'undefined') {
  window.addEventListener('popstate', () => { Object.assign(router, here()) })
}
