import { existsSync } from 'node:fs'
import { defineConfig } from '@playwright/test'

const baseURL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173'
const parsedBaseURL = new URL(baseURL)
const previewHost = parsedBaseURL.hostname
const previewPort = parsedBaseURL.port || (parsedBaseURL.protocol === 'https:' ? '443' : '80')

const chromeCandidates = [
  process.env.PLAYWRIGHT_CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  process.env.LOCALAPPDATA
    ? `${process.env.LOCALAPPDATA}\\Google\\Chrome\\Application\\chrome.exe`
    : undefined,
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].filter((candidate): candidate is string => Boolean(candidate))

const chromeExecutable = chromeCandidates.find((candidate) => existsSync(candidate))

if (!chromeExecutable) {
  throw new Error(
    '未找到系统 Chrome。请安装 Chrome，或通过 PLAYWRIGHT_CHROME_PATH 指向 chrome 可执行文件；本套 E2E 不下载 Playwright 浏览器。',
  )
}

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  outputDir: './test-results',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 90_000,
  expect: {
    timeout: 15_000,
  },
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],
  use: {
    baseURL,
    browserName: 'chromium',
    headless: process.env.E2E_HEADED !== '1',
    launchOptions: {
      executablePath: chromeExecutable,
    },
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop-system-chrome',
      use: {
        viewport: { width: 1440, height: 900 },
        deviceScaleFactor: 1,
      },
    },
    {
      name: 'mobile-390-system-chrome',
      use: {
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 1,
        hasTouch: true,
        isMobile: true,
      },
    },
  ],
  webServer: process.env.E2E_SKIP_WEBSERVER === '1'
    ? undefined
    : {
        command: `npm run preview -- --host ${previewHost} --port ${previewPort} --strictPort`,
        url: baseURL,
        reuseExistingServer: true,
        timeout: 120_000,
        env: {
          VITE_API_TARGET: process.env.E2E_API_TARGET ?? 'http://127.0.0.1:8000',
        },
      },
})
