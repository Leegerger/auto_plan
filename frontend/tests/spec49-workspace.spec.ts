import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { gzipSync } from 'node:zlib'
import type { DemandComparisonResult } from '../src/demandComparisonTypes'

const port = process.env.AUTO_PLAN_BROWSER_PORT ?? '8822'
const headers = { Origin: `https://localhost:${port}`, 'X-Auto-Plan-Request': '1' }
const lines = ['L', ...Array.from({ length: 17 }, (_, index) => `L${String(index + 2).padStart(2, '0')}`)]
type Segment = { id: string; date: string; line_code: string; model_code: string; quantity: number; hours: string; order: number }
type Head = { revision: number; input_version: number; latest_adoption: number; plan: { segments: Segment[] }; lineage: { demand_goals: Record<string, { requested_output: number; remaining_output_target: number }> } }

async function enter(page: Page) {
  await page.goto('/planning/login')
  await page.getByRole('textbox', { name: '公司账号' }).fill('SHARED_EDITOR')
  await page.getByLabel('公司密码', { exact: true }).fill('company-secret')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '排程', exact: true }).click()
}

for (const viewport of [{ width: 1280, height: 800 }, { width: 1440, height: 900 }]) {
  test(`representative workspace composes independent selection, maintenance and adoption at ${viewport.width}px`, async ({ page }, info: TestInfo) => {
    test.skip(process.env.AUTO_PLAN_BROWSER_WORKSPACE_FIXTURE !== '1', 'Requires the real 18-line, 40-model, 15-week representative fixture')
    test.setTimeout(180_000)
    await page.setViewportSize(viewport)
    const directory = `${process.env.AUTO_PLAN_BROWSER_EVIDENCE_DIR ?? '/tmp/auto-plan-spec49/browser62-evidence'}/${viewport.width}`
    await mkdir(directory, { recursive: true })
    const timings: { phase: string; milliseconds: number; bytes: number; status: number }[] = []
    const evidence = async (name: string, value: unknown) => writeFile(`${directory}/${name}.json`, `${JSON.stringify(value, null, 2)}\n`)
    async function observed<T>(phase: string, endpoint: string, action: () => Promise<unknown>, limit?: number): Promise<T> {
      const response = page.waitForResponse(row => row.url().endsWith(endpoint) && row.request().method() === 'POST', { timeout: 60_000 })
      void response.catch(() => undefined)
      const start = performance.now()
      await action()
      const answer = await response
      const milliseconds = performance.now() - start
      const bytes = await answer.body()
      timings.push({ phase, milliseconds, bytes: bytes.length, status: answer.status() })
      await evidence(`${phase}-request`, answer.request().postDataJSON())
      await writeFile(`${directory}/${phase}-response.json`, bytes)
      expect(answer.ok(), await answer.text()).toBe(true)
      if (limit && process.env.AUTO_PLAN_BROWSER_ACCEPTANCE_TIMING === '1') expect(milliseconds, phase).toBeLessThanOrEqual(limit)
      return answer.json() as Promise<T>
    }
    try {
      await enter(page)
      const inputs = await (await page.request.get('/planning/api/inputs')).json()
      // The sparse source attributes existing R production to earlier weeks. Explicit
      // shipping permissions make that source adoptable; P100 below needs none.
      const confirmations = inputs.business.demands.map((row: { model_code: string; week: string }) => ({
        ...row, kind: 'shipping', batch_id: 'SPEC49-SOURCE', status: 'allowed', date: '2027-01-24', quantity: 100,
      }))
      const precondition = { base_version: inputs.input_version, changes: { maintenance: [], confirmations } }
      const permissions = await page.request.post('/planning/api/inputs/apply', { headers, data: precondition })
      expect(permissions.ok(), await permissions.text()).toBe(true)
      await evidence('source-permissions-request', precondition)
      await evidence('source-permissions-response', await permissions.json())
      let source: Head = await (await page.request.get('/planning/api/plans/current')).json()
      if (source.revision !== 1) {
        const reset = { base_revision: source.revision, input_version: source.input_version, latest_adoption: source.latest_adoption,
          operation_id: crypto.randomUUID(), pending: true, operations: [{ kind: 'restore_history', source: { kind: 'revision', id: 1 } }] }
        const restored = await page.request.post('/planning/api/plans/save', { headers, data: reset })
        expect(restored.ok(), await restored.text()).toBe(true)
        await evidence('fixture-restore-request', reset)
        await evidence('fixture-restore-response', await restored.json())
      }
      await page.reload()
      source = await (await page.request.get('/planning/api/plans/current')).json()
      const frozenInputs = await (await page.request.get('/planning/api/inputs')).json()
      await writeFile(`${directory}/inputs.json.gz`, gzipSync(JSON.stringify(frozenInputs)))
      await evidence('source', source)
      await evidence('expected', { viewport, frozen_clock: '2026-10-14T00:00:00Z', lines: 18, models: 40, weeks: 15, segments: 270,
        source_revision: source.revision, source_input_version: source.input_version, source_latest_adoption: source.latest_adoption,
        goal_entry: Object.keys(source.lineage.demand_goals ?? {}).length ? 'Continue retained current goal after public history restore' : 'Create a new goal',
        selected_demand: { model_code: 'P', week: '2026-10-12', target_output: 100 },
        independent_candidates: 18, each_suggested_input: 100, each_scheduled_on_time_output: 100,
        selected_line: 'L02', selected_date: '2026-10-16', original_M_quantity: 20, changed_M_hours: '2',
        source_permissions: 'Explicit shipping permissions for existing source attribution, distinct from the selected current-week P100 action.' })
      expect(source.plan.segments).toHaveLength(270)
      await expect(page.getByLabel('筛选机种').locator('option')).toHaveCount(41)
      await expect(page.getByRole('table', { name: '十五周日期线体计划' }).locator('tbody tr')).toHaveCount(18)
      await expect(page.getByLabel('计划周次').locator('option')).toHaveCount(15)
      const matrix = page.getByRole('region', { name: '周矩阵滚动区域' })
      expect((await matrix.boundingBox())!.y).toBeLessThan(viewport.height - 40)
      await page.screenshot({ path: `${directory}/matrix-entry.png` })

      // Keep an ordinary edit selected before entering the independent comparison.
      await page.getByRole('button', { name: '2026-10-15 L 日内详情', exact: true }).click()
      await page.getByLabel('选择生产段 workspace-0-0').check()
      await page.getByLabel('调整范围起日', { exact: true }).fill('2026-10-15')
      await page.getByLabel('调整范围终日', { exact: true }).fill('2026-10-19')
      await page.getByLabel('调整后精确工时').fill('2')
      await observed('ordinary-edit-preview', '/plans/preview', () => page.getByRole('button', { name: '加入工时调整并预览', exact: true }).click(), 15_000)
      await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible()
      await page.getByRole('button', { name: '展开待处理需求', exact: true }).click()
      await page.getByRole('button', { name: '选择需求 P 2026-10-12', exact: true }).click()
      if (Object.keys(source.lineage.demand_goals ?? {}).length) {
        // Restoring the source keeps the durable goal. Continue that goal rather
        // than creating a duplicate when both viewports share one real server.
        await page.getByRole('button', { name: '查看补排目标', exact: true }).click()
        await page.getByRole('button', { name: '继续补排 P 2026-10-12', exact: true }).click()
      } else {
        await page.getByRole('button', { name: '按需补排', exact: true }).click()
      }
      await expect(page.getByLabel('补排合格产出目标', { exact: true })).toHaveValue('100')
      await page.getByLabel('补排投入起日', { exact: true }).fill('2026-10-16')
      await page.getByLabel('补排投入终日', { exact: true }).fill('2026-10-16')
      await page.getByLabel('补排线体', { exact: true }).selectOption('L02')
      await page.getByLabel('补排调整范围线体', { exact: true }).selectOption(lines)
      const first = await observed<DemandComparisonResult>('comparison-before-maintenance', '/plans/demand-comparison', () => page.getByRole('button', { name: '比较授权线体', exact: true }).click())
      expect(first.rows).toHaveLength(18)
      for (const row of first.rows) expect(row).toMatchObject({ suggested_input: 100, scheduled_on_time_output: 100, valid_on_time_output: 100, remaining_output_target: 0 })
      const comparison = page.getByRole('region', { name: '独立线体补排比较', exact: true })
      await expect(comparison).toContainText('各行的覆盖量和容量不能相加')
      await page.getByRole('button', { name: '查看 L02 完整安排', exact: true }).click()
      const complete = page.getByRole('region', { name: 'L02 线体完整安排', exact: true })
      await expect(complete).toContainText('100 片投入')
      await expect(complete).toContainText('100 片合格产出')
      await expect(complete.getByRole('table', { name: '建议逐日投入' })).toContainText('2026-10-16')
      await page.screenshot({ path: `${directory}/independent-selected.png` })
      expect((await (await page.request.get('/planning/api/plans/current')).json()).plan).toEqual(source.plan)

      await page.getByRole('button', { name: '关闭对象面板', exact: true }).click()
      await expect(page.getByRole('complementary', { name: '排程对象详情' })).toBeHidden()
      await page.getByRole('button', { name: '打开对象面板', exact: true }).click()
      await expect(page.getByLabel('选择生产段 workspace-0-0')).toBeChecked()
      await expect(page.getByLabel('调整后精确工时')).toHaveValue('2')
      await expect(complete).toBeVisible()
      await matrix.evaluate(element => { element.scrollLeft = 400; element.scrollTop = 200 })
      const scroll = await matrix.evaluate(element => ({ left: element.scrollLeft, top: element.scrollTop }))
      const navigation = page.getByRole('navigation', { name: '主要导航' })
      await navigation.getByRole('link', { name: '需求与资料', exact: true }).click()
      await page.getByRole('link', { name: '基础资料', exact: true }).click()
      await page.getByLabel('资料类别', { exact: true }).selectOption('maintenance')
      await page.getByLabel('粘贴表格', { exact: true }).fill('date\tline_code\thours\n2026-10-16\tL02\t1')
      await page.getByRole('button', { name: '预览粘贴内容', exact: true }).click()
      await expect(page.getByText('预览通过，尚未提交', { exact: true })).toBeVisible()
      await observed('maintenance-submit', '/inputs/apply', () => page.getByRole('button', { name: '确认提交整批', exact: true }).click(), 30_000)
      await navigation.getByRole('link', { name: '排程', exact: true }).click()
      await expect(comparison).toHaveCount(0)
      await expect(page.getByText('线体比较已失效，覆盖目标和日期已保留，请重新比较。', { exact: true })).toBeVisible()
      await expect(page.getByRole('region', { name: '计划状态' })).toContainText('待重新预览')
      await expect(page.getByRole('button', { name: '正式采用', exact: true })).toHaveCount(0)
      await expect(page.getByRole('region', { name: '所选需求摘要' })).toContainText('P · 需求周 2026-10-12')
      for (const [name, value] of [['补排合格产出目标', '100'], ['补排投入起日', '2026-10-16'], ['补排投入终日', '2026-10-16'], ['补排调整范围起日', '2026-10-15'], ['补排调整范围终日', '2026-10-19'], ['补排线体', 'L02']]) await expect(page.getByLabel(name, { exact: true })).toHaveValue(value)
      expect(await page.getByLabel('补排调整范围线体', { exact: true }).evaluate(element => Array.from((element as HTMLSelectElement).selectedOptions, option => option.value))).toEqual(lines)
      await expect(page.getByLabel('选择生产段 workspace-0-0')).toBeChecked()
      await expect(page.getByLabel('调整后精确工时')).toHaveValue('2')
      expect(await matrix.evaluate(element => ({ left: element.scrollLeft, top: element.scrollTop }))).toEqual(scroll)
      await page.screenshot({ path: `${directory}/maintenance-return.png` })
      await observed('latest-source-preview', '/plans/preview', () => page.getByRole('button', { name: '按最新资料重新预览', exact: true }).click(), 15_000)
      await page.getByRole('button', { name: '继续编辑', exact: true }).click()
      const refreshed = await observed<DemandComparisonResult>('comparison-after-maintenance', '/plans/demand-comparison', () => page.getByRole('button', { name: '比较授权线体', exact: true }).click())
      expect(refreshed.basis.input_version).toBeGreaterThan(first.basis.input_version)
      const selected = refreshed.rows.find(row => row.line_code === 'L02')!
      expect(selected).toMatchObject({ suggested_input: 100, scheduled_on_time_output: 100, valid_on_time_output: 100, remaining_output_target: 0 })
      await evidence('selected-complete-operation', selected.operation)
      await page.getByRole('button', { name: '查看 L02 完整安排', exact: true }).click()
      const acceptedRequest = viewport.width === 1280 ? page.waitForRequest(request => request.url().endsWith('/plans/preview') && request.method() === 'POST') : null
      await observed('selected-full-preview', '/plans/preview', () => complete.getByRole('button', { name: '选用完整安排并预览', exact: true }).click(), 15_000)
      if (acceptedRequest) expect((await acceptedRequest).postDataJSON().operations).toContainEqual(selected.operation)
      await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible()
      await page.getByRole('button', { name: '2026-10-16 L02 日内详情', exact: true }).click()
      await expect(page.getByRole('table', { name: '日内生产顺序' })).toContainText('P')
      await expect(page.getByRole('table', { name: '日内生产顺序' })).toContainText('100片')
      await page.locator('.plan-status-actions').getByRole('button', { name: /本机临时稿/ }).click()
      const queue = page.getByRole('region', { name: '本机完整动作队列' })
      await expect(queue.getByRole('checkbox')).toHaveCount(2)
      for (const checkbox of await queue.getByRole('checkbox').all()) await expect(checkbox).toBeChecked()
      await page.getByRole('button', { name: '继续编辑', exact: true }).click()

      // One desktop also proves that this composed two-action draft survives reload.
      if (viewport.width === 1280) {
        await page.reload()
        await page.locator('.plan-status-actions').getByRole('button', { name: /本机临时稿/ }).click()
        await page.getByRole('button', { name: '恢复本机临时稿', exact: true }).click()
        await expect(queue.getByRole('checkbox')).toHaveCount(2)
        for (const checkbox of await queue.getByRole('checkbox').all()) await expect(checkbox).toBeChecked()
        await observed('recovered-full-preview', '/plans/preview', () => page.getByRole('tabpanel', { name: '本机动作' }).getByRole('button', { name: '预览本机修改', exact: true }).click(), 15_000)
        await page.getByRole('button', { name: '继续编辑', exact: true }).click()
      }
      await observed('shared-save', '/plans/save', () => page.getByRole('region', { name: '计划状态' }).getByRole('button', { name: '共享保存', exact: true }).click(), 30_000)
      await expect(page.getByText(/已共享保存修订/)).toBeVisible()
      const saved: Head = await (await page.request.get('/planning/api/plans/current')).json()
      await evidence('saved', saved)
      expect(saved.plan.segments.find(row => row.id === 'workspace-0-0')).toMatchObject({ model_code: 'M', quantity: 20, hours: '2' })
      expect(saved.plan.segments.filter(row => row.model_code === 'P')).toEqual([expect.objectContaining({ date: '2026-10-16', line_code: 'L02', quantity: 100 })])
      expect(saved.plan.segments.filter(row => row.date > '2026-10-19')).toEqual(source.plan.segments.filter(row => row.date > '2026-10-19'))
      expect(Object.values(saved.lineage.demand_goals)).toEqual([expect.objectContaining({ requested_output: 100, remaining_output_target: 0 })])
      await observed('saved-current-preview', '/plans/preview', () => page.getByRole('button', { name: '预览当前计划', exact: true }).click(), 15_000)
      await page.getByRole('button', { name: '继续编辑', exact: true }).click()
      await page.getByRole('region', { name: '计划状态' }).getByRole('button', { name: '正式采用', exact: true }).click()
      await observed('formal-adopt', '/plans/adopt', () => page.getByRole('dialog', { name: '确认正式采用', exact: true }).getByRole('button', { name: '正式采用', exact: true }).click(), 30_000)
      await expect(page.getByText(/已正式采用/)).toBeVisible()
      const adopted: Head = await (await page.request.get('/planning/api/plans/current')).json()
      await evidence('adopted', adopted)
      expect(adopted.plan).toEqual(saved.plan)
      expect(adopted.latest_adoption).toBeGreaterThan(source.latest_adoption)
      await page.screenshot({ path: `${directory}/adopted.png` })

      if (viewport.width === 1280) {
        const historical = await (await page.request.get(`/planning/api/plans/revisions/${saved.revision}`)).json()
        await evidence('historical-saved-revision', historical)
        await navigation.getByRole('link', { name: '版本与采用', exact: true }).click()
        await page.getByLabel('选择历史版本', { exact: true }).selectOption(`revision:${saved.revision}`)
        await expect(page.getByRole('table', { name: '历史每日投入' })).toContainText('100')
        await observed('history-full-preview', '/plans/preview', () => page.getByRole('button', { name: '恢复为新草稿并预览', exact: true }).click(), 15_000)
        const afterRestore = await (await page.request.get('/planning/api/plans/current')).json()
        await evidence('shared-head-after-history-preview', afterRestore)
        expect(afterRestore.revision).toBe(adopted.revision)
        expect((await (await page.request.get(`/planning/api/plans/revisions/${saved.revision}`)).json()).snapshot).toEqual(historical.snapshot)
        await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible()
        await page.screenshot({ path: `${directory}/history-restored-preview.png` })
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    } finally {
      await evidence('timings', { timing_gates_enabled: process.env.AUTO_PLAN_BROWSER_ACCEPTANCE_TIMING === '1', comparison_has_no_new_SLA: true, timings })
      await info.attach('representative-timings', { body: JSON.stringify(timings), contentType: 'application/json' })
    }
  })
}
