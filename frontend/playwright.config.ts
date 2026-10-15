import { defineConfig } from '@playwright/test'
const port=process.env.AUTO_PLAN_BROWSER_PORT ?? '8774'
export default defineConfig({
  testDir: './tests', workers: 1,
  use: { baseURL: `https://localhost:${port}`, ignoreHTTPSErrors: true },
  webServer: { command: 'cd .. && UV_CACHE_DIR=/tmp/auto-plan-uv-cache uv run --no-sync python -m tests.browser_server', url: `https://localhost:${port}/planning/api/health`, ignoreHTTPSErrors: true, reuseExistingServer: false },
})
