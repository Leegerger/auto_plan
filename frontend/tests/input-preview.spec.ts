import { test, expect, type Page } from '@playwright/test'
import type { InputPreview, InputSnapshot } from '../src/inputFields'

const snapshot: InputSnapshot = {
  schema_version: 1, input_version: 1,
  masters: { recipes: [], models: [], lines: [] }, parameters: {}, calendar: {}, business: {},
}

async function openPreview(page: Page, preview: InputPreview) {
  await page.addInitScript(() => { window.__AUTO_PLAN_CONFIG__ = { basePath: '/planning' } })
  await page.route('**/api/ready', route => route.fulfill({ json: { status: 'ready' } }))
  await page.route('**/api/auth/me', route => route.fulfill({ json: { user_id: 'PREVIEW_EDITOR', roles: ['editor'] } }))
  await page.route('**/api/plans/current', route => route.fulfill({ json: { revision: 0, input_version: 1, latest_adoption: 0 } }))
  await page.route('**/api/inputs', route => route.fulfill({ json: snapshot }))
  await page.route('**/api/inputs/excel/preview?*', route => route.fulfill({ json: preview }))
  await page.goto('/planning/data/base')
  await expect(page.getByRole('heading', { name: '基础资料', exact: true })).toBeVisible()
  await page.getByLabel('导入标准 Excel 并预览', { exact: true }).setInputFiles({ name: 'inputs.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('mock workbook') })
  await expect(page.getByRole('heading', { name: '整批预览', exact: true })).toBeVisible()
}

test('整批预览每页显示 20 行并可查看后续资料', async ({ page }) => {
  await openPreview(page, {
    valid: true, base_version: 1, errors: [], snapshot,
    changes: { recipes: Array.from({ length: 45 }, (_, index) => ({ code: `R${index + 1}`, name: `Recipe ${index + 1}`, active: true })) },
  })

  const table = page.getByRole('table', { name: 'recipes 预览', exact: true })
  await expect(table.locator('tbody tr')).toHaveCount(20)
  await expect(table.getByRole('cell', { name: 'R1', exact: true })).toBeVisible()
  await page.getByRole('navigation', { name: '整批预览明细上方分页', exact: true }).getByRole('button', { name: '下一页', exact: true }).click()
  await expect(table.getByRole('cell', { name: 'R21', exact: true })).toBeVisible()
  await expect(table.locator('tbody tr')).toHaveCount(20)
  await expect(page.getByRole('navigation', { name: '整批预览明细下方分页', exact: true })).toContainText('第 21 至 40 笔，共 45 笔')
})

test('导入错误独立分页并保留工作表和原始来源行号', async ({ page }) => {
  await openPreview(page, {
    valid: false, base_version: 1, snapshot,
    errors: Array.from({ length: 45 }, (_, index) => ({ sheet: 'line_yields', row: 101 + index, field: 'percent', reason: `第 ${index + 1} 个良率错误` })),
    changes: { recipes: Array.from({ length: 45 }, (_, index) => ({ code: `R${index + 1}`, name: `Recipe ${index + 1}`, active: true })) },
  })

  const errors = page.getByRole('table', { name: '导入错误', exact: true })
  await expect(errors.locator('tbody tr')).toHaveCount(20)
  await expect(errors.locator('tbody tr').first()).toHaveText('线体良率101良率 (%)第 1 个良率错误')
  await page.getByRole('navigation', { name: '整批预览明细上方分页', exact: true }).getByRole('button', { name: '下一页', exact: true }).click()
  await page.getByRole('navigation', { name: '导入错误上方分页', exact: true }).getByRole('button', { name: '下一页', exact: true }).click()
  await expect(errors.locator('tbody tr').first()).toHaveText('线体良率121良率 (%)第 21 个良率错误')
  await expect(errors.locator('tbody tr')).toHaveCount(20)
  await expect(page.getByRole('table', { name: 'recipes 预览', exact: true }).getByRole('cell', { name: 'R21', exact: true })).toBeVisible()
  await page.getByRole('navigation', { name: '导入错误下方分页', exact: true }).getByRole('button', { name: '下一页', exact: true }).click()
  await expect(errors.locator('tbody tr')).toHaveCount(5)
  await expect(errors.locator('tbody tr').first()).toHaveText('线体良率141良率 (%)第 41 个良率错误')
  await expect(page.getByRole('button', { name: '确认提交整批', exact: true })).toBeDisabled()
})

test('预览摘要涵盖整批类别，切换类别和每页笔数后仍提交完整整批', async ({ page }) => {
  const changes = {
    recipes: Array.from({ length: 45 }, (_, index) => ({ code: `R${index + 1}`, name: `Recipe ${index + 1}`, active: true })),
    lines: Array.from({ length: 65 }, (_, index) => ({ code: `L${index + 1}`, name: `Line ${index + 1}`, active: true })),
    models: [],
  }
  await page.route('**/api/inputs/apply', route => route.fulfill({ json: { ...snapshot, input_version: 2 } }))
  await openPreview(page, { valid: true, base_version: 1, errors: [], snapshot, changes })

  const summary = page.getByRole('list', { name: '整批变更摘要', exact: true })
  await expect(summary.getByRole('listitem')).toHaveText(['Recipe (45 行)', '线体 (65 行)'])
  const top = page.getByRole('navigation', { name: '整批预览明细上方分页', exact: true })
  await top.getByRole('button', { name: '下一页', exact: true }).click()
  await page.getByLabel('预览资料类别', { exact: true }).selectOption('lines')
  const lines = page.getByRole('table', { name: 'lines 预览', exact: true })
  await expect(page.getByRole('table', { name: 'recipes 预览', exact: true })).toHaveCount(0)
  await expect(lines.getByRole('cell', { name: 'L1', exact: true })).toBeVisible()
  await expect(lines.locator('tbody tr')).toHaveCount(20)
  await top.getByRole('button', { name: '下一页', exact: true }).click()
  await top.getByText('20条/页', { exact: true }).click()
  const sizes = page.getByRole('listbox').getByRole('option')
  await expect(sizes).toHaveText(['20条/页', '50条/页', '100条/页'])
  await page.getByRole('listbox').getByRole('option', { name: '50条/页', exact: true }).click()
  await expect(lines.getByRole('cell', { name: 'L1', exact: true })).toBeVisible()
  await expect(lines.locator('tbody tr')).toHaveCount(50)
  await expect(top).toContainText('第 1 至 50 笔，共 65 笔')
  await top.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(lines.getByRole('cell', { name: 'L51', exact: true })).toBeVisible()

  const request = page.waitForRequest('**/api/inputs/apply')
  await page.getByRole('button', { name: '确认提交整批', exact: true }).click()
  const applied = (await request).postDataJSON()
  expect(applied.base_version).toBe(1)
  expect(applied.changes.recipes).toHaveLength(45)
  expect(applied.changes.recipes[44]).toEqual({ code: 'R45', name: 'Recipe 45', active: true })
  expect(applied.changes.lines).toHaveLength(65)
  expect(applied.changes.lines[64]).toEqual({ code: 'L65', name: 'Line 65', active: true })
  expect(applied.changes.models).toEqual([])
  await expect(page.getByText('已提交资料版本 2', { exact: true })).toBeVisible()
})
