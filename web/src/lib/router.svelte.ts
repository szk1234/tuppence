export const router = $state({ path: typeof window === 'undefined' ? '/' : window.location.pathname })

export function navigate(path: string) {
  if (path !== window.location.pathname) window.history.pushState({}, '', path)
  router.path = path
}

export function link(event: MouseEvent) {
  const anchor = event.currentTarget as HTMLAnchorElement
  if (event.metaKey || event.ctrlKey || event.shiftKey || anchor.target === '_blank') return
  event.preventDefault()
  navigate(anchor.getAttribute('href') ?? '/')
}

if (typeof window !== 'undefined') {
  window.addEventListener('popstate', () => { router.path = window.location.pathname })
}
