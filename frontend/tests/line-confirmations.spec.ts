import { test, expect } from '@playwright/test'

test('batch site confirmation and reasoned correction use the shared preview and persistence flow', async ({ page }) => {
  test.skip(process.env.AUTO_PLAN_BROWSER_LINE_STATES_FIXTURE !== '1', 'Requires unconfirmed line-state fixture')
  await page.goto('/planning/login')
  await page.getByRole('textbox', { name: '公司账号' }).fill('SHARED_EDITOR')
  await page.getByLabel('公司密码', { exact: true }).fill('company-secret')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '排程', exact: true }).click()
  await page.getByRole('button',{name:'预览当前计划',exact:true}).click()
  await expect(page.getByRole('region',{name:'正式采用所需现场依据'})).toContainText('线体状态现场确认')
  await expect(page.getByRole('button',{name:'正式采用',exact:true})).toBeDisabled()
  await page.getByRole('button',{name:'继续编辑',exact:true}).click()
  const before = await (await page.request.get('/planning/api/plans/current')).json()
  await page.getByRole('button', { name: '线体状态', exact: true }).click()
  await page.getByLabel('确认线体 L', { exact: true }).check()
  await page.getByLabel('确认线体 B', { exact: true }).check()
  await page.getByLabel('批量 Recipe', { exact: true }).selectOption('R')
  await page.getByRole('button', { name: '应用到所选线体', exact: true }).click()
  await page.getByLabel('线体状态来源', { exact: true }).fill('现场点检')
  await page.getByRole('button', { name: '加入状态确认并预览', exact: true }).click()
  await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible()
  const unchanged = await (await page.request.get('/planning/api/plans/current')).json()
  expect(unchanged.revision).toBe(before.revision)
  expect(unchanged.context.line_states?.records ?? []).toEqual([])
  await page.getByRole('button', { name: '共享保存', exact: true }).click()
  await expect(page.getByText(/已共享保存修订/)).toBeVisible()
  const confirmed = await (await page.request.get('/planning/api/plans/current')).json()
  expect(confirmed.context.line_states.records).toHaveLength(2)
  expect(confirmed.context.line_states.records[0].effective_at).toBe('2026-10-12T08:00:00+08:00')
  expect(confirmed.context.line_states.records[0].recorded_at).toBe('2026-10-14T00:00:00Z')
  expect(confirmed.context.line_states.records.map((row: { actor: string }) => row.actor)).toEqual(['SHARED_EDITOR', 'SHARED_EDITOR'])
  const records = page.getByRole('table', { name: '现场线体状态记录' })
  await expect(records).toContainText('现场点检')
  await expect(records).toContainText('状态对应')
  await expect(records).toContainText('录入时间')
  await page.getByRole('button', { name: '更正线体 L 状态', exact: true }).click()
  await page.getByLabel('状态 Recipe L', { exact: true }).selectOption('S')
  await page.getByLabel('线体状态来源', { exact: true }).fill('重新点检')
  await page.getByRole('button', { name: '加入状态确认并预览', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: '更正确认状态必须填写原因' })).toBeVisible()
  await page.getByLabel('线体状态更正原因', { exact: true }).fill('现场记录更正')
  await page.getByRole('button', { name: '加入状态确认并预览', exact: true }).click()
  await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '正式采用', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '查看预览', exact: true }).click()
  await expect(page.getByRole('table', { name: '完整业务问题' })).toContainText('Recipe')
  await page.getByRole('tabpanel', { name: '变更预览' }).getByText('保存为待处理草稿', { exact: true }).click()
  await page.getByRole('button', { name: '共享保存', exact: true }).click()
  await expect(page.getByText(/已共享保存修订/)).toBeVisible()
  const corrected = await (await page.request.get('/planning/api/plans/current')).json()
  const record = corrected.context.line_states.records.find((row: { line_code: string }) => row.line_code === 'L')
  expect(record.recipe_code).toBe('S')
  expect(record.source).toBe('重新点检')
  expect(corrected.context.line_states.audit.at(-1).reason).toBe('现场记录更正')
  const old = await (await page.request.get(`/planning/api/plans/revisions/${confirmed.revision}`)).json()
  expect(old.snapshot.line_states.records.find((row: { line_code: string }) => row.line_code === 'L').recipe_code).toBe('R')
  await page.reload()
  await page.getByRole('button', { name: '线体状态', exact: true }).click()
  await expect(records).toContainText('重新点检')
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await page.getByRole('textbox', { name: '公司账号' }).fill('SHARED_VIEWER')
  await page.getByLabel('公司密码', { exact: true }).fill('company-secret')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '排程', exact: true }).click()
  await page.getByRole('button', { name: '线体状态', exact: true }).click()
  await expect(records).toContainText('重新点检')
  await expect(page.getByRole('button', { name: '加入状态确认并预览', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '更正线体 L 状态', exact: true })).toHaveCount(0)
})

test('retained uncertainty reaches line readiness and a different Recipe trial until current evidence resolves it', async ({ page }) => {
  test.skip(process.env.AUTO_PLAN_BROWSER_LINE_STATES_FIXTURE !== '1', 'Requires line-state fixture')
  await page.goto('/planning/login')
  await page.getByRole('textbox', { name: '公司账号' }).fill('SHARED_EDITOR')
  await page.getByLabel('公司密码', { exact: true }).fill('company-secret')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  const headers = { Origin: `https://localhost:${process.env.AUTO_PLAN_BROWSER_PORT ?? '8774'}`, 'X-Auto-Plan-Request': '1' }
  async function head() { return (await page.request.get('/planning/api/plans/current')).json() }
  async function save(operations: Record<string, unknown>[]) {
    const current = await head()
    const response = await page.request.post('/planning/api/plans/save', { headers, data: {
      base_revision: current.revision, input_version: current.input_version, latest_adoption: current.latest_adoption,
      operation_id: crypto.randomUUID(), pending: true, operations,
    } })
    expect(response.ok(), await response.text()).toBe(true)
  }
  const before = await head()
  const prior = before.context.line_states.records.find((row: { line_code: string; effective_at: string }) =>
    row.line_code === 'L' && Date.parse(row.effective_at) === Date.parse('2026-10-12T08:00:00+08:00'))
  const future = { id: 'review-future', date: '2026-10-15', line_code: 'L', order: 0, model_code: 'M', quantity: 100, hours: '3' }
  await save([
    { kind: 'confirm_line_states', reason: prior ? '准备历史现场依据' : null, rows: [{ line_code: 'L', effective_at: '2026-10-12T08:00:00+08:00',
      recipe_code: 'R', source: '历史现场点检', ...(prior ? { expected_record_id: prior.id } : {}) }] },
    { kind: 'replace_plan', plan: { segments: [{ ...future, model_code: 'P' }], switches: [{ id: 'elapsed-review', line_code: 'L', from_recipe: 'R', to_recipe: 'S',
      allocations: [{ date: '2026-10-13', order: 0, hours: '0.95' }] }] } },
  ])
  await save([{ kind: 'replace_plan', plan: { segments: [future] } }])
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '排程', exact: true }).click()
  const line = page.getByRole('rowheader', { name: 'L 一线', exact: true })
  await expect(line).toContainText('保留 R')
  await expect(line).toContainText('起点未知，首次切机尚未计入')
  await page.getByRole('button', { name: '2026-10-15 L 日内详情', exact: true }).click()
  const confirmation = page.getByRole('region', { name: '当前现场确认' })
  await expect(confirmation).toContainText('需重新核对')
  await expect(confirmation).not.toContainText('现场依据有效')
  const current = await head()
  const trial = await page.request.post('/planning/api/plans/preview', { headers, data: {
    base_revision: current.revision, input_version: current.input_version, latest_adoption: current.latest_adoption,
    operations: [{ kind: 'patch_plan', segments: [{ ...future, model_code: 'P' }] }],
  } })
  expect(trial.ok(), await trial.text()).toBe(true)
  const value = await trial.json()
  expect(value.evaluation.capacity_valid).toBe(true)
  expect(value.evaluation.executable).toBe(false)
  expect(value.evaluation.issues.some((issue: { code: string }) => issue.code === 'recipe_sequence')).toBe(false)
  await save([
    { kind: 'confirm_line_states', rows: [{ line_code: 'L', effective_at: '2026-10-14T08:00:00+08:00', recipe_code: 'S', source: '当前现场复核' }] },
    { kind: 'patch_plan', segments: [{ ...future, model_code: 'P' }] },
  ])
  await page.reload()
  await expect(confirmation).toContainText('Recipe S')
  await expect(confirmation).toContainText('现场依据有效')
  await expect(line).toContainText('同 Recipe 续做')
})
