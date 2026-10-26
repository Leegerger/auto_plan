import { test, expect, type Page } from '@playwright/test'

async function enter(page: Page) {
  await page.goto('/planning/login')
  await page.getByRole('textbox', { name: '公司账号' }).fill('SHARED_EDITOR')
  await page.getByLabel('公司密码', { exact: true }).fill('company-secret')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '排程', exact: true }).click()
}

const headers = { Origin: `https://localhost:${process.env.AUTO_PLAN_BROWSER_PORT ?? '8774'}`, 'X-Auto-Plan-Request': '1' }

test('matrix shows original Monday state, first switches, empty-day reasons and hidden production from the same plan', async ({ page }) => {
  test.skip(process.env.AUTO_PLAN_BROWSER_MATRIX_FIXTURE !== '1', 'Requires matrix business fixture')
  await enter(page)
  const before = await (await page.request.get('/planning/api/plans/current')).json()
  const line = page.getByRole('rowheader', { name: 'L 一线', exact: true })
  await expect(line).toContainText('周一 08:00')
  await expect(line).toContainText('保留 R')
  await expect(line).toContainText('窗口起点')
  await expect(line).toContainText('R → S')
  await expect(line).toContainText('0.9500 小时')
  await expect(line).toContainText('已安排')
  await expect(page.getByRole('rowheader', { name: 'B 空白线', exact: true })).toContainText('起点未知')
  await expect(page.getByRole('rowheader', { name: 'F 续做线', exact: true })).toContainText('同 Recipe 续做')
  await expect(page.getByRole('rowheader', { name: 'G 未知试排线', exact: true })).toContainText('起点未知，首次切机尚未计入')
  const empty = page.getByRole('button', { name: '2026-10-16 L 日内详情', exact: true })
  await expect(empty).toContainText('可用 16.0000 小时')
  await expect(empty).toContainText('保养 5 小时')
  await expect(empty).toContainText('实验 3 小时')
  await expect(page.getByRole('button', { name: '2026-10-15 B 日内详情', exact: true })).toContainText('Holiday')
  await expect(page.getByRole('button', { name: '2026-10-15 B 日内详情', exact: true })).toContainText('可用 0.0000 小时')
  await expect(page.getByRole('button', { name: '2026-10-15 L 日内详情', exact: true })).toContainText('开工例外')
  await expect(page.getByRole('button', { name: '2026-10-16 B 日内详情', exact: true })).toContainText('锁定 N = 0 片')
  await page.getByLabel('筛选机种', { exact: true }).selectOption('M')
  await expect(page.getByRole('button', { name: '2026-10-16 C 日内详情', exact: true })).toContainText('已知零产能')
  await expect(page.getByRole('button', { name: '2026-10-16 D 日内详情', exact: true })).toContainText('缺产能资料')
  await expect(page.getByRole('button', { name: '2026-10-16 E 日内详情', exact: true })).toContainText('明确不能生产')
  const hidden = page.getByRole('button', { name: '2026-10-18 L 日内详情', exact: true })
  await expect(hidden).toContainText('另有 1 段已排生产被筛选隐藏')
  await expect(hidden).not.toContainText('空白')
  await expect(hidden).toContainText('切机 0.9500 小时')
  await hidden.click()
  await expect(page.getByRole('complementary', { name: '排程对象详情' })).toContainText('另有 1 段已排生产被筛选隐藏')
  await page.getByRole('button', { name: '显示该日全部机种', exact: true }).click()
  await expect(page.getByRole('table', { name: '日内生产顺序' })).toContainText('100片')
  await page.getByLabel('计划周次').selectOption('2')
  await expect(line).toContainText('计划推算')
  await expect(line).toContainText('保留 S')
  await page.getByLabel('计划周次').selectOption('3')
  await expect(line).toContainText('保留 S')
  const after = await (await page.request.get('/planning/api/plans/current')).json()
  expect([after.revision, after.input_version, after.latest_adoption]).toEqual([before.revision, before.input_version, before.latest_adoption])
})

test('current site confirmation is separate from Monday and an outdated confirmation does not claim residual capacity', async ({ page }) => {
  test.skip(process.env.AUTO_PLAN_BROWSER_MATRIX_FIXTURE !== '1', 'Requires matrix business fixture')
  await enter(page)
  const current = await (await page.request.get('/planning/api/plans/current')).json()
  const saved = await page.request.post('/planning/api/plans/save', { headers, data: {
    base_revision: current.revision, input_version: current.input_version, latest_adoption: current.latest_adoption,
    operation_id: crypto.randomUUID(), pending: true, operations: [
      { kind: 'replace_plan', plan: { initial_states: [{ line_code: 'L', recipe_code: 'R' }], segments: [] } },
      { kind: 'confirm_day_capacity', observed_at: '2026-10-14T08:00:00+08:00', confirmations: [{ line_code: 'L', current_recipe: 'S', remaining_hours: '1' }] },
    ],
  } })
  expect(saved.ok(), await saved.text()).toBe(true)
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '需求与资料', exact: true }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '排程', exact: true }).click()
  await expect(page.getByRole('rowheader', { name: 'L 一线', exact: true })).toContainText('保留 R')
  await page.getByRole('button', { name: '2026-10-14 L 日内详情', exact: true }).click()
  const detail = page.getByRole('region', { name: '完整日容量依据' })
  await expect(detail).toContainText('现场确认剩余工时')
  await expect(detail).toContainText('1.0000 小时')
  const confirmation = page.getByRole('region', { name: '当前现场确认' })
  await expect(confirmation).toContainText('Recipe S')
  await expect(confirmation).toContainText('2026-10-14')
  const inputs = await (await page.request.get('/planning/api/inputs')).json()
  const applied = await page.request.post('/planning/api/inputs/apply', { headers, data: { base_version: inputs.input_version, changes: { maintenance: [{ date: '2026-10-14', line_code: 'L', hours: 2 }] } } })
  expect(applied.ok()).toBe(true)
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '需求与资料', exact: true }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '排程', exact: true }).click()
  await expect(confirmation).toContainText('需重新核对')
  await expect(detail).toContainText('22.0000 小时')
})

test('first-production details show an arranged multi-switch path and its total hours', async ({ page }) => {
  test.skip(process.env.AUTO_PLAN_BROWSER_MATRIX_FIXTURE !== '1', 'Requires matrix business fixture')
  await enter(page)
  const current = await (await page.request.get('/planning/api/plans/current')).json()
  const switches = [
    { id: 'path-1', from_recipe: 'R', to_recipe: 'S', hours: '0.95' },
    { id: 'path-2', from_recipe: 'S', to_recipe: 'R', hours: '2' },
    { id: 'path-3', from_recipe: 'R', to_recipe: 'S', hours: '0.95' },
  ].map(({ hours, ...value }, order) => ({ ...value, line_code: 'L', allocations: [{ date: '2026-10-18', order, hours }] }))
  const saved = await page.request.post('/planning/api/plans/save', { headers, data: {
    base_revision: current.revision, input_version: current.input_version, latest_adoption: current.latest_adoption,
    operation_id: crypto.randomUUID(), pending: true, operations: [{ kind: 'replace_plan', plan: {
      initial_states: [{ line_code: 'L', recipe_code: 'R' }], switches,
      segments: [{ id: 'path-production', date: '2026-10-18', line_code: 'L', model_code: 'P', quantity: 100, hours: '3', order: 3 }],
    } }],
  } })
  expect(saved.ok(), await saved.text()).toBe(true)
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '需求与资料', exact: true }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '排程', exact: true }).click()
  const line = page.getByRole('rowheader', { name: 'L 一线', exact: true })
  await expect(line).toContainText('切机路径 R → S；S → R；R → S')
  await expect(line).toContainText('3.9000 小时')
  await expect(line).toContainText('已安排')
})
