import { describe, expect, it } from 'vitest'
import { navigate, router } from './router.svelte'

describe('router', () => {
  it('tracks pushState navigation', () => {
    navigate('/settings/household')
    expect(router.path).toBe('/settings/household')
    expect(window.location.pathname).toBe('/settings/household')
  })
})
