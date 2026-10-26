import { test, expect, type Page } from '@playwright/test'

const headers = { Origin: `https://localhost:${process.env.AUTO_PLAN_BROWSER_PORT ?? '8774'}`, 'X-Auto-Plan-Request': '1' }

async function prepare(page: Page) {
  await page.goto('/planning/login')
  await page.getByRole('textbox', { name: '公司账号' }).fill('SHARED_EDITOR')
  await page.getByLabel('公司密码', { exact: true }).fill('company-secret')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible()
  const head = await (await page.request.get('/planning/api/plans/current')).json()
  const saved = await page.request.post('/planning/api/plans/save', { headers, data: {
    base_revision: head.revision, input_version: head.input_version, latest_adoption: head.latest_adoption,
    operation_id: crypto.randomUUID(), pending: true, operations: [{ kind: 'replace_plan', plan: {
      initial_states: [{ line_code: 'L', recipe_code: 'R' }],
      segments: [{ id: 'effective-m', date: '2026-10-15', line_code: 'L', model_code: 'M', quantity: 400, hours: '10', order: 0 }],
    } }],
  } })
  expect(saved.ok()).toBeTruthy()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '排程', exact: true }).click()
  await page.getByLabel('追加机种', { exact: true }).selectOption('P')
  await page.getByLabel('追加起日', { exact: true }).fill('2026-10-16')
  await page.getByLabel('追加终日', { exact: true }).fill('2026-10-16')
  await page.getByRole('button', { name: '读取可生产线体', exact: true }).click()
  await expect(page.getByLabel('追加线体', { exact: true }).locator('option[value="L"]')).toHaveCount(1)
  await page.getByLabel('追加线体', { exact: true }).selectOption('L')
  await page.getByLabel('操作数量', { exact: true }).fill('100')
  await page.getByRole('button', { name: '加入完整动作并预览', exact: true }).click()
  await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible()
}

test('summary, week navigation and day details use one preview and invalidate together', async ({ page }) => {
  test.skip(process.env.AUTO_PLAN_BROWSER_EFFECTIVE_PREVIEW_FIXTURE !== '1', 'Requires frozen shared-plan fixture')
  await prepare(page)
  const summary = page.getByRole('region', { name: '排程摘要' })
  await expect(summary.locator('div').filter({ hasText: '切机次数' })).toContainText('1')
  await expect(summary.locator('div').filter({ hasText: '未补足缺口' })).toContainText('1000')
  await expect(page.getByRole('navigation', { name: '十五周导航' }).getByRole('button').first()).toContainText('缺口 1000')
  await page.getByRole('button', { name: '2026-10-16 L 日内详情', exact: true }).click()
  await expect(page.getByRole('region', { name: '每日容量依据' })).toContainText('完整计划切机 1 次')
  const unchanged = await (await page.request.get('/planning/api/plans/current')).json()
  expect(unchanged.evaluation.switch_count).toBe(0)
  expect(unchanged.evaluation.output_totals.gap).toBe(1100)
  await page.getByRole('button', { name: /本机临时稿/ }).click()
  await page.getByRole('region', { name: '本机完整动作队列' }).getByRole('checkbox').first().uncheck()
  await expect(page.getByRole('region', { name: '计划状态' })).toContainText('待重新预览')
  await expect(summary.locator('div').filter({ hasText: '未补足缺口' })).toContainText('—')
  await expect(page.getByRole('navigation', { name: '十五周导航' }).getByRole('button').first()).toContainText('待重新预览')
  await expect(page.getByRole('button', { name: '正式采用', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '预览本机修改', exact: true }).click()
  await expect(summary.locator('div').filter({ hasText: '切机次数' })).toContainText('0')
  await expect(summary.locator('div').filter({ hasText: '未补足缺口' })).toContainText('1100')
})

test('input maintenance invalidates the preview, retains the original action basis and requires current server checks', async ({ page }) => {
  test.skip(process.env.AUTO_PLAN_BROWSER_EFFECTIVE_PREVIEW_FIXTURE !== '1', 'Requires frozen shared-plan fixture')
  await prepare(page)
  const before = await (await page.request.get('/planning/api/plans/current')).json()
  const nav = page.getByRole('navigation', { name: '主要导航' })
  await nav.getByRole('link', { name: '需求与资料', exact: true }).click()
  await page.getByRole('link', { name: '基础资料', exact: true }).click()
  await page.getByLabel('资料类别', { exact: true }).selectOption('line_yields')
  await page.getByLabel('粘贴表格', { exact: true }).fill('month\tmodel_code\tpercent\n2026-10\tP\t50')
  await page.getByRole('button', { name: '预览粘贴内容', exact: true }).click()
  await expect(page.getByText('预览通过，尚未提交', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '确认提交整批', exact: true }).click()
  await expect(page.getByText(/已提交资料版本/)).toBeVisible()
  await expect(page.getByRole('region', { name: '计划状态' })).toContainText('待重新预览')
  await nav.getByRole('link', { name: '排程', exact: true }).click()
  await expect(page.getByRole('region', { name: '计划状态' })).toContainText(`原动作基准：共享修订 ${before.revision}，输入版本 ${before.input_version}`)
  const summary = page.getByRole('region', { name: '排程摘要' })
  await expect(summary.locator('div').filter({ hasText: '未补足缺口' })).toContainText('—')
  await expect(page.getByRole('button', { name: '正式采用', exact: true })).toHaveCount(0)
  const stale = await page.request.post('/planning/api/plans/adopt', { headers, data: {
    base_revision: before.revision, input_version: before.input_version, latest_adoption: before.latest_adoption,
    operations: [], operation_id: crypto.randomUUID(),
  } })
  expect(stale.status()).toBe(409)
  expect((await stale.json()).detail).toContain('输入资料已变化')
  await page.getByRole('button', { name: '按最新资料重新预览', exact: true }).click()
  await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible()
  await expect(summary.locator('div').filter({ hasText: '未补足缺口' })).toContainText('1050')
  await expect(page.getByRole('region', { name: '计划状态' })).toContainText('本机预览')
  await page.getByRole('button', { name: '继续编辑', exact: true }).click()
  await page.getByRole('button', { name: /本机临时稿/ }).click()
  await expect(page.getByRole('region', { name: '本机完整动作队列' })).toContainText('100片')
})
