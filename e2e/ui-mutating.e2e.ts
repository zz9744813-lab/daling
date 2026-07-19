import { randomUUID } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import type { APIRequestContext, Page, TestInfo } from '@playwright/test'
import { expect, test, type BrowserDiagnostics } from './fixtures'

const OUTLINE_ITEM_COUNT = 620
const initialPrompt = 'E2E 初始提示词：严格遵守大纲，不扩写未授权设定。'
const savedPrompt = 'E2E 刷新回读提示词：逐场景核对人物动机、因果链与时间线；任何冲突必须先纠正。'
const finalAnchor = `E2E-ONLY-LAST-${String(OUTLINE_ITEM_COUNT).padStart(4, '0')}`

interface CreatedProject {
  id: string
  title: string
}

async function expectNoHorizontalOverflow(page: Page, label: string) {
  const geometry = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    documentWidth: document.documentElement.scrollWidth,
    bodyWidth: document.body.scrollWidth,
  }))
  expect(
    Math.max(geometry.documentWidth, geometry.bodyWidth),
    `${label} 不应产生页面级横向溢出：${JSON.stringify(geometry)}`,
  ).toBeLessThanOrEqual(geometry.viewportWidth + 1)
}

function buildLargeOutline() {
  const sections = [
    '# E2E 长篇大纲',
    '总规则：所有证据必须能够回溯到本文件，禁止模型补写。',
  ]
  for (let index = 1; index <= OUTLINE_ITEM_COUNT; index += 1) {
    const ordinal = String(index).padStart(4, '0')
    sections.push(
      `## 第${index}章 验收节点 ${ordinal}`,
      `证据锚点 E2E-SOURCE-${ordinal}：角色在第 ${index} 个检查点执行不可替代动作；代价为 ${index} 枚刻度。${index === OUTLINE_ITEM_COUNT ? finalAnchor : ''}`,
    )
  }
  return `${sections.join('\n\n')}\n`
}

function mutationPath(label: string) {
  const separator = label.indexOf(' ')
  const method = label.slice(0, separator)
  const url = new URL(label.slice(separator + 1))
  return `${method} ${url.pathname}`
}

async function jsonResponse<T>(responsePromise: Promise<import('@playwright/test').Response>) {
  const response = await responsePromise
  expect(response.ok(), `${response.request().method()} ${response.url()} 应成功`).toBe(true)
  return response.json() as Promise<T>
}

async function enterAdvancedCockpit(page: Page) {
  const enter = page.getByRole('button', { name: '进入高级创作舱' })
  if (await enter.isVisible()) await enter.click()
  await expect(page.getByLabel('24 小时自动生产状态')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByLabel('生产证据状态')).toBeVisible()
}

function seedAcceptedChapter(projectId: string) {
  const databasePath = process.env.E2E_DB_PATH
  if (!databasePath) throw new Error('隔离验收缺少 E2E_DB_PATH')
  const python = process.env.E2E_PYTHON || (process.platform === 'win32' ? 'python.exe' : 'python3')
  const script = fileURLToPath(new URL('./seed_isolated_chapter.py', import.meta.url))
  const result = spawnSync(python, [script, databasePath, projectId], {
    encoding: 'utf8',
    windowsHide: true,
    timeout: 30_000,
  })
  if (result.status !== 0) {
    throw new Error(
      `隔离章节前置数据写入失败（exit=${result.status}）：${result.stderr || result.stdout}`,
    )
  }
  return JSON.parse(result.stdout) as Record<string, unknown>
}

async function cleanupDisposableProject(
  request: APIRequestContext,
  page: Page,
  project: CreatedProject | null,
  title: string,
) {
  await page.goto('about:blank')
  let targets = project ? [project] : []
  if (!targets.length) {
    const list = await request.get('/api/projects')
    if (list.ok()) {
      const projects = (await list.json()) as CreatedProject[]
      targets = projects.filter((candidate) => candidate.title === title)
    }
  }
  for (const target of targets) {
    const status = await request.get(`/api/pipeline/${target.id}/continuous/status`)
    if (status.ok()) {
      const body = (await status.json()) as { run_id?: string | null; desired_state?: string }
      if (body.run_id && body.desired_state !== 'stopped') {
        await request.post(`/api/pipeline/${target.id}/continuous/stop`)
      }
    }
    const removed = await request.delete(`/api/projects/${target.id}`)
    expect(removed.ok(), `一次性项目 ${target.id} 应完成清理`).toBe(true)
    const absent = await request.get(`/api/projects/${target.id}`)
    expect(absent.status(), '清理后项目应不可回读').toBe(404)
  }
}

test.describe('隔离数据库真实写回验收', () => {
  test.skip(process.env.E2E_MUTATING !== '1', '仅由 test:e2e:isolated 在空白隔离数据库中启用')

  test('创建、上传、提示词持久化、24H 专用生命周期与证据分页搜索均可回读', async ({
    page,
    request,
    browserDiagnostics,
  }, testInfo) => {
    const nonce = randomUUID().slice(0, 8)
    const title = `E2E-真实写回-${nonce}`
    const outline = buildLargeOutline()
    const outlinePath = testInfo.outputPath(`e2e-large-outline-${nonce}.md`)
    await writeFile(outlinePath, outline, 'utf8')
    let project: CreatedProject | null = null
    const continuousResponses: Array<Record<string, unknown>> = []
    const nodePageOffsets: number[] = []

    page.on('request', (requestEvent) => {
      const url = new URL(requestEvent.url())
      if (
        requestEvent.method() === 'GET' &&
        /\/api\/intelligence\/[^/]+\/outline\/nodes$/.test(url.pathname)
      ) {
        nodePageOffsets.push(Number(url.searchParams.get('offset') ?? 0))
      }
    })

    try {
      await test.step('从首页上传大纲并创建一次性项目', async () => {
        await page.goto('/')
        await expect(page.getByText('大纲全文分块', { exact: true })).toBeVisible()
        await expect(page.getByText('24H 持久任务', { exact: true })).toBeVisible()
        const uploadEntry = page.getByRole('button', { name: '上传已有大纲' }).first()
        await expect(uploadEntry).toBeVisible()
        const chooserPromise = page.waitForEvent('filechooser')
        await uploadEntry.click()
        await (await chooserPromise).setFiles(outlinePath)
        await expect(page.getByText(`e2e-large-outline-${nonce}.md`)).toBeVisible()

        const inspectionPromise = page.waitForResponse(
          (response) =>
            response.request().method() === 'POST' &&
            response.url().endsWith('/api/projects/outline/inspect'),
        )
        await page.getByRole('button', { name: '开始构思' }).click()
        await expect(page).toHaveURL(/\/projects\/new$/)
        const inspection = await jsonResponse<Record<string, unknown>>(inspectionPromise)
        expect(inspection.exact_source_covered).toBe(true)
        expect(Number(inspection.node_count)).toBeGreaterThan(500)

        await page.getByLabel('作品标题').first().fill(title)
        const projectPrompt = page.getByLabel('项目总提示词')
        await page.getByRole('button', { name: /记忆一致性/ }).first().click()
        await expect(projectPrompt).toHaveValue(/【记忆一致性】/)
        await projectPrompt.fill(initialPrompt)
        await expect(page.getByText(/本次预检已通过可验证性检查/)).toBeVisible({ timeout: 60_000 })

        const createPromise = page.waitForResponse(
          (response) =>
            response.request().method() === 'POST' &&
            new URL(response.url()).pathname === '/api/projects',
        )
        const uploadPromise = page.waitForResponse(
          (response) =>
            response.request().method() === 'POST' &&
            /\/api\/projects\/[^/]+\/upload-outline$/.test(new URL(response.url()).pathname),
        )
        const mobileConfiguration = page.getByRole('button', { name: '打开创作配置' })
        if (await mobileConfiguration.isVisible()) {
          await mobileConfiguration.click()
          await expect(page.getByRole('heading', { name: '创作配置' })).toBeVisible()
          await expectNoHorizontalOverflow(page, '移动端创作配置抽屉')
        }
        const createButton = page.getByRole('button', { name: /创建项目|按当前信息创建/ }).last()
        await expect(createButton).toBeEnabled({ timeout: 60_000 })
        await createButton.click()
        project = await jsonResponse<CreatedProject>(createPromise)
        const upload = await jsonResponse<Record<string, unknown>>(uploadPromise)
        expect(upload.exact_source_covered).toBe(true)
        expect(Number(upload.node_count)).toBeGreaterThan(500)
        await expect(page).toHaveURL(/\/cockpit$/)
      })

      await test.step('通过真实 API 回读项目、大纲与初始提示词', async () => {
        expect(project).not.toBeNull()
        const current = project!
        const projectResponse = await request.get(`/api/projects/${current.id}`)
        expect(projectResponse.ok()).toBe(true)
        expect((await projectResponse.json()).title).toBe(title)

        const outlineResponse = await request.get(`/api/projects/${current.id}/outline`)
        expect(outlineResponse.ok()).toBe(true)
        const persistedOutline = await outlineResponse.json()
        expect(persistedOutline.text).toContain('E2E-SOURCE-0001')
        expect(persistedOutline.text).toContain(finalAnchor)
        expect(persistedOutline.outline_index.exact_source_covered).toBe(true)
        expect(persistedOutline.outline_index.node_count).toBeGreaterThan(500)

        const promptResponse = await request.get(`/api/projects/${current.id}/custom-prompt`)
        expect(promptResponse.ok()).toBe(true)
        expect((await promptResponse.json()).text).toBe(initialPrompt)
      })

      await test.step('保存提示词，整页刷新后仍从后端回读', async () => {
        const promptEntry = page.getByRole('button', { name: '查看并修改提示词' })
        await expect(promptEntry).toBeVisible({ timeout: 30_000 })
        await promptEntry.click()
        const editor = page.locator('textarea[maxlength="20000"]')
        await expect(editor).toHaveValue(initialPrompt)
        await expect(page.getByRole('region', { name: '项目提示词契约模板' })).toBeVisible()
        await expectNoHorizontalOverflow(page, '项目提示词编辑器')
        await editor.fill(savedPrompt)
        const savePromise = page.waitForResponse(
          (response) =>
            response.request().method() === 'PUT' &&
            response.url().endsWith(`/api/projects/${project!.id}/custom-prompt`),
        )
        await page.getByRole('button', { name: '保存提示词' }).click()
        const saved = await jsonResponse<{ text: string }>(savePromise)
        expect(saved.text).toBe(savedPrompt)

        await page.reload()
        await expect(page.getByRole('button', { name: '查看并修改提示词' })).toBeVisible({ timeout: 30_000 })
        await page.getByRole('button', { name: '查看并修改提示词' }).click()
        await expect(page.locator('textarea[maxlength="20000"]')).toHaveValue(savedPrompt)
        await page.getByRole('button', { name: '取消', exact: true }).click()

        const promptResponse = await request.get(`/api/projects/${project!.id}/custom-prompt`)
        expect((await promptResponse.json()).text).toBe(savedPrompt)
      })

      await test.step('24H 启动、暂停、恢复、停止只走 Continuous API', async () => {
        const seededChapter = seedAcceptedChapter(project!.id)
        expect(seededChapter.status).toBe('approved')
        await enterAdvancedCockpit(page)
        await expectNoHorizontalOverflow(page, '高级创作舱')
        await page.screenshot({ path: testInfo.outputPath('cockpit-polish.png'), fullPage: true })

        const startPromise = page.waitForResponse(
          (response) =>
            response.request().method() === 'POST' &&
            response.url().endsWith(`/api/pipeline/${project!.id}/continuous/start`),
        )
        await page.getByRole('button', { name: '启动 24H 写作', exact: true }).click()
        const started = await jsonResponse<Record<string, unknown>>(startPromise)
        continuousResponses.push(started)
        expect(started.run_id).toBeTruthy()
        expect(started.desired_state).toBe('running')
        await expect(page.getByRole('status')).toContainText(/24H 持久化任务已确认(启动|恢复)/)

        const pausePromise = page.waitForResponse(
          (response) =>
            response.request().method() === 'POST' &&
            response.url().endsWith(`/api/pipeline/${project!.id}/continuous/pause`),
        )
        await page.getByRole('button', { name: '暂停 24H 写作', exact: true }).click()
        const paused = await jsonResponse<Record<string, unknown>>(pausePromise)
        continuousResponses.push(paused)
        expect(paused.run_id).toBe(started.run_id)
        expect(paused.desired_state).toBe('paused')
        expect(paused.status).toBe('paused')

        const resumePromise = page.waitForResponse(
          (response) =>
            response.request().method() === 'POST' &&
            response.url().endsWith(`/api/pipeline/${project!.id}/continuous/resume`),
        )
        await page.getByRole('button', { name: '继续 24H 写作', exact: true }).click()
        const resumed = await jsonResponse<Record<string, unknown>>(resumePromise)
        continuousResponses.push(resumed)
        expect(resumed.run_id).toBe(started.run_id)
        expect(resumed.desired_state).toBe('running')

        const stopPromise = page.waitForResponse(
          (response) =>
            response.request().method() === 'POST' &&
            response.url().endsWith(`/api/pipeline/${project!.id}/continuous/stop`),
        )
        await page.getByLabel('输入给写作系统的可执行指令').fill('停止 24H 写作')
        await page.getByRole('button', { name: '执行', exact: true }).click()
        const stopped = await jsonResponse<Record<string, unknown>>(stopPromise)
        continuousResponses.push(stopped)
        expect(stopped.run_id).toBe(started.run_id)
        expect(stopped.desired_state).toBe('stopped')
        expect(stopped.status).toBe('stopped')

        const statusResponse = await request.get(`/api/pipeline/${project!.id}/continuous/status`)
        expect(statusResponse.ok()).toBe(true)
        const durableStatus = await statusResponse.json()
        expect(durableStatus.run_id).toBe(started.run_id)
        expect(durableStatus.desired_state).toBe('stopped')
        expect(durableStatus.status).toBe('stopped')

        await page.getByLabel('24 小时自动生产状态').getByRole('button').first().click()
        await expect(page.getByRole('heading', { name: '24 小时自动生产总控' })).toBeVisible()
        await page.getByRole('tab', { name: /生产契约/ }).click()
        await expect(page.getByLabel('本次自动生产目标章数')).not.toHaveValue('')
        expect(Number(await page.getByLabel('最低真实纠错闭环').inputValue())).toBeGreaterThan(0)
        await page.screenshot({ path: testInfo.outputPath('autopilot-safe-contract.png'), fullPage: true })
        await page.getByLabel('关闭 24 小时总控').click()

        const eventsResponse = await request.get(`/api/pipeline/${project!.id}/continuous/events?limit=100`)
        expect(eventsResponse.ok()).toBe(true)
        const events = (await eventsResponse.json()) as Array<{ event_type: string }>
        const eventTypes = events.map((event) => event.event_type)
        expect(eventTypes).toEqual(expect.arrayContaining(['run_started', 'run_paused', 'run_resumed', 'run_stopped']))
      })

      await test.step('证据台完整读取后执行渲染分页与唯一锚点搜索', async () => {
        const evidenceEntry = page.getByLabel('生产证据状态').getByRole('button').first()
        await evidenceEntry.click()
        await expect(page.getByRole('heading', { name: '可验证生产证据' })).toBeVisible()
        await expectNoHorizontalOverflow(page, '生产证据台')
        await page.screenshot({ path: testInfo.outputPath('evidence-loading-skeleton.png'), fullPage: true })
        await expect(page.getByText('来源节点已完整分页读取')).toBeVisible({ timeout: 60_000 })
        await page.screenshot({ path: testInfo.outputPath('evidence-loaded.png'), fullPage: true })
        expect(nodePageOffsets).toEqual(expect.arrayContaining([0, 500]))
        expect(Math.max(...nodePageOffsets)).toBeGreaterThanOrEqual(500)

        const clearChapterFilter = page.getByRole('button', { name: '清除', exact: true })
        if (await clearChapterFilter.isVisible()) await clearChapterFilter.click()

        const progress = page.getByText(/已显示 \d[\d,]* \/ \d[\d,]* 条匹配证据/).first()
        const beforeText = await progress.innerText()
        const beforeShown = Number(beforeText.match(/已显示 ([\d,]+)/)?.[1].replaceAll(',', ''))
        const loadMore = page.getByRole('button', { name: /继续加载 \d[\d,]* 条/ }).first()
        await expect(loadMore).toBeVisible()
        await loadMore.click()
        await expect.poll(async () => {
          const text = await progress.innerText()
          return Number(text.match(/已显示 ([\d,]+)/)?.[1].replaceAll(',', ''))
        }).toBeGreaterThan(beforeShown)

        const search = page.getByLabel('检索当前证据')
        await search.fill(finalAnchor)
        await expect(page.getByText(finalAnchor, { exact: false }).first()).toBeVisible()
        await expect(page.getByText(/已显示 1 \/ 1 条匹配证据/).first()).toBeVisible()
        await search.fill('E2E-NOT-FOUND-ANCHOR')
        await expect(page.getByText('没有匹配检索词的来源节点')).toBeVisible()
        await page.getByLabel('清除证据检索').click()
        await expect(page.getByRole('button', { name: /继续加载 \d[\d,]* 条/ }).first()).toBeVisible()
        await page.getByLabel('关闭证据台').click()
      })

      const browserMutationPaths = browserDiagnostics.mutationRequests.map(mutationPath)
      expect(
        browserMutationPaths.filter((entry) => entry.includes('/continuous/')),
        '24H 生命周期必须逐项命中持久化 Continuous API',
      ).toEqual([
        `POST /api/pipeline/${project!.id}/continuous/start`,
        `POST /api/pipeline/${project!.id}/continuous/pause`,
        `POST /api/pipeline/${project!.id}/continuous/resume`,
        `POST /api/pipeline/${project!.id}/continuous/stop`,
      ])
      expect(
        browserMutationPaths.filter((entry) =>
          /work[-_/]?sessions?|\/api\/cockpit\/[^/]+\/command|\/api\/pipeline\/[^/]+\/run$/.test(entry),
        ),
        '24H 控制不得回退到普通 WorkSession、Boss command 或手动 Pipeline',
      ).toEqual([])

      const durableEvidencePath = testInfo.outputPath('durable-e2e-evidence.json')
      await writeFile(
        durableEvidencePath,
        JSON.stringify({
          project,
          nodePageOffsets,
          browserMutationPaths,
          continuousResponses,
        }, null, 2),
        'utf8',
      )
      await testInfo.attach('durable-e2e-evidence.json', {
        path: durableEvidencePath,
        contentType: 'application/json',
      })
    } finally {
      await cleanupDisposableProject(request, page, project, title)
    }
  })
})
