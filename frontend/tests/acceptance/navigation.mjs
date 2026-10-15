import { expect } from '@playwright/test'

export async function prepareWeekNavigation(page, head, lineCode, targetWeek = 15) {
  const currentWeek = Number(await page.getByLabel('计划周次', { exact: true }).inputValue())
  return { from_week: currentWeek, to_week: targetWeek, target_date: head.weeks[targetWeek - 1].dates[6], line_code: lineCode }
}

export async function navigateWeek(page, navigation) {
  await page.getByLabel('计划周次', { exact: true }).selectOption(String(navigation.to_week))
  await expect(page.getByRole('button', { name: `${navigation.target_date} ${navigation.line_code} 日内详情`, exact: true })).toBeVisible()
}

export async function prepareDayNavigation(page, source, row) {
  const targetWeek = Math.floor((Date.parse(row.date) - Date.parse(source.time.window_start)) / 604800000)
  const other = source.plan.segments.find(item => item.line_code === row.line_code && item.date !== row.date && Math.floor((Date.parse(item.date) - Date.parse(source.time.window_start)) / 604800000) === targetWeek)
  if (!other) throw new Error('A different same-week day is required for navigation timing.')
  await page.getByRole('button', { name: `${other.date} ${other.line_code} 日内详情`, exact: true }).click()
  await expect(page.getByLabel('选择生产段 ' + other.id, { exact: true })).toBeVisible()
  await expect(page.getByLabel('选择生产段 ' + row.id, { exact: true })).toHaveCount(0)
  return { from: { date: other.date, line_code: other.line_code, segment: other.id }, to: { date: row.date, line_code: row.line_code, segment: row.id } }
}

export async function navigateDay(page, navigation) {
  await page.getByRole('button', { name: `${navigation.to.date} ${navigation.to.line_code} 日内详情`, exact: true }).click()
  await expect(page.getByRole('heading', { name: `${navigation.to.date} · ${navigation.to.line_code} 日内详情`, exact: true })).toBeVisible()
  await expect(page.getByLabel('选择生产段 ' + navigation.to.segment, { exact: true })).toBeVisible()
}
