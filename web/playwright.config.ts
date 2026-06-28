import { defineConfig, devices } from '@playwright/test';

// E2E + screenshot-evidence run against the dev server on :3001. Reuses an
// already-running dev server if present, else starts one.
export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  timeout: 30_000,
  use: {
    baseURL: 'http://localhost:3001',
    viewport: { width: 1380, height: 880 },
    actionTimeout: 10_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3001/home',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
