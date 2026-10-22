/** Real browser workflows. Callers own timing policy, business checks and recovery. */
import { expect } from '@playwright/test'
import assert from 'node:assert/strict'

const timeout = 180000
const drawer = page => page.getByRole('dialog', { name: /^(本机临时稿|变更预览)$/ })

async function closeDraft(page) {
  if (await drawer(page).isVisible()) {
    await drawer(page).getByRole('button', { name: '继续编辑', exact: true }).click()
    await expect(drawer(page)).toBeHidden()
  }
}

async function actions(ui) {
  const { page } = ui
  await expect(page.getByRole('region', { name: '计划状态', exact: true })).toBeVisible({ timeout })
  if (!await drawer(page).isVisible()) await page.getByRole('button', { name: /^本机临时稿/ }).click()
  await drawer(page).getByRole('tab', { name: '本机动作', exact: true }).click()
  return drawer(page).getByRole('region', { name: '本机完整动作队列', exact: true })
}

export async function expectLocalActions(ui, count) {
  const region = await actions(ui)
  await expect(region.getByRole('checkbox')).toHaveCount(count, { timeout })
}

export async function undoLocalDraft(ui, { count } = {}, observation) {
  await workspace(ui)
  const region = await actions(ui)
  const before = await region.getByRole('checkbox').count()
  if (count !== undefined) assert.equal(before, count, 'Unexpected action count before settled undo.')
  const attempts = []
  for (let remaining = before; remaining > 0; remaining--) {
    const attempt = await exchange(ui, '/plans/preview', region.getByRole('button', { name: '撤销最后完整动作', exact: true }), observation)
    assert.equal(attempt.status, 200, 'Undo preview failed.')
    await workspace(ui)
    await expect(region.getByRole('checkbox')).toHaveCount(remaining - 1)
    if (remaining === 1) await expect(region.getByRole('button', { name: '撤销最后完整动作', exact: true })).toBeDisabled()
    else await expect(region.getByRole('button', { name: '撤销最后完整动作', exact: true })).toBeEnabled()
    attempts.push(attempt)
  }
  if (attempts.length) await finishPreview(ui, attempts.at(-1))
  return attempts
}

async function workspace(ui) {
  const { page, appUrl } = ui
  if (new URL(page.url()).pathname !== new URL(appUrl + '/schedule').pathname) {
    await closeDraft(page)
    await page.getByRole('link', { name: '排程', exact: true }).click()
  }
  await expect(page.getByRole('heading', { name: '计划工作区', exact: true })).toBeVisible({ timeout })
  await expect(page.getByLabel('手动操作', { exact: true })).toBeEnabled({ timeout })
}

export async function openWorkspace(ui) {
  await ui.page.goto(ui.appUrl + '/schedule', { timeout })
  await workspace(ui)
  await expectLocalActions(ui, 0)
  await closeDraft(ui.page)
}

export async function selectRows(ui, { source, rows, scope }) {
  const { page } = ui
  await workspace(ui)
  await closeDraft(page)
  const only = rows.length === 1 ? rows[0] : null
  const selected = only && await page.getByText(`已选 1 段：${only.date}/${only.line_code}/${only.model_code} ${only.quantity}片`, { exact: true }).isVisible()
    && await page.getByLabel('选择生产段 ' + only.id, { exact: true }).count() === 1
    && await page.getByLabel('选择生产段 ' + only.id, { exact: true }).isChecked()
  if (!selected) await page.getByRole('button', { name: '取消生产段选择', exact: true }).click()
  for (const row of rows) {
    const week = Math.floor((Date.parse(row.date) - Date.parse(source.time.window_start)) / 604800000) + 1
    if (await page.getByLabel('计划周次', { exact: true }).inputValue() !== String(week)) await page.getByLabel('计划周次', { exact: true }).selectOption(String(week))
    if (!await page.getByRole('heading', { name: `${row.date} · ${row.line_code} 日内详情`, exact: true }).isVisible()) {
      await page.getByRole('button', { name: `${row.date} ${row.line_code} 日内详情`, exact: true }).click()
    }
    await page.getByLabel('选择生产段 ' + row.id, { exact: true }).check()
  }
  scope ??= { start_date: rows.map(row => row.date).sort()[0], end_date: rows.map(row => row.date).sort().at(-1),
    line_codes: [...new Set(rows.map(row => row.line_code))], model_codes: [...new Set(rows.map(row => row.model_code))] }
  for (const [label, value] of [['调整范围起日', scope.start_date], ['调整范围终日', scope.end_date]]) await page.getByLabel(label, { exact: true }).fill(value)
  await page.getByLabel('调整范围线体', { exact: true }).selectOption(scope.line_codes)
  await page.getByLabel('调整范围机种', { exact: true }).selectOption(scope.model_codes)
  await page.getByLabel('顺延截止日', { exact: true }).fill(scope.end_date)
}

async function exchange(ui, path, control, observation = {}, confirm) {
  const { page, appUrl } = ui
  const url = appUrl + '/api' + path
  const request = page.waitForRequest(request => request.url() === url && request.method() === 'POST', { timeout })
  request.catch(() => {})
  await observation.beforeInput?.()
  await control.click()
  // Capture before any subsequent drawer, confirmation or route click rewrites the epoch.
  await observation.afterInput?.()
  await confirm?.()
  const sent = await request
  await observation.submitted?.(sent)
  const step = (phase, work) => observation.step ? observation.step(phase, work) : work()
  const received = await step('headers', () => sent.response())
  const bytes = received ? await step('body', () => received.body()) : null
  const result = bytes ? await step('parse', () => JSON.parse(bytes)) : null
  const attempt = { request: sent, response: received, command: sent.postDataJSON(), result, bytes, status: received?.status() ?? null }
  await observation.responded?.(attempt)
  return attempt
}

async function jobs(ui) {
  const { page, appUrl } = ui
  await closeDraft(page)
  if (new URL(page.url()).pathname !== new URL(appUrl + '/schedule/jobs').pathname) {
    await page.getByRole('link', { name: '计算任务', exact: true }).click()
  }
  await expect(page.getByRole('heading', { name: '计算任务', exact: true, level: 1 })).toBeVisible({ timeout })
  await expect(page.getByLabel('计算模式', { exact: true })).toBeEnabled({ timeout })
}

export async function prepareComputation(ui, request) {
  const { page } = ui
  await jobs(ui)
  for (const [label, value] of [['计算模式', request.mode], ['求解策略', request.strategy]]) await page.getByLabel(label, { exact: true }).selectOption(value)
  for (const [label, value] of [['计算范围起日', request.scope.start_date], ['计算范围终日', request.scope.end_date], ['计算时限', String(request.time_limit_seconds)], ['候选数量', String(request.max_candidates)]]) await page.getByLabel(label, { exact: true }).fill(value)
  await page.getByLabel('计算线体', { exact: true }).selectOption(request.scope.line_codes)
  await page.getByLabel('计算机种', { exact: true }).selectOption(request.scope.model_codes ?? [])
}

export async function submitComputation(ui, observation = {}) {
  const { page } = ui
  const all = await page.getByLabel('计算模式', { exact: true }).inputValue() === 'all_replan'
  const attempt = await exchange(ui, '/jobs', page.getByRole('button', { name: '提交计算任务', exact: true }), observation, all ? async () => {
    await page.getByRole('dialog', { name: '确认全部重排', exact: true }).getByRole('button', { name: '提交全部重排', exact: true }).click()
  } : undefined)
  if (attempt.status === 200) {
    await expect(page.getByRole('region', { name: '计算任务', exact: true })).toContainText('计算任务已接收', { timeout })
    await observation.feedback?.()
  } else assert.equal(attempt.status, 409, 'Computation was not accepted.')
  return attempt
}

export async function hasCandidate(ui, jobId) {
  await jobs(ui)
  return ui.page.getByRole('list', { name: '持久计算任务', exact: true }).locator('li').filter({ hasText: jobId }).getByRole('button', { name: '查看候选内容', exact: true }).first().isVisible()
}

export async function viewCandidate(ui, { jobId, submitted, reload = false }, observation = {}) {
  const { page } = ui
  if (reload) {
    await expectLocalActions(ui, 0)
    await page.goto(ui.appUrl + '/schedule/jobs', { timeout })
  }
  await jobs(ui)
  const item = page.getByRole('list', { name: '持久计算任务', exact: true }).locator('li').filter({ hasText: jobId })
  const button = item.getByRole('button', { name: '查看候选内容', exact: true }).first()
  await expect(button).toBeVisible({ timeout })
  const pending = page.waitForResponse(response => response.url().startsWith(ui.appUrl + `/api/jobs/${jobId}/candidates/`), { timeout })
  pending.catch(() => {})
  await button.click()
  const response = await pending, bytes = await response.body(), result = JSON.parse(bytes)
  const attempt = { request: response.request(), response, command: null, bytes, result, status: response.status() }
  await observation.responded?.(attempt)
  assert.equal(attempt.status, 200, 'Candidate content request failed.')
  assert.equal(result.job_id, jobId, 'Displayed candidate belongs to another job.')
  const region = page.getByRole('region', { name: '候选内容', exact: true })
  await expect(region.getByRole('heading', { name: '候选 ' + result.candidate_id, exact: true })).toBeVisible()
  const pins = submitted ?? result.proposal
  await expect(region).toContainText(`原共享修订 ${pins.base_revision} · 输入版本 ${pins.input_version} · 最近采用版 ${pins.latest_adoption}`)
  await expect(region).toContainText(result.candidate.eligibility === 'executable' ? '可执行候选' : '待业务确认')
  await region.locator('summary').filter({ hasText: '查看原始候选业务检查' }).click()
  await assertOutput(ui, result.candidate.evaluation, region)
  return attempt
}

export async function assertDaily(ui, row, quantity) {
  const tr = ui.page.getByLabel('选择生产段 ' + row.id, { exact: true }).locator('xpath=ancestor::tr')
  await expect(tr).toContainText(row.model_code)
  await expect(tr.locator('td').nth(2)).toHaveText(`${quantity}片`)
}

export async function assertOutput(ui, evaluation, parent) {
  const { page } = ui
  if (!parent) {
    if (!await drawer(page).isVisible()) await page.getByRole('button', { name: '查看预览', exact: true }).click()
    await drawer(page).getByRole('tab', { name: '变更预览', exact: true }).click()
    parent = drawer(page)
  }
  const result = parent.getByRole('region', { name: '产出需求与确认核算', exact: true })
  await expect(result).toBeVisible({ timeout })
  await expect(result.locator('p').filter({ hasText: /(?:预计产出|当前已算产出)/ })).toContainText(new RegExp(`预计产出\\s*${evaluation.output_totals.expected}\\s*片；窗口外\\s*${evaluation.output_totals.outside_window}\\s*片；未分配\\s*${evaluation.output_totals.unallocated}\\s*片。`))
  const first = evaluation.weekly_results[0]
  if (first) {
    const row = result.getByRole('table', { name: '周需求覆盖', exact: true }).locator('tbody tr').first()
    for (const [index, value] of [[0, first.model_code], [1, first.week], [2, first.demand], [3, first.on_time], [6, first.gap]]) await expect(row.locator('td').nth(index)).toHaveText(String(value))
  }
}

export async function previewQuantity(ui, { source, row, quantity }, observation) {
  await selectRows(ui, { source, rows: [row] })
  await assertDaily(ui, row, row.quantity)
  await ui.page.getByLabel('改量后总量', { exact: true }).fill(String(quantity))
  const attempt = await exchange(ui, '/plans/preview', ui.page.getByRole('button', { name: '预览完整改量顺延', exact: true }), observation)
  if (attempt.status === 200) await assertDaily(ui, row, quantity)
  await finishPreview(ui, attempt)
  return attempt
}

export async function previewDraft(ui, { latest = false } = {}, observation) {
  const { page } = ui
  await workspace(ui)
  await actions(ui)
  const control = latest ? page.getByRole('button', { name: '按最新资料重新预览', exact: true })
    : drawer(page).getByRole('button', { name: '预览本机修改', exact: true })
  const attempt = await exchange(ui, '/plans/preview', control, observation)
  await finishPreview(ui, attempt)
  return attempt
}

async function finishPreview(ui, attempt) {
  if (attempt.status === 409) {
    await expect(ui.page.getByRole('region', { name: '共享修订冲突', exact: true })).toBeVisible({ timeout })
  } else {
    assert.equal(attempt.status, 200, 'Plan preview failed.')
    await expect(ui.page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible({ timeout })
    await assertOutput(ui, attempt.result.evaluation)
  }
}

export async function previewBatch(ui, { source, rows, scope }, observation) {
  await selectRows(ui, { source, rows, scope })
  await ui.page.getByLabel('手动操作', { exact: true }).selectOption('clear_segments')
  const attempt = await exchange(ui, '/plans/preview', ui.page.getByRole('button', { name: '加入完整动作并预览', exact: true }), observation)
  await finishPreview(ui, attempt)
  return attempt
}

export async function undoLastAction(ui, observation) {
  await workspace(ui)
  const region = await actions(ui)
  const count = await region.getByRole('checkbox').count()
  const attempt = await exchange(ui, '/plans/preview', region.getByRole('button', { name: '撤销最后完整动作', exact: true }), observation)
  await expect(region.getByRole('checkbox')).toHaveCount(count - 1)
  await finishPreview(ui, attempt)
  return attempt
}

export async function waitForEditing(ui) { await workspace(ui) }

async function expectSaveUnknown(ui) {
  const { page } = ui
  await expect(page.getByText('前次保存结果未知，当前只读。请先核实，避免重复保存或采用。', { exact: true })).toBeVisible({ timeout: 60000 })
  // Unknown outcomes are read-only even when the preview tab is hidden.
  await expect(page.getByLabel('手动操作', { exact: true })).toBeDisabled()
  await drawer(page).getByRole('tab', { name: '本机动作', exact: true }).click()
  for (const name of ['预览本机修改', '撤销最后完整动作']) await expect(drawer(page).getByRole('button', { name, exact: true })).toBeDisabled()
  await drawer(page).getByRole('tab', { name: '变更预览', exact: true }).click()
  for (const name of ['共享保存', '正式采用']) await expect(drawer(page).getByRole('button', { name, exact: true })).toBeDisabled()
  await drawer(page).getByRole('tab', { name: '本机动作', exact: true }).click()
}

export async function saveLocalDraft(ui, { pending } = {}, observation) {
  const { page } = ui
  if (pending !== undefined) {
    if (!await drawer(page).isVisible()) await page.getByRole('button', { name: '查看预览', exact: true }).click()
    await drawer(page).getByRole('tab', { name: '变更预览', exact: true }).click()
    const panel = drawer(page).getByRole('tabpanel', { name: '变更预览', exact: true })
    if (await panel.getByRole('checkbox', { name: '保存为待处理草稿', exact: true }).isChecked() !== pending) {
      await panel.getByText('保存为待处理草稿', { exact: true }).click()
    }
  }
  const parent = await drawer(page).isVisible() ? drawer(page) : page.getByRole('region', { name: '计划状态', exact: true })
  const attempt = await exchange(ui, '/plans/save', parent.getByRole('button', { name: '共享保存', exact: true }), observation)
  if (attempt.status === null || attempt.status >= 500) await expectSaveUnknown(ui)
  else if (attempt.status === 409) await expect(page.getByRole('region', { name: '共享修订冲突', exact: true })).toBeVisible()
  else {
    assert.equal(attempt.status, 200, 'Shared save failed.')
    await expect(page.getByText(`已共享保存修订 ${attempt.result.revision}，最近采用版未改变`, { exact: true })).toBeVisible({ timeout })
    await expect(drawer(page)).toBeHidden()
    await expectLocalActions(ui, 0)
  }
  return attempt
}

export async function verifyLocalSave(ui, observation) {
  const { page } = ui
  const parent = await drawer(page).isVisible() ? drawer(page) : page.getByRole('region', { name: '计划状态', exact: true })
  const attempt = await exchange(ui, '/plans/verify', parent.getByRole('button', { name: '核实前次操作', exact: true }), observation)
  assert.equal(attempt.status, 200, 'Original save verification failed.')
  await expect(page.getByText(attempt.result.status === 'COMMITTED' ? `已核实前次操作成功，共享修订 ${attempt.result.revision}` : '已核实前次操作作废，原操作不会再写入。请核对保留的修改后重新预览，再由你发起保存。', { exact: true })).toBeVisible({ timeout })
  await expect(drawer(page)).toBeHidden()
  await workspace(ui)
  return attempt
}

export async function reviewComputationSource(ui) {
  await jobs(ui)
  const { page } = ui
  const pending = page.waitForResponse(response => response.url() === ui.appUrl + '/api/jobs/source' && response.request().method() === 'GET', { timeout })
  pending.catch(() => {})
  await page.getByRole('button', { name: '重新读取计算来源', exact: true }).click()
  const response = await pending
  assert.equal(response.status(), 200, 'Complete public computation source read failed.')
  // Do not hold the native review behind an extra full-body parse.
  const body = response.body().then(bytes => ({ bytes }), error => ({ error }))
  const review = page.getByRole('region', { name: '计算来源核对', exact: true })
  await expect(review.getByRole('checkbox', { name: '已核对本次计算来源', exact: true })).toBeEnabled({ timeout })
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))))
  await review.getByRole('checkbox', { name: '已核对本次计算来源', exact: true }).check()
  return { status: response.status(), body }
}
