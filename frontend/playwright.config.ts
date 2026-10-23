import {defineConfig} from '@playwright/test';

/**
 * E2E runs the real stack: backend in single-process mode plus the built
 * frontend served by `vite preview`, which proxies /ws and /api.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  use: {baseURL: 'http://localhost:4173', trace: 'retain-on-failure'},
  webServer: [
    {
      command: 'python -m optdesk all',
      cwd: '../backend',
      url: 'http://localhost:8000/healthz',
      reuseExistingServer: !process.env.CI,
      env: {PYTHONPATH: 'src', OPTDESK_LOG_LEVEL: 'WARNING'},
    },
    {
      command: 'npm run build && npm run preview',
      url: 'http://localhost:4173',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
