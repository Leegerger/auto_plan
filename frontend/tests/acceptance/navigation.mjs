import { expect } from '@playwright/test'

export async function prepareWeekNavigation(page, head, lineCode, targetWeek = 15) {
  const fromWeek = targetWeek === 1 ? 2 : 1
  const fromDate = head.weeks[fromWeek - 1]?.dates[0]
  const targetDate = head.weeks[targetWeek - 1]?.dates[6]
  if (!fromDate || !targetDate || fromDate === targetDate) throw new Error('Two distinct dated weeks are required.')
  await page.getByLabel('计划周次', { exact: true }).selectOption(String(fromWeek))
  await expect(page.getByLabel('计划周次', { exact: true })).toHaveValue(String(fromWeek))
  await expect(page.getByRole('button', { name: `${fromDate} ${lineCode} 日内详情`, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: `${targetDate} ${lineCode} 日内详情`, exact: true })).toHaveCount(0)
  return { from_week: fromWeek, to_week: targetWeek, from_date: fromDate, target_date: targetDate, line_code: lineCode }
}

export async function navigateWeek(page, navigation) {
  await page.getByLabel('计划周次', { exact: true }).selectOption(String(navigation.to_week))
  await expect(page.getByLabel('计划周次', { exact: true })).toHaveValue(String(navigation.to_week))
  await expect(page.getByRole('button', { name: `${navigation.target_date} ${navigation.line_code} 日内详情`, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: `${navigation.from_date} ${navigation.line_code} 日内详情`, exact: true })).toHaveCount(0)
}

export async function prepareDayNavigation(page, source, row) {
  const targetWeek = Math.floor((Date.parse(row.date) - Date.parse(source.time.window_start)) / 604800000)
  const other = source.plan.segments.find(item => item.line_code === row.line_code && item.date !== row.date && Math.floor((Date.parse(item.date) - Date.parse(source.time.window_start)) / 604800000) === targetWeek)
  if (!other) throw new Error('A different same-week day is required for navigation timing.')
  await page.getByRole('button', { name: `${other.date} ${other.line_code} 日内详情`, exact: true }).click()
  await expect(page.getByRole('heading', { name: `${other.date} · ${other.line_code} 日内详情`, exact: true })).toBeVisible()
  await expect(page.getByLabel('选择生产段 ' + other.id, { exact: true })).toBeVisible()
  await expect(page.getByLabel('选择生产段 ' + row.id, { exact: true })).toHaveCount(0)
  return { from: { date: other.date, line_code: other.line_code, segment: other.id }, to: { date: row.date, line_code: row.line_code, segment: row.id } }
}

export async function navigateDay(page, navigation) {
  await page.getByRole('button', { name: `${navigation.to.date} ${navigation.to.line_code} 日内详情`, exact: true }).click()
  await expect(page.getByRole('heading', { name: `${navigation.to.date} · ${navigation.to.line_code} 日内详情`, exact: true })).toBeVisible()
  await expect(page.getByLabel('选择生产段 ' + navigation.to.segment, { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: `${navigation.from.date} · ${navigation.from.line_code} 日内详情`, exact: true })).toHaveCount(0)
  await expect(page.getByLabel('选择生产段 ' + navigation.from.segment, { exact: true })).toHaveCount(0)
}

export async function assertSelected(page, row) {
  await expect(page.getByLabel('选择生产段 ' + row.id, { exact: true })).toBeChecked()
  await expect(page.getByText(`已选 1 段：${row.date}/${row.line_code}/${row.model_code} ${row.quantity}片`, { exact: true })).toBeVisible()
  const form = page.getByRole('form', { name: '改量与完整顺延链', exact: true })
  await expect(form).toContainText(`当前段：${row.date} / ${row.line_code} / ${row.model_code}，原总量 ${row.quantity} 片。`)
  await expect(page.getByRole('button', { name: '预览完整改量顺延', exact: true })).toBeEnabled()
}

export async function unselectSegment(page, row) {
  await page.getByLabel('选择生产段 ' + row.id, { exact: true }).uncheck()
  await expect(page.getByLabel('选择生产段 ' + row.id, { exact: true })).not.toBeChecked()
  await expect(page.getByText('已选 0 段：', { exact: true })).toBeVisible()
  await expect(page.getByRole('form', { name: '改量与完整顺延链', exact: true })).toContainText('在日内详情中选择一段生产安排。其他已选段须先取消选择。')
  await expect(page.getByRole('button', { name: '预览完整改量顺延', exact: true })).toBeDisabled()
}
