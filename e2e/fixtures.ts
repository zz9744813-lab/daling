import { writeFile } from 'node:fs/promises'
import { expect, test as base, type Page, type Request, type TestInfo } from '@playwright/test'

export interface BrowserDiagnostics {
  consoleErrors: string[]
  pageErrors: string[]
  requestFailures: string[]
  responseFailures: string[]
  mutationRequests: string[]
}

function requestLabel(request: Request) {
  return `${request.method()} ${request.url()}`
}

function startBrowserDiagnostics(page: Page): BrowserDiagnostics {
  const diagnostics: BrowserDiagnostics = {
    consoleErrors: [],
    pageErrors: [],
    requestFailures: [],
    responseFailures: [],
    mutationRequests: [],
  }

  page.on('console', (message) => {
    if (message.type() === 'error') {
      diagnostics.consoleErrors.push(message.text())
    }
  })
  page.on('pageerror', (error) => {
    diagnostics.pageErrors.push(error.stack ?? error.message)
  })
  page.on('requestfailed', (request) => {
    const failure = request.failure()?.errorText ?? 'unknown failure'
    // Browser navigation and EventSource teardown can intentionally abort an
    // in-flight GET. Keep real failures strict without treating cancellation as
    // an application outage.
    if (!/ERR_ABORTED|NS_BINDING_ABORTED|cancell?ed/i.test(failure)) {
      diagnostics.requestFailures.push(`${requestLabel(request)} :: ${failure}`)
    }
  })
  page.on('response', (response) => {
    if (response.status() >= 400) {
      diagnostics.responseFailures.push(
        `${response.status()} ${response.request().method()} ${response.url()}`,
      )
    }
  })
  page.on('request', (request) => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      diagnostics.mutationRequests.push(requestLabel(request))
    }
  })

  return diagnostics
}

async function attachDiagnostics(testInfo: TestInfo, diagnostics: BrowserDiagnostics) {
  const diagnosticsPath = testInfo.outputPath('browser-diagnostics.json')
  await writeFile(diagnosticsPath, JSON.stringify(diagnostics, null, 2), 'utf8')
  await testInfo.attach('browser-diagnostics.json', {
    path: diagnosticsPath,
    contentType: 'application/json',
  })
}

export const test = base.extend<{ browserDiagnostics: BrowserDiagnostics }>({
  browserDiagnostics: async ({ page }, use, testInfo) => {
    const diagnostics = startBrowserDiagnostics(page)
    await use(diagnostics)
    await attachDiagnostics(testInfo, diagnostics)

    expect.soft(diagnostics.consoleErrors, '浏览器 Console 不应出现 error').toEqual([])
    expect.soft(diagnostics.pageErrors, '页面不应抛出未捕获异常').toEqual([])
    expect.soft(diagnostics.requestFailures, '不应有真实网络请求失败').toEqual([])
    expect.soft(diagnostics.responseFailures, '不应有 HTTP 4xx/5xx').toEqual([])
  },
})

export { expect }
