import { render, screen, within } from '@testing-library/svelte'
import { expect, it } from 'vitest'
import Nav from './Nav.svelte'

it('lists the settings links as plain links under a visible Settings heading', () => {
  render(Nav)
  const nav = screen.getByRole('navigation', { name: 'Main' })
  expect(within(nav).getByRole('heading', { name: 'Settings' })).toBeVisible()
  for (const name of ['Home', 'Usage', 'Household', 'Accounts', 'Income', 'Debts', 'Goals', 'Timeline', 'AI', 'Privacy', 'Agents']) {
    expect(within(nav).getByRole('link', { name })).toBeVisible()
  }
  expect(within(nav).getByRole('link', { name: 'Timeline' })).toHaveAttribute('href', '/settings/timeline')
})

it('links Statements after Home and keeps it current on a statement page', async () => {
  const { router } = await import('../lib/router.svelte')
  router.path = '/statements/s_1'
  render(Nav)
  const nav = screen.getByRole('navigation', { name: 'Main' })
  expect(within(nav).getByRole('link', { name: 'Statements' })).toHaveAttribute('aria-current', 'page')
  expect(within(nav).getByRole('link', { name: 'Home' })).not.toHaveAttribute('aria-current')
  router.path = '/'
})
