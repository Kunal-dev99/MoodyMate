import { defineConfig, devices } from '@playwright/test'

/**
 * Frontend smoke checks (FE-6.2) against the real stack: a migrated, seeded database, the
 * backend in dev-auth mode (ENV=test) and the production build of the frontend.
 *
 * The tests share one database and the proposal flow changes it, so they run in order
 * with one worker. CI starts from a fresh database each run; locally, point
 * DATABASE_URL at a disposable copy (D-03).
 */
const API_PORT = 8000
const WEB_PORT = 3000
const backendDb = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL
// CI installs the backend without a virtualenv; locally it runs from backend/.venv.
const uvicorn = process.env.E2E_UVICORN ?? '.venv/bin/uvicorn'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  timeout: 60_000,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 1000 },
        // Use a preinstalled browser when one is provided (e.g. a sandbox without downloads).
        launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
      },
    },
  ],
  webServer: [
    {
      command: `${uvicorn} app.main:create_app --factory --port ${API_PORT}`,
      cwd: '../backend',
      url: `http://localhost:${API_PORT}/api/v1/health`,
      reuseExistingServer: !process.env.CI,
      env: { ENV: 'test', ...(backendDb ? { DATABASE_URL: backendDb } : {}) },
      timeout: 60_000,
    },
    {
      command: `npm run start -- -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/login`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
})
