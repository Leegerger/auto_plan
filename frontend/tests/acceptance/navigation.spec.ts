import { test, expect } from '@playwright/test'
import { prepareWeekNavigation, navigateWeek, prepareDayNavigation, navigateDay } from './navigation.mjs'

test('acceptance navigation starts from another week and another populated day', async ({ page }) => {
  test.skip(process.env.AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE !== '1', 'Requires the explicit shared-plan adapter fixture')
  await page.goto('/planning/login')
  await page.getByRole('textbox', { name: '公司账号' }).fill('SHARED_EDITOR')
  await page.getByLabel('公司密码', { exact: true }).fill('company-secret')
  const login = page.waitForResponse(response => response.url().endsWith('/api/auth/login'))
  await page.getByRole('button', { name: '登录', exact: true }).click()
  expect((await login).ok()).toBeTruthy()
  const before = await (await page.request.get('/planning/api/plans/current')).json()
  const rows = [
    { id: 'navigation-target', date: '2027-01-24', line_code: 'L', model_code: 'M', quantity: 100, hours: '3', order: 0 },
    { id: 'navigation-other', date: '2027-01-18', line_code: 'L', model_code: 'M', quantity: 100, hours: '3', order: 0 },
  ]
  const seeded = await page.request.post('/planning/api/plans/save', { headers: { Origin: 'https://localhost:8774', 'X-Auto-Plan-Request': '1' }, data: {
    base_revision: before.revision, input_version: before.input_version, latest_adoption: before.latest_adoption,
    operation_id: crypto.randomUUID(), pending: true, operations: [{ kind: 'replace_plan', plan: { segments: rows } }],
  } })
  expect(seeded.ok()).toBeTruthy()
  await page.goto('/planning/plans/shared')
  await expect(page.getByRole('button', { name: '预览本机修改', exact: true })).toBeEnabled()
  const head = await (await page.request.get('/planning/api/plans/current')).json()
  await page.getByLabel('计划周次', { exact: true }).selectOption('15')
  await page.getByRole('button', { name: '2027-01-24 L 日内详情', exact: true }).click()
  const week = await prepareWeekNavigation(page, head, 'L')
  expect(week.from_week).toBe(1)
  expect(week.to_week).toBe(15)
  await expect(page.getByRole('button', { name: '2027-01-24 L 日内详情', exact: true })).toHaveCount(0)
  await navigateWeek(page, week)
  const day = await prepareDayNavigation(page, { time: head.time, plan: { segments: rows } }, rows[0])
  expect(day.from.segment).toBe('navigation-other')
  expect(day.to.segment).toBe('navigation-target')
  await navigateDay(page, day)
  const selected = page.getByLabel('选择生产段 navigation-target', { exact: true })
  await selected.check()
  await expect(selected).toBeChecked()
  await selected.uncheck()
  await expect(selected).not.toBeChecked()
  await expect(page.getByText('已选 0 段：', { exact: true })).toBeVisible()
})
