import { describe, expect, it } from 'vitest'
import { navigate, router } from './router.svelte'

describe('router', () => {
  it('tracks pushState navigation', () => {
    navigate('/settings/household')
    expect(router.path).toBe('/settings/household')
    expect(window.location.pathname).toBe('/settings/household')
    expect(router.hash).toBe('')
  })

  it('keeps a #fragment apart from the page path', () => {
    navigate('/settings/household#person-p_1')
    expect(router.path).toBe('/settings/household')
    expect(router.hash).toBe('#person-p_1')
    expect(window.location.hash).toBe('#person-p_1')
    navigate('/settings/income')
    expect(router.hash).toBe('')
  })
})
