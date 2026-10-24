import { expect, test } from '@playwright/test'
import { signIn } from './support'

/**
 * Feature 05 Phase 24 browser check against the real stack (backend ENV=test, synthetic).
 * FE-24.1: the sidebar's "Advanced" section sits below "AI Assistant" and above "Administration";
 * risk_analyst opens "Agentic Stress-Test Explorer" and sees the D-44 §8 banner.
 * FE-28.4 (Q19): the Liquidity Intelligence demo page shows the illustrative-mock banner.
 * FE-35.6: the retired explorer demo route is a 404, and nothing on the explorer links to it.
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

  // FE-28.4: the demo page carries the illustrative-mock banner (FE-35.6: it stays, labelled).
  await page.goto('/advanced/liquidity-intelligence')
  const demoBanner = page.getByTestId('demo-banner')
  await expect(demoBanner).toBeVisible()
  await expect(demoBanner).toContainText('Illustrative mock: browser arithmetic on synthetic data, not the calculation engine')
  await expect(page.getByText('Prototype · mock agent · synthetic data')).toBeVisible()
})

test('FE-35.6: the retired explorer demo route is a 404 and the explorer no longer links to it', async ({ page }) => {
  await signIn(page, 'Risk analyst')
  await page.goto('/advanced/stress-explorer')
  await expect(page.getByTestId('stress-explorer-banner')).toBeVisible()
  await expect(page.locator('a[href^="/advanced/stress-explorer/"][href$="-demo"]')).toHaveCount(0)

  const retired = await page.goto('/advanced/stress-explorer/agent-demo')
  expect(retired?.status()).toBe(404)
  await expect(page.getByTestId('demo-banner')).toHaveCount(0)
  // A scenario id is a UUID; any other segment under the explorer is a 404 too, never a scenario page.
  expect((await page.goto('/advanced/stress-explorer/not-a-scenario'))?.status()).toBe(404)
  // Q19: a well-formed but unknown id (any case) is no 404; the page shows its "not found" empty state.
  expect((await page.goto('/advanced/stress-explorer/6F1C0F7E-0000-4000-8000-00000000ABCD'))?.status()).toBe(200)
  await expect(page.getByText('Scenario not found')).toBeVisible()
  // Liquidity Intelligence stays, labelled.
  expect((await page.goto('/advanced/liquidity-intelligence'))?.status()).toBe(200)
  await expect(page.getByTestId('demo-banner')).toBeVisible()
})
