import { test as base, expect, type Page } from '@playwright/test'
import { openWorkspace, previewQuantity, previewBatch, undoLocalDraft, expectLocalActions, prepareComputation, submitComputation, viewCandidate, saveLocalDraft, verifyLocalSave, type UiTarget } from './plan-workflow.mjs'

declare global { interface Window { __acceptanceInputEpoch: number } }

const computation = { mode: 'all_replan', strategy: 'hybrid', time_limit_seconds: 1, max_candidates: 1,
  scope: { start_date: '2026-10-15', end_date: '2026-10-15', line_codes: ['L'], model_codes: ['M'] } }
const epoch = (page: Page) => page.evaluate(() => window.__acceptanceInputEpoch)
const headers = (page: Page) => ({ Origin: new URL(page.url()).origin, 'X-Auto-Plan-Request': '1' })
const row = { id: 'workflow-a', date: '2026-10-15', line_code: 'L', model_code: 'M', quantity: 600, hours: '15', order: 0 }

type Fixture = { ui: UiTarget; original: any; jobs: Set<string> }
const test = base.extend<{ workflow: Fixture }>({
  workflow: async ({ page }, use) => {
    base.skip(process.env.AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE !== '1', 'Requires the shared-plan memory adapter')
    await page.addInitScript(() => {
      for (const type of ['click', 'change']) window.addEventListener(type, () => {
        window.__acceptanceInputEpoch = performance.timeOrigin + performance.now()
      }, true)
    })
    await page.goto('/planning/login')
    await page.getByRole('textbox', { name: '公司账号', exact: true }).fill('SHARED_EDITOR')
    await page.getByLabel('公司密码', { exact: true }).fill('company-secret')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeVisible()
    const head = await (await page.request.get('/planning/api/plans/current')).json()
    const seeded = await page.request.post('/planning/api/plans/save', {
      headers: headers(page),
      data: { base_revision: head.revision, input_version: head.input_version, latest_adoption: head.latest_adoption,
        operation_id: crypto.randomUUID(), pending: true, operations: [{ kind: 'replace_plan', plan: { segments: [row] } }] },
    })
    expect(seeded.ok()).toBeTruthy()
    const original = await (await page.request.get('/planning/api/plans/current')).json()
    const ui = { page, appUrl: new URL('/planning', page.url()).href }, jobs = new Set<string>()
    await openWorkspace(ui)
    try { await use({ ui, original, jobs }) }
    finally {
      for (const id of jobs) {
        const get = async () => (await (await page.request.get('/planning/api/jobs/' + id)).json()).state
        if (['queued', 'running'].includes(await get())) {
          expect((await page.request.post('/planning/api/jobs/' + id + '/stop', { headers: headers(page), data: {} })).ok()).toBeTruthy()
        }
        await expect.poll(get).not.toMatch(/^(queued|running)$/)
      }
    }
  },
})

test('quantity preview keeps the original input evidence and settles complete undo without shared writes', async ({ page, workflow: { ui, original } }) => {
  let inputEpoch = 0, submitted: any, acknowledged: any
  const preview = await previewQuantity(ui, { source: { time: original.time }, row, quantity: 599 }, {
    afterInput: async () => { inputEpoch = await epoch(page) },
    submitted: request => { submitted = request.postDataJSON() },
    responded: attempt => { acknowledged = attempt.result },
  })
  expect(submitted.operations).toEqual([expect.objectContaining({ kind: 'change_quantity', quantity: 599 })])
  expect(acknowledged).toEqual(preview.result)
  expect(preview.command).toEqual(submitted)
  expect(preview.result.plan.segments).toEqual([expect.objectContaining({ id: row.id, quantity: 599 })])
  await expect(page.getByRole('region', { name: '产出需求与确认核算', exact: true })).toBeVisible()
  expect(inputEpoch).toBeGreaterThan(0)
  expect(await epoch(page)).toBeGreaterThan(inputEpoch)
  const unchanged = await (await page.request.get('/planning/api/plans/current')).json()
  expect(unchanged.plan).toEqual(original.plan)
  expect(unchanged.revision).toBe(original.revision)
  await expectLocalActions(ui, 1)
  const undone = await undoLocalDraft(ui)
  expect(undone.at(-1)?.result.plan).toEqual(original.plan)
  await expect(page.getByRole('region', { name: '产出需求与确认核算', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '产出需求与确认核算', exact: true })).toContainText('预计产出 600 片')
  await expectLocalActions(ui, 0)
})

test('a computation retains its first click and frozen source through workspace editing and candidate display', async ({ page, workflow: { ui, original, jobs } }) => {
  await prepareComputation(ui, computation)
  let submissionEpoch = 0
  const submitted = await submitComputation(ui, {
    afterInput: async () => { submissionEpoch = await epoch(page) },
    responded: attempt => { jobs.add(attempt.result.job_id) },
  })
  expect(submitted.command.request).toMatchObject(computation)
  const verified = await page.request.post('/planning/api/jobs/verify', { headers: headers(page), data: { submission_id: submitted.command.submission_id } })
  expect(await verified.json()).toMatchObject({ status: 'ACCEPTED', job_id: submitted.result.job_id })
  await previewQuantity(ui, { source: { time: original.time }, row, quantity: 598 })
  const restored = await undoLocalDraft(ui, { count: 1 })
  expect(restored[0].result.plan).toEqual(original.plan)
  const candidate = await viewCandidate(ui, { jobId: submitted.result.job_id, reload: true })
  expect(candidate.result.job_id).toBe(submitted.result.job_id)
  expect(candidate.result.candidate.snapshot.plan).toEqual(original.plan)
  expect(submissionEpoch).toBeLessThan(await epoch(page))
})

test('a batch preview clears the named days and leaves the complete unrelated row unchanged', async ({ page, workflow: { ui, original } }) => {
  const second = { ...row, id: 'workflow-second', date: '2026-10-16', quantity: 100 }
  const untouched = { ...row, id: 'workflow-untouched', date: '2026-10-17', quantity: 50 }
  const seeded = await page.request.post('/planning/api/plans/save', { headers: headers(page), data: {
    base_revision: original.revision, input_version: original.input_version, latest_adoption: original.latest_adoption,
    operation_id: crypto.randomUUID(), pending: true, operations: [{ kind: 'replace_plan', plan: { segments: [row, second, untouched] } }],
  } })
  expect(seeded.ok()).toBeTruthy()
  const source = await (await page.request.get('/planning/api/plans/current')).json()
  await openWorkspace(ui)
  const preview = await previewBatch(ui, { source, rows: [row, second] })
  expect(preview.command.operations).toEqual([expect.objectContaining({ kind: 'clear_segments', segment_ids: [row.id, second.id] })])
  expect(preview.result.plan.segments).toEqual([source.plan.segments[2]])
  const undone = await undoLocalDraft(ui, { count: 1 })
  expect(undone[0].result.plan).toEqual(source.plan)
})

test('a lost successful save reply stays read-only until its original UUID is verified', async ({ page, workflow: { ui, original } }) => {
  await previewQuantity(ui, { source: { time: original.time }, row, quantity: 599 })
  await page.route('**/api/plans/save', async route => {
    await route.fetch()
    await route.abort('failed')
  }, { times: 1 })
  let originalUuid: string | undefined
  const unknown = await saveLocalDraft(ui, { pending: true }, {
    submitted: request => { originalUuid = request.postDataJSON().operation_id },
  })
  expect(unknown.status).toBeNull()
  expect(originalUuid).toBeTruthy()
  const resolution = await verifyLocalSave(ui)
  expect(resolution.command).toEqual({ operation_id: originalUuid })
  expect(resolution.result).toMatchObject({ status: 'COMMITTED', revision: original.revision + 1 })
  await expectLocalActions(ui, 0)
})

test('an accepted job remains recoverable when response observation fails with its original error', async ({ page, workflow: { ui, jobs } }) => {
  await prepareComputation(ui, computation)
  const failure = new Error('Evidence sink is unavailable')
  let submissionId: string | undefined, jobId: string | undefined
  await expect(submitComputation(ui, {
    submitted: request => { submissionId = request.postDataJSON().submission_id },
    responded: attempt => { jobId = attempt.result.job_id; jobs.add(jobId!); throw failure },
  })).rejects.toBe(failure)
  expect(jobId).toBeTruthy()
  const verified = await page.request.post('/planning/api/jobs/verify', { headers: headers(page), data: { submission_id: submissionId } })
  expect(await verified.json()).toMatchObject({ status: 'ACCEPTED', job_id: jobId })
})
