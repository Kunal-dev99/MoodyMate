// Phase 2 browser verification, AUTH_MODE=dev (FE-2.1, FE-2.2) against the real backend.
import { chromium } from 'playwright'
const OUT = process.argv[2]
const BASE = 'http://localhost:3000'
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const results = { roles: {} }

async function signIn(page, roleName) {
  await page.goto(`${BASE}/login`)
  await page.getByTestId('dev-user-picker').waitFor()
  await page.getByRole('button', { name: new RegExp(roleName) }).click()
  await page.waitForURL('**/dashboard')
  await page.getByText('Operational').waitFor()
}
const navLabels = (page) => page.locator('aside nav a').allInnerTexts()

// 1. Unauthenticated -> redirected to sign-in with next=
{
  const page = await browser.newPage()
  await page.goto(`${BASE}/access`)
  await page.waitForURL('**/login?next=%2Faccess')
  results.unauthenticated_redirect = page.url()
  await page.getByTestId('dev-user-picker').waitFor()
  results.picker_personas = await page.locator('[data-testid="dev-user-picker"] button').count()
  await page.screenshot({ path: `${OUT}/01-dev-user-picker.png`, fullPage: true })
  await page.close()
}

// 2. Every role: nav, identity and permissions as rendered vs as the backend reports
const ROLES = { platform_admin: 'Platform administrator', data_steward: 'Data steward', risk_analyst: 'Risk analyst', approver: 'Approver', auditor: 'Auditor' }
for (const [code, name] of Object.entries(ROLES)) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  await signIn(page, name)
  const nav = await navLabels(page)
  await page.goto(`${BASE}/access`)
  await page.getByTestId('my-permissions').waitFor()
  const shownPerms = (await page.getByTestId('my-permissions').innerText()).split('\n').filter(Boolean)
  const token = await page.evaluate(() => JSON.parse(sessionStorage.getItem('moodymate_session')).state.token)
  const backend = await (await fetch('http://localhost:8000/api/v1/me/permissions', { headers: { Authorization: `Bearer ${token}` } })).json()
  const usersStatus = (await fetch('http://localhost:8000/api/v1/users', { headers: { Authorization: `Bearer ${token}` } })).status
  results.roles[code] = {
    nav,
    permissions_shown: shownPerms,
    permissions_backend: backend.permissions,
    shown_matches_backend: JSON.stringify([...shownPerms].sort()) === JSON.stringify([...backend.permissions].sort()),
    users_section_visible: await page.getByRole('heading', { name: 'Users' }).count() > 0,
    backend_GET_users_status: usersStatus,
  }
  if (code === 'platform_admin') {
    await page.screenshot({ path: `${OUT}/02-access-platform-admin.png`, fullPage: true })
    await page.goto(`${BASE}/access/users`)
    await page.locator('table').waitFor()
    results.admin_users_rows = await page.locator('tbody tr').count()
    await page.screenshot({ path: `${OUT}/03-users-platform-admin.png`, fullPage: true })
  }
  if (code === 'auditor') {
    // Direct URL to a forbidden page: UI 403 state AND backend 403 for the same request
    let backendStatus = null
    page.on('response', (r) => { if (r.url().includes('/api/v1/users')) backendStatus = r.status() })
    await page.goto(`${BASE}/access/users`)
    await page.getByText("You don't have access to this").waitFor()
    await page.waitForTimeout(500)
    results.auditor_direct_url_users = { ui: await page.locator('main').innerText(), backend_status_seen_by_browser: backendStatus }
    await page.screenshot({ path: `${OUT}/04-forbidden-auditor-users.png`, fullPage: true })
  }
  if (code === 'risk_analyst') {
    await page.goto(`${BASE}/audit`)
    await page.getByText("You don't have access to this").waitFor()
    results.risk_analyst_direct_url_audit = await page.locator('main').innerText()
    await page.screenshot({ path: `${OUT}/05-forbidden-risk-analyst-audit.png`, fullPage: true })
    // Sign out returns to sign-in and clears the token
    await page.getByRole('button', { name: 'Log out' }).click()
    await page.waitForURL('**/login')
    results.sign_out = { url: page.url(), token_after: await page.evaluate(() => JSON.parse(sessionStorage.getItem('moodymate_session') ?? '{}')?.state?.token ?? null) }
  }
  await page.close()
}

// 3. Expired/invalid token mid-session -> back to sign-in
{
  const page = await browser.newPage()
  await signIn(page, 'Auditor')
  await page.evaluate(() => {
    const s = JSON.parse(sessionStorage.getItem('moodymate_session'))
    s.state.token = s.state.token.slice(0, -4) + 'AAAA'
    sessionStorage.setItem('moodymate_session', JSON.stringify(s))
  })
  await page.goto(`${BASE}/access`)
  await page.waitForURL('**/login?next=*', { timeout: 10000 })
  results.tampered_token_redirect = page.url()
  await page.close()
}
console.log(JSON.stringify(results, null, 2))
await browser.close()
