import { expect, test } from '@playwright/test'
import { signIn } from './support'

/**
 * Feature 05 Phase 24 browser check against the real stack (backend ENV=test, synthetic).
 * FE-24.1: the sidebar's "Advanced" section sits below "AI Assistant" and above "Administration";
 * risk_analyst opens "Agentic Stress-Test Explorer" and sees the D-44 §8 banner.
 * Needs a frontend built with NEXT_PUBLIC_STRESS_EXPLORER_UI on (its default).
 */

test('FE-24.1: risk_analyst opens the Agentic Stress-Test Explorer from the Advanced section and sees the banner', async ({ page }) => {
  await signIn(page, 'Risk analyst')

  const sidebar = page.locator('aside')
  const advanced = sidebar.getByRole('navigation', { name: 'Advanced' })
  await expect(sidebar.getByText('Advanced', { exact: true })).toBeVisible()
  const item = advanced.getByRole('link', { name: 'Agentic Stress-Test Explorer' })
  await expect(item).toHaveAttribute('href', '/advanced/stress-explorer')

  // Order in the sidebar: … "AI Assistant" → "Advanced" → the explorer → "Administration".
  const texts = (await sidebar.innerText()).split('\n').map((t) => t.trim()).filter(Boolean)
  const at = (label: string) => texts.findIndex((t) => t.toLowerCase() === label.toLowerCase())
  expect(at('AI Assistant')).toBeGreaterThan(-1)
  expect(at('AI Assistant')).toBeLessThan(at('Advanced'))
  expect(at('Advanced')).toBeLessThan(at('Agentic Stress-Test Explorer'))
  expect(at('Agentic Stress-Test Explorer')).toBeLessThan(at('Administration'))

  await item.click()
  await page.waitForURL('**/advanced/stress-explorer')
  const banner = page.getByTestId('stress-explorer-banner')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText('Illustrative stress exploration, not a regulatory stress test')
  await expect(item).toHaveClass(/text-primary/)
})
