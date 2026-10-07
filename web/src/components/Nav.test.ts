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
