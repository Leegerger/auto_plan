import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './tests', workers: 1,
  use: { baseURL: 'https://localhost:8774', ignoreHTTPSErrors: true },
  webServer: { command: 'cd .. && UV_CACHE_DIR=/tmp/auto-plan-uv-cache uv run --no-sync python -m tests.browser_server', url: 'https://localhost:8774/planning/api/health', ignoreHTTPSErrors: true, reuseExistingServer: false },
})
