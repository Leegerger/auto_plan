import { test, expect, type Page } from '@playwright/test'
import type { InputSnapshot, InputRow } from '../src/inputFields'

async function openInputs(page: Page, count = 45) {
  const snapshot: InputSnapshot = {
    schema_version: 1, input_version: 1,
    masters: { recipes: Array.from({ length: count }, (_, index) => ({
      code: `R${String(index + 1).padStart(3, '0')}`, name: `Recipe ${index + 1}`, active: true,
    })), models: [], lines: [], capabilities: [] },
    parameters: {}, calendar: {}, business: {},
  }
  const submitted: Record<string, InputRow[]>[] = []
  await page.addInitScript(() => { window.__AUTO_PLAN_CONFIG__ = { basePath: '/planning' } })
  await page.route('**/api/ready', route => route.fulfill({ json: { status: 'ready' } }))
  await page.route('**/api/auth/me', route => route.fulfill({ json: { user_id: 'PAGING_EDITOR', roles: ['editor'] } }))
  await page.route('**/api/plans/current', route => route.fulfill({ json: { revision: 0, input_version: 1, latest_adoption: 0 } }))
  await page.route('**/api/inputs', route => route.fulfill({ json: snapshot }))
  await page.route('**/api/inputs/preview', route => {
    const payload = route.request().postDataJSON()
    submitted.push(payload.changes)
    return route.fulfill({ json: { valid: true, base_version: 1, errors: [], changes: payload.changes, snapshot: null } })
  })
  await page.route('**/api/inputs/apply', route => {
    const payload = route.request().postDataJSON()
    submitted.push(payload.changes)
    snapshot.masters.recipes = payload.changes.recipes
    snapshot.input_version++
    return route.fulfill({ json: snapshot })
  })
  await page.goto('/planning/data/base')
  await expect(page.getByRole('heading', { name: '基础资料', exact: true })).toBeVisible()
  return { snapshot, submitted }
}

test('base-data pages preserve edits and submit the complete category after searching across pages', async ({ page }) => {
  const { submitted } = await openInputs(page)
  const table = page.getByRole('table', { name: 'Recipe', exact: true })
  await expect(table.locator('tbody tr')).toHaveCount(20)
  const top = page.getByRole('navigation', { name: '基础资料上方分页', exact: true })
  await expect(top).toContainText('第 1 至 20 笔，共 45 笔')
  await expect(page.getByRole('navigation', { name: '基础资料下方分页', exact: true })).toBeVisible()
  await page.getByLabel('名称 第1行', { exact: true }).fill('Changed on page one')
  await top.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.getByLabel('编码 第21行', { exact: true })).toBeDisabled()
  await page.getByLabel('名称 第21行', { exact: true }).fill('Changed on page two')
  await top.getByRole('button', { name: '上一页', exact: true }).click()
  await expect(page.getByLabel('名称 第1行', { exact: true })).toHaveValue('Changed on page one')

  await page.getByLabel('搜索资料', { exact: true }).fill('R045')
  await expect(table.locator('tbody tr')).toHaveCount(1)
  await expect(page.getByLabel('编码 第45行', { exact: true })).toHaveValue('R045')
  await expect(page.getByLabel('编码 第45行', { exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '预览表格变更', exact: true }).click()
  await expect(page.getByText('预览通过，尚未提交', { exact: true })).toBeVisible()
  expect(submitted[0]?.recipes).toHaveLength(45)
  expect(submitted[0]?.recipes?.[0]?.name).toBe('Changed on page one')
  expect(submitted[0]?.recipes?.[20]?.name).toBe('Changed on page two')
  await page.getByRole('button', { name: '确认提交整批', exact: true }).click()
  await expect(page.getByText('已提交资料版本 2', { exact: true })).toBeVisible()
  expect(submitted[1]?.recipes).toHaveLength(45)
  expect(submitted[1]?.recipes?.[20]?.name).toBe('Changed on page two')
})

test('adding from search reveals the new row and withdrawing the last page returns to existing records', async ({ page }) => {
  await openInputs(page, 40)
  const table = page.getByRole('table', { name: 'Recipe', exact: true })
  const query = page.getByLabel('搜索资料', { exact: true })
  await query.fill('R040')
  await expect(table.locator('tbody tr')).toHaveCount(1)
  await page.getByRole('button', { name: '新增一行', exact: true }).click()
  await expect(query).toHaveValue('')
  await expect(table.locator('tbody tr')).toHaveCount(1)
  await expect(page.getByLabel('编码 第41行', { exact: true })).toBeEnabled()
  await page.getByLabel('编码 第41行', { exact: true }).fill('NEW')
  await page.getByLabel('名称 第41行', { exact: true }).fill('New Recipe')
  await page.getByRole('button', { name: '预览表格变更', exact: true }).click()
  await expect(page.getByText('预览通过，尚未提交', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '撤回新增', exact: true }).click()
  await expect(table.locator('tbody tr')).toHaveCount(20)
  await expect(page.getByLabel('编码 第21行', { exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '确认提交整批', exact: true })).toBeDisabled()
  await expect(page.getByRole('navigation', { name: '基础资料上方分页', exact: true })).toContainText('第 21 至 40 笔，共 40 笔')
  await query.fill('nothing matches')
  await expect(table.locator('tbody tr')).toHaveCount(0)
  await expect(page.getByText('没有符合搜索条件的资料。', { exact: true })).toBeVisible()
  await query.clear()
  await expect(page.getByLabel('编码 第1行', { exact: true })).toBeVisible()
})

test('unsaved base-data edits can cancel category changes, reloads and navigation', async ({ page }) => {
  await openInputs(page)
  await page.getByLabel('名称 第1行', { exact: true }).fill('Keep my changes')
  const confirmation = page.getByRole('dialog', { name: '舍弃未提交资料变更？', exact: true })
  await page.getByLabel('资料类别', { exact: true }).selectOption('lines')
  await expect(confirmation).toBeVisible()
  await confirmation.getByRole('button', { name: '继续编辑', exact: true }).click()
  await expect(page.getByLabel('资料类别', { exact: true })).toHaveValue('recipes')
  await expect(page.getByLabel('名称 第1行', { exact: true })).toHaveValue('Keep my changes')
  await page.getByRole('button', { name: '重新读取资料', exact: true }).click()
  await confirmation.getByRole('button', { name: '继续编辑', exact: true }).click()
  await expect(page.getByLabel('名称 第1行', { exact: true })).toHaveValue('Keep my changes')
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '排程', exact: true }).click()
  await confirmation.getByRole('button', { name: '继续编辑', exact: true }).click()
  await expect(page).toHaveURL(/\/data\/base$/)
  await page.getByRole('button', { name: '线体', exact: true }).click()
  await confirmation.getByRole('button', { name: '舍弃变更', exact: true }).click()
  await expect(page.getByRole('heading', { name: '线体', exact: true })).toBeVisible()
  await page.getByLabel('资料类别', { exact: true }).selectOption('recipes')
  await expect(confirmation).toBeHidden()
  await expect(page.getByLabel('名称 第1行', { exact: true })).toHaveValue('Recipe 1')

  await page.getByLabel('粘贴表格', { exact: true }).fill('code\tname\tactive\nNEW\tPending\ttrue')
  await page.getByRole('button', { name: '重新读取资料', exact: true }).click()
  await confirmation.getByRole('button', { name: '继续编辑', exact: true }).click()
  await expect(page.getByLabel('粘贴表格', { exact: true })).toHaveValue('code\tname\tactive\nNEW\tPending\ttrue')
  await page.getByRole('button', { name: '重新读取资料', exact: true }).click()
  await confirmation.getByRole('button', { name: '舍弃变更', exact: true }).click()
  await expect(page.getByLabel('粘贴表格', { exact: true })).toHaveValue('')
})

test('logging out asks before discarding base-data edits and leaves the session active on cancel', async ({ page }) => {
  await openInputs(page)
  let logouts = 0
  await page.route('**/api/auth/logout', route => {
    logouts++
    return route.fulfill({ json: { status: 'ok' } })
  })
  await page.getByLabel('名称 第1行', { exact: true }).fill('Pending logout')
  const confirmation = page.getByRole('dialog', { name: '舍弃未提交资料变更？', exact: true })
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await expect(confirmation).toBeVisible()
  expect(logouts).toBe(0)
  await confirmation.getByRole('button', { name: '继续编辑', exact: true }).click()
  await expect(page.getByLabel('名称 第1行', { exact: true })).toHaveValue('Pending logout')
  expect(logouts).toBe(0)
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await confirmation.getByRole('button', { name: '舍弃变更', exact: true }).click()
  await expect(page).toHaveURL(/\/login$/)
  expect(logouts).toBe(1)
})

test('page size controls stay in sync and search resets to the first page of the full category', async ({ page }) => {
  await openInputs(page, 125)
  const table = page.getByRole('table', { name: 'Recipe', exact: true })
  const top = page.getByRole('navigation', { name: '基础资料上方分页', exact: true })
  const bottom = page.getByRole('navigation', { name: '基础资料下方分页', exact: true })
  await top.getByRole('button', { name: '下一页', exact: true }).click()
  await top.getByText('20条/页', { exact: true }).click()
  await expect(page.getByRole('listbox').getByRole('option')).toHaveText(['20条/页', '50条/页', '100条/页'])
  await page.getByRole('option', { name: '50条/页', exact: true }).click()
  await expect(page.getByRole('listbox')).toBeHidden()
  await expect(table.locator('tbody tr')).toHaveCount(50)
  await expect(bottom).toContainText('第 1 至 50 笔，共 125 笔')
  await bottom.getByText('50条/页', { exact: true }).click()
  await page.getByRole('option', { name: '100条/页', exact: true }).click()
  await expect(page.getByRole('listbox')).toBeHidden()
  await expect(table.locator('tbody tr')).toHaveCount(100)
  await top.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(table.locator('tbody tr')).toHaveCount(25)
  await expect(bottom).toContainText('第 101 至 125 笔，共 125 笔')
  await page.getByLabel('搜索资料', { exact: true }).fill('R125')
  await expect(table.locator('tbody tr')).toHaveCount(1)
  await expect(top).toContainText('第 1 至 1 笔，共 1 笔')
  await expect(page.getByLabel('编码 第125行', { exact: true })).toHaveValue('R125')
  await page.getByLabel('搜索资料', { exact: true }).clear()
  await expect(page.getByLabel('编码 第1行', { exact: true })).toBeVisible()
})

test('restored values do not prompt, while browser refresh warns before discarding edits', async ({ page }) => {
  await openInputs(page)
  await page.getByLabel('名称 第1行', { exact: true }).fill('Changed temporarily')
  await page.getByLabel('名称 第1行', { exact: true }).fill('Recipe 1')
  await page.getByLabel('资料类别', { exact: true }).selectOption('lines')
  await expect(page.getByRole('heading', { name: '线体', exact: true })).toBeVisible()
  await page.getByLabel('资料类别', { exact: true }).selectOption('recipes')
  await page.getByLabel('名称 第1行', { exact: true }).click()
  await page.getByLabel('名称 第1行', { exact: true }).fill('Pending browser refresh')
  const dialog = page.waitForEvent('dialog')
  const reload = page.reload()
  const warning = await dialog
  expect(warning.type()).toBe('beforeunload')
  await warning.accept()
  await reload
  await expect(page.getByLabel('名称 第1行', { exact: true })).toHaveValue('Recipe 1')
})
