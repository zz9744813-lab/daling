import { existsSync } from 'node:fs'
import { expect, test, type BrowserDiagnostics } from './fixtures'
import type { Locator, Page, TestInfo } from '@playwright/test'

const outlinePath =
  process.env.E2E_OUTLINE_PATH ?? 'F:\\小说\\实验\\顶级\\《人间种》.docx'

async function capture(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`${name}.png`)
  await page.screenshot({ path, fullPage: true })
  await testInfo.attach(name, { path, contentType: 'image/png' })
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

async function expectUnobscured(locator: Locator, label: string) {
  await locator.scrollIntoViewIfNeeded()
  await expect(locator, `${label} 应可见`).toBeVisible()
  const result = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect()
    const x = Math.min(window.innerWidth - 1, Math.max(0, rect.left + rect.width / 2))
    const y = Math.min(window.innerHeight - 1, Math.max(0, rect.top + rect.height / 2))
    const hit = document.elementFromPoint(x, y)
    return {
      width: rect.width,
      height: rect.height,
      insideViewport:
        rect.width > 0 &&
        rect.height > 0 &&
        rect.right > 0 &&
        rect.bottom > 0 &&
        rect.left < window.innerWidth &&
        rect.top < window.innerHeight,
      receivesPointer:
        hit != null &&
        (hit === element || element.contains(hit) || hit.contains(element)),
      hitTag: hit?.tagName ?? null,
    }
  })
  expect(result.insideViewport, `${label} 应位于视口内：${JSON.stringify(result)}`).toBe(true)
  expect(result.receivesPointer, `${label} 中心点不应被其他元素遮挡：${JSON.stringify(result)}`).toBe(true)
}

function expectOnlyOutlineInspectionMutation(diagnostics: BrowserDiagnostics) {
  expect(diagnostics.mutationRequests, '只允许只读式的大纲预检 POST').toHaveLength(1)
  expect(diagnostics.mutationRequests[0]).toMatch(
    /^POST .*\/api\/projects\/outline\/inspect$/,
  )
}

async function enterFirstExistingProject(page: Page) {
  await page.goto('/')
  const deleteButton = page.locator('button[aria-label^="删除《"]').first()
  await expect(
    deleteButton,
    '真实浏览器验收需要数据库中至少存在一个项目',
  ).toBeVisible({ timeout: 20_000 })
  const projectCard = deleteButton.locator('xpath=ancestor::article')
  const title = (await projectCard.locator('h3').innerText()).trim()
  await projectCard.locator('button').first().click()
  await expect(page).toHaveURL(/\/cockpit$/)

  const enterAdvanced = page.getByRole('button', { name: '进入高级创作舱' })
  const autopilotStatus = page.getByLabel('24 小时自动生产状态')
  await expect(enterAdvanced.or(autopilotStatus).first()).toBeVisible({ timeout: 30_000 })
  if (await enterAdvanced.isVisible()) {
    await enterAdvanced.click()
  }
  await expect(autopilotStatus).toBeVisible({ timeout: 30_000 })
  return title
}

test.describe('production preview 只读浏览器验收', () => {
  test('首页上传真实大纲后显示新建页提示词与可验证预检', async ({
    page,
    browserDiagnostics,
  }, testInfo) => {
    if (!existsSync(outlinePath)) {
      throw new Error(`真实大纲不存在：${outlinePath}。可通过 E2E_OUTLINE_PATH 覆盖。`)
    }

    await page.goto('/')
    await expect(page.getByRole('button', { name: '从零开始' })).toBeVisible()
    await expect(page.getByText('大纲全文分块', { exact: true })).toBeVisible()
    await expect(page.getByText('长期记忆检索', { exact: true })).toBeVisible()
    await expect(page.getByText('真实纠错闭环', { exact: true })).toBeVisible()
    await expect(page.getByText('24H 持久任务', { exact: true })).toBeVisible()
    const uploadEntry = page.getByRole('button', { name: '上传已有大纲' }).first()
    await expectUnobscured(uploadEntry, '首页上传已有大纲入口')
    await expectNoHorizontalOverflow(page, '项目首页')

    const chooserPromise = page.waitForEvent('filechooser')
    await uploadEntry.click()
    const chooser = await chooserPromise
    await chooser.setFiles(outlinePath)
    await expect(page.getByText('《人间种》.docx')).toBeVisible()

    const inspectionPromise = page.waitForResponse(
      (response) =>
        response.url().endsWith('/api/projects/outline/inspect') &&
        response.request().method() === 'POST',
      { timeout: 60_000 },
    )
    await page.getByRole('button', { name: '开始构思' }).click()
    await expect(page).toHaveURL(/\/projects\/new$/)
    const inspectionResponse = await inspectionPromise
    expect(inspectionResponse.status(), '真实大纲预检应成功').toBe(200)

    await expect(
      page.getByRole('heading', { name: '1. 故事来源 / 详细大纲' }),
    ).toBeVisible()
    await expect(
      page.getByRole('heading', { name: '2. 项目总提示词' }),
    ).toBeVisible()
    const prompt = page.getByLabel('项目总提示词')
    await expectUnobscured(prompt, '项目总提示词输入区')
    const correctionContract = page.getByRole('button', { name: /纠错闭环/ }).first()
    await expect(correctionContract).toBeEnabled()
    await correctionContract.click()
    await expect(prompt).toHaveValue(/【质量纠错】/)
    await expect(correctionContract).toBeDisabled()
    await expect(page.getByText(/1 \/ 5 已写入/).first()).toBeVisible()
    await expect(
      page.getByText(/本次预检已通过可验证性检查/),
    ).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText('来源节点')).toBeVisible()
    await expect(page.getByText('索引覆盖')).toBeVisible()
    await expect(page.getByText('100.00%')).toBeVisible()
    await expectNoHorizontalOverflow(page, '新建项目页')

    expectOnlyOutlineInspectionMutation(browserDiagnostics)
    await capture(page, testInfo, 'new-project-outline-and-prompt')
  })

  test('现有项目创作舱暴露 24H、提示词与证据入口且不触发生产写入', async ({
    page,
    browserDiagnostics,
  }, testInfo) => {
    const projectTitle = await enterFirstExistingProject(page)

    const autopilotStatus = page.getByLabel('24 小时自动生产状态')
    await expect(autopilotStatus.getByText('24H 总控')).toBeVisible()
    await expectUnobscured(
      autopilotStatus.getByRole('button').first(),
      '24H 总控入口',
    )
    const evidenceStatus = page.getByLabel('生产证据状态')
    await expect(evidenceStatus.getByText('生产证据台')).toBeVisible()
    await expectNoHorizontalOverflow(page, `《${projectTitle}》创作舱`)
    await capture(page, testInfo, 'cockpit-readonly-entry-points')

    await autopilotStatus.getByRole('button').first().click()
    await expect(
      page.getByRole('heading', { name: '24 小时自动生产总控' }),
    ).toBeVisible()
    await expect(page.getByRole('tab', { name: /生产契约/ })).toBeVisible()
    await expect(page.getByText(/关闭浏览器不会停止任务/)).toBeVisible()
    await page.getByRole('tab', { name: /生产契约/ }).click()
    await expect(page.getByText('最低真实纠错闭环', { exact: true })).toBeVisible()
    await expect(page.getByText(/批评\/连续性审查、非同文改写与复审/)).toBeVisible()
    const targetInput = page.getByLabel('本次自动生产目标章数')
    const minimumRewriteInput = page.getByLabel('最低真实纠错闭环')
    await expect(targetInput).not.toHaveValue('')
    expect(Number(await targetInput.inputValue()), '自动生产目标默认值必须大于 0').toBeGreaterThan(0)
    expect(Number(await minimumRewriteInput.inputValue()), '真实纠错闭环默认值不得为 0').toBeGreaterThan(0)
    await expectNoHorizontalOverflow(page, '24H 总控弹层')
    await capture(page, testInfo, 'autopilot-control-center-readonly')
    await page.getByLabel('关闭 24 小时总控').click()

    const promptButton = page.getByRole('button', { name: '项目提示词' })
    await promptButton.scrollIntoViewIfNeeded()
    await expectUnobscured(promptButton, '项目提示词入口')
    await promptButton.click()
    await expect(page.getByText('AI 创作指令', { exact: true })).toBeVisible()
    await expect(page.locator('textarea[maxlength="20000"]')).toBeVisible()
    await expect(page.getByRole('region', { name: '项目提示词契约模板' })).toBeVisible()
    await expect(page.getByRole('button', { name: /24H 守护/ })).toBeVisible()
    await expectNoHorizontalOverflow(page, '项目提示词弹层')
    await capture(page, testInfo, 'project-prompt-readonly')
    await page.getByRole('button', { name: '取消', exact: true }).click()

    await expectUnobscured(
      evidenceStatus.getByRole('button').first(),
      '生产证据台入口',
    )
    await evidenceStatus.getByRole('button').first().click()
    await expect(
      page.getByRole('heading', { name: '可验证生产证据' }),
    ).toBeVisible()
    await expect(page.getByRole('tab', { name: /来源到场景/ })).toBeVisible()
    await expect(page.getByRole('tab', { name: /记忆与缓存/ })).toBeVisible()
    await expect(page.getByRole('tab', { name: /模型调用账本/ })).toBeVisible()
    await expect(page.getByText(/正在装配可验证证据|来源节点/).first()).toBeVisible()
    await expectNoHorizontalOverflow(page, '生产证据弹层')
    await capture(page, testInfo, 'intelligence-evidence-console-readonly')
    await page.getByLabel('关闭证据台').click()

    expect(
      browserDiagnostics.mutationRequests,
      '只读创作舱验收不得启动、暂停、重写、保存提示词或创建项目',
    ).toEqual([])
  })
})
