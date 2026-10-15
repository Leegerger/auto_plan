/** External Chromium acceptance runner. Never starts an adapter server or stores login secrets. */
import { chromium, expect } from '@playwright/test'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gunzipSync } from 'node:zlib'
import { createHash, randomUUID } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { performance } from 'node:perf_hooks'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const flags = Object.fromEntries(process.argv.slice(2).map((arg, i, all) => arg.startsWith('--') ? [arg.slice(2), all[i + 1]] : null).filter(Boolean))
if (!flags.config || !flags.output) throw new Error('Provide --config and a new --output directory.')
const config = JSON.parse(readFileSync(flags.config, 'utf8'))
const origin = new URL(config.origin)
if (origin.protocol !== 'https:' || origin.pathname !== '/' || origin.username || origin.password) throw new Error('An HTTPS origin without credentials is required.')
if (!['PLAN_TEST', 'adapter'].includes(config.target?.kind) || config.target?.disposable !== true || config.target?.production !== false) throw new Error('The selected disposable test target must be explicit.')
if (config.target.kind === 'PLAN_TEST' && !/^AP_TEST_[A-F0-9]{6}$/.test(config.target.namespace)) throw new Error('An authorized disposable PLAN_TEST namespace is required.')
if (!config.base_path?.startsWith('/') || config.base_path.endsWith('/')) throw new Error('Provide the exact retained application prefix.')
const mode = flags.mode ?? 'solo'
if (!['solo', 'five_automated_editors_one_computation'].includes(mode)) throw new Error('Unknown load mode.')
const operations = (flags.operations ?? 'selection,week,day,single_cell,batch_preview,shared_save').split(',')
const importOnly = operations.length === 1 && operations[0] === 'import_validation'
if (operations.includes('import_validation') && !importOnly) throw new Error('Initial import validation needs a separate fresh-target run.')
const fixtures = resolve(root, config.fixtures ?? 'docs/acceptance/frozen')
const manifestBytes = readFileSync(resolve(fixtures, 'manifest.json'))
const manifest = JSON.parse(manifestBytes)
const scenario = manifest.scenarios.find(row => row.id === config.scenario)
if (!scenario) throw new Error('Unknown frozen scenario.')
function asset(pointer) {
  const bytes = gunzipSync(readFileSync(resolve(fixtures, pointer.path)))
  if (createHash('sha256').update(bytes).digest('hex') !== pointer.sha256) throw new Error('Frozen asset hash mismatch.')
  return JSON.parse(bytes)
}
const source = asset(scenario.source), action = asset(scenario.action)
const output = resolve(flags.output)
mkdirSync(output, { recursive: false })
const evidence = { schema_version: 1, kind: config.target.kind === 'adapter' ? 'external browser with explicitly configured adapters' : 'external browser to operator-attested disposable PLAN_TEST',
  run_config_sha256: createHash('sha256').update(readFileSync(flags.config)).digest('hex'), runner_sha256: createHash('sha256').update(readFileSync(fileURLToPath(import.meta.url))).digest('hex'),
  mode, scenario: scenario.id, fixture_sha256: scenario.source.sha256, reference_sha256: scenario.reference.sha256,
  manifest_sha256: createHash('sha256').update(manifestBytes).digest('hex'), reference_confirmation: manifest.reference_confirmation,
  environment: config.environment, target: config.target, origin: config.origin, base_path: config.base_path,
  reset_policy: 'Each repeat restores its local action queue; confirmed saves retain the unchanged source quantities and baseline. Browser and server caches are retained. First repeat is included.',
  editor_schedule: 'Each of four background editor contexts repeatedly previews quantity 99, previews original quantity 100, then saves the complete pair. Saves have zero net quantity change and must acknowledge one revision. The primary context is editor five and owns the single computation.',
  repeats: [], editor_actions: [], request_spans: [], acceptance: 'not_run',
  real_user_acceptance: { status: 'not_run', reason: 'Automated contexts do not establish five humans, twenty real accounts, real company authentication or user-confirmed reference quality.' } }
function persist() { writeFileSync(resolve(output, 'report.json'), JSON.stringify(evidence, null, 2) + '\n') }
const credentials = JSON.parse(readFileSync(0, 'utf8'))
const accounts = credentials.accounts
const count = mode === 'solo' ? 1 : 5
if (!Array.isArray(accounts) || accounts.length !== count) throw new Error('Provide exactly one or five authorized test accounts through private stdin.')
if (new Set(accounts.map(row => row.username.trim().toUpperCase())).size !== count) throw new Error('The five editor identities must be distinct.')
let browser, stopped = false
const contexts = [], pages = []
const headers = { Origin: config.origin, 'X-Auto-Plan-Request': '1' }
const api = path => config.origin + config.base_path + '/api' + path
async function json(page, path, data) {
  const response = data === undefined ? await page.request.get(api(path)) : await page.request.post(api(path), { headers, data })
  if (!response.ok()) throw new Error(`Public request returned HTTP ${response.status()}`)
  return response.json()
}
async function inputTime(page) { return await page.evaluate(() => window.__acceptanceInputEpoch) - performance.timeOrigin }
async function settle(page) { await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))) }
async function shared(page) {
  await page.goto(config.origin + config.base_path + '/plans/shared')
  await expect(page.getByLabel('计划周次', { exact: true })).toBeVisible({ timeout: 30000 })
  await expect(page.getByRole('button', { name: '预览本机修改', exact: true })).toBeEnabled({ timeout: 30000 })
}
async function selectRow(page, row) {
  const week = Math.floor((Date.parse(row.date) - Date.parse(source.time.window_start)) / 604800000) + 1
  await page.getByLabel('计划周次', { exact: true }).selectOption(String(week))
  await page.getByRole('button', { name: `${row.date} ${row.line_code} 日内详情`, exact: true }).click()
  await page.getByLabel('选择生产段 ' + row.id, { exact: true }).check()
  await page.getByLabel('调整范围起日', { exact: true }).fill(row.date)
  await page.getByLabel('调整范围终日', { exact: true }).fill(row.date)
  await page.getByLabel('调整范围线体', { exact: true }).selectOption([row.line_code])
  await page.getByLabel('调整范围机种', { exact: true }).selectOption([row.model_code])
  await page.getByLabel('顺延截止日', { exact: true }).fill(row.date)
}
async function change(page, quantity) {
  await page.getByLabel('改量后总量', { exact: true }).fill(String(quantity))
  const response = page.waitForResponse(row => row.url().endsWith('/api/plans/preview') && row.request().method() === 'POST', { timeout: 180000 })
  await page.getByRole('button', { name: '预览完整改量顺延', exact: true }).click()
  const received = await response
  if (!received.ok()) throw new Error('Quantity preview failed.')
  const result = await received.json()
  await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible({ timeout: 180000 })
  await expect(page.getByRole('table', { name: '日内生产顺序', exact: true })).toContainText(`${quantity}片`)
  await settle(page)
  return result
}
async function save(page, before) {
  before ??= await json(page, '/plans/current')
  const response = page.waitForResponse(row => row.url().endsWith('/api/plans/save'), { timeout: 180000 })
  await page.getByRole('button', { name: '共享保存', exact: true }).click()
  const received = await response
  if (!received.ok()) throw new Error('Save was not acknowledged.')
  const result = await received.json()
  await expect(page.getByText(/已共享保存修订/)).toBeVisible({ timeout: 180000 })
  await settle(page)
  if (result.status !== 'COMMITTED' || result.revision <= before.revision || result.latest_adoption !== before.latest_adoption) throw new Error('Save did not confirm a new shared revision with the preserved baseline.')
  return { ...result, operation_id: received.request().postDataJSON().operation_id }
}
function check(kind, evaluation, candidate, stem, observedSource) {
  writeFileSync(resolve(output, stem + '-evaluation.json'), JSON.stringify(evaluation))
  if (candidate) writeFileSync(resolve(output, stem + '-candidate.json'), JSON.stringify(candidate))
  const args = [resolve(root, 'scripts/acceptance/check_result.py'), '--fixtures', fixtures, '--scenario', scenario.id,
    '--evaluation', resolve(output, stem + '-evaluation.json'), '--kind', kind]
  if (candidate) args.push('--candidate', resolve(output, stem + '-candidate.json'))
  if (observedSource) { writeFileSync(resolve(output, stem + '-snapshot.json'), JSON.stringify(observedSource)); args.push('--observed-source', resolve(output, stem + '-snapshot.json')) }
  const result = spawnSync(config.python ?? 'python', args, { cwd: root, encoding: 'utf8' })
  if (result.status !== 0) throw new Error('Exact reference, protection or objective comparison failed.')
  return JSON.parse(result.stdout)
}
async function compute(page, measuredMode, stem) {
  if (measuredMode !== action.request.mode) throw new Error('Measured computation mode differs from the frozen action.')
  await page.getByLabel('计算模式', { exact: true }).selectOption(measuredMode)
  await page.getByLabel('计算范围起日', { exact: true }).fill(action.request.scope.start_date)
  await page.getByLabel('计算范围终日', { exact: true }).fill(action.request.scope.end_date)
  await page.getByLabel('计算线体', { exact: true }).selectOption(action.request.scope.line_codes)
  await page.getByLabel('计算机种', { exact: true }).selectOption(action.request.scope.model_codes ?? [])
  await page.getByLabel('计算时限', { exact: true }).fill(String(action.request.time_limit_seconds))
  const response = page.waitForResponse(row => row.url().endsWith('/api/jobs') && row.request().method() === 'POST')
  await page.getByRole('button', { name: '提交计算任务', exact: true }).click()
  const clicked = await inputTime(page)
  const received = await response
  if (!received.ok()) throw new Error('Computation was not accepted.')
  const submitted = await received.json()
  await expect(page.getByRole('region', { name: '计算任务', exact: true })).toContainText('计算任务已接收')
  const feedback = performance.now()
  const item = page.getByRole('list', { name: '持久计算任务', exact: true }).locator('li').filter({ hasText: submitted.job_id })
  await expect(item.getByRole('button', { name: '查看候选内容', exact: true }).first()).toBeVisible({ timeout: 180000 })
  await item.getByRole('button', { name: '查看候选内容', exact: true }).first().click()
  await expect(page.getByRole('region', { name: '候选内容', exact: true })).toContainText('可执行候选', { timeout: 180000 })
  await page.getByRole('region', { name: '候选内容', exact: true }).locator('summary').filter({ hasText: '查看原始候选业务检查' }).click()
  await expect(page.getByRole('region', { name: '候选内容', exact: true }).getByRole('table').first()).toBeVisible({ timeout: 180000 })
  await settle(page)
  const rendered = performance.now()
  const job = await json(page, '/jobs/' + submitted.job_id)
  const ref = job.candidates[0]
  const view = await json(page, `/jobs/${submitted.job_id}/candidates/${ref.candidate_id}`)
  if (view.candidate.source_revision !== job.source_revision || view.candidate.input_version !== config.bindings.input_version || view.candidate.latest_adoption !== config.bindings.latest_adoption) throw new Error('Candidate source tokens differ.')
  const evaluation = await json(page, '/plans/evaluate', { snapshot: { ...source, plan: view.candidate.plan } })
  const comparison = check('candidate', evaluation, view.candidate.plan, stem)
  return { clicked, rendered, feedback_ms: feedback, job_timestamps: Object.fromEntries(Object.entries(job).filter(([key]) => key.endsWith('_at'))), job_id: submitted.job_id, candidate_id: ref.candidate_id, objective: comparison.objective,
    evaluation_check_seconds: (performance.now() - rendered) / 1000, stage_bounds: view.candidate.stage_bounds }
}
async function editorLoop(page, row, index) {
  while (!stopped) {
    const entry = { editor: index, started_ms: performance.now(), phase: 'shared_page' }
    try {
      await shared(page); entry.phase = 'select_segment'; await selectRow(page, row)
      entry.phase = 'preview_decrease'; await change(page, row.quantity - 1)
      entry.phase = 'preview_restore'; await change(page, row.quantity)
      entry.phase = 'shared_save'; const result = await save(page)
      const current = await json(page, '/plans/current')
      if (current.plan.segments.find(item => item.id === row.id)?.quantity !== row.quantity) throw new Error('Load edit failed to restore source quantity.')
      entry.status = 'pass'; entry.committed_revision = result.revision
    } catch (error) { entry.status = 'fail'; entry.reason = error.name; stopped = true }
    entry.finished_ms = performance.now(); evidence.editor_actions.push(entry); persist()
  }
}
try {
  if (importOnly && mode !== 'solo') throw new Error('A fresh initial-import target has no shared state for concurrent editors; this load observation is not_run.')
  browser = await chromium.launch({ headless: true })
  evidence.client = { chromium: browser.version(), node: process.version, platform: process.platform }
  evidence.phase = 'login_and_shared_page'; persist()
  for (let index = 0; index < count; index++) {
    const context = await browser.newContext({ ignoreHTTPSErrors: config.allow_test_certificate === true })
    contexts.push(context)
    await context.addInitScript(() => { for (const type of ['click', 'change']) window.addEventListener(type, () => { window.__acceptanceInputEpoch = performance.timeOrigin + performance.now() }, true) })
    const page = await context.newPage(); pages.push(page)
    page.on('response', response => {
      const url = new URL(response.url())
      if (url.pathname.startsWith(config.base_path + '/api/') && !url.pathname.includes('/auth/')) evidence.request_spans.push({ editor: index, path: url.pathname, status: response.status(), received_ms: performance.now(), request_started_epoch_ms: response.request().timing().startTime })
    })
    await page.goto(config.origin + config.base_path + '/login')
    await page.getByRole('textbox', { name: '公司账号', exact: true }).fill(accounts[index].username)
    await page.getByLabel('公司密码', { exact: true }).fill(accounts[index].password)
    evidence.phase = `login_acknowledgment_editor_${index}`; persist()
    const loginResponse = page.waitForResponse(response => response.url() === api('/auth/login') && response.request().method() === 'POST')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    if (!(await loginResponse).ok()) throw new Error('Login was not acknowledged.')
    accounts[index].password = ''
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeVisible({ timeout: 30000 })
    evidence.phase = `shared_page_editor_${index}`; persist()
    await shared(page)
  }
  evidence.phase = 'frozen_source_preflight'; persist()
  const head = await json(pages[0], '/plans/current')
  evidence.initial_tokens = { source_revision: head.revision, input_version: head.input_version, latest_adoption: head.latest_adoption }
  if (!importOnly && JSON.stringify(evidence.initial_tokens) !== JSON.stringify(config.bindings)) throw new Error('Prepared shared tokens differ from frozen run configuration.')
  if (head.evaluation.time.as_of !== source.time.as_of) throw new Error('The prepared environment clock differs from the frozen source.')
  const inputs = await json(pages[0], '/inputs?version=' + head.input_version)
  const adopted = head.latest_adoption ? await json(pages[0], '/plans/adoptions/' + head.latest_adoption) : null
  const observedSource = { time: head.time, inputs, plan: head.plan, execution: head.context.execution, locks: head.context.locks, reset: head.context.reset, preferences: head.context.preferences, baseline: adopted ? { version: 1, plan: adopted.snapshot.plan, coverage: adopted.coverage } : null }
  if (importOnly) { if (head.revision !== 0 || head.latest_adoption !== 0) throw new Error('Initial import validation requires an empty shared plan.') }
  else check('source', head.evaluation, head.plan, 'source-preflight', observedSource)
  const row = source.plan.segments.find(item => item.id === config.primary_segment)
  if (!row) throw new Error('Primary segment is absent from the frozen source.')
  const loads = mode === 'solo' ? [] : (config.load_segments ?? []).map(id => source.plan.segments.find(item => item.id === id))
  if (mode !== 'solo' && (loads.length !== 4 || loads.some(item => !item))) throw new Error('Four explicit disjoint load segments are required.')
  if (new Set([row, ...loads].map(item => item.model_code)).size !== count || new Set(loads.map(item => item.line_code)).size !== loads.length) throw new Error('Load edits require disjoint models and lines.')
  const loadPromises = loads.map((item, i) => editorLoop(pages[i + 1], item, i + 1))
  for (const operation of operations) {
    if (!(operation in manifest.gates_seconds)) throw new Error('Unknown measured operation.')
    for (let repeat = 1; repeat <= 5; repeat++) {
      evidence.phase = `${operation}_repeat_${repeat}`; persist()
      const page = pages[0], entry = { operation, repeat, first: repeat === 1, gate_seconds: manifest.gates_seconds[operation], status: 'fail', phase: 'setup_shared_page' }
      let loadJob
      try {
        if (stopped) throw new Error('An editor load action failed.')
        if (!importOnly) { await shared(page); entry.phase = 'setup_selected_segment'; await selectRow(page, row) }
        if (operation === 'day') {
          const targetWeek = Math.floor((Date.parse(row.date) - Date.parse(source.time.window_start)) / 604800000)
          const other = source.plan.segments.find(item => item.line_code === row.line_code && item.date !== row.date && Math.floor((Date.parse(item.date) - Date.parse(source.time.window_start)) / 604800000) === targetWeek)
          if (!other) throw new Error('A different same-week day is required for navigation timing.')
          await page.getByRole('button', { name: `${other.date} ${other.line_code} 日内详情`, exact: true }).click()
          await expect(page.getByLabel('选择生产段 ' + other.id, { exact: true })).toBeVisible()
          await expect(page.getByLabel('选择生产段 ' + row.id, { exact: true })).toHaveCount(0)
          entry.navigation_from = { date: other.date, line_code: other.line_code, segment: other.id }
          entry.navigation_to = { date: row.date, line_code: row.line_code, segment: row.id }
        }
        if (mode !== 'solo' && !['local', 'new_week', 'all_replan'].includes(operation)) {
          entry.phase = 'setup_running_computation'
          const current = await json(page, '/plans/current')
          const request = { ...action.request, time_limit_seconds: 90 }
          const submitted = await json(page, '/jobs', { submission_id: randomUUID(), base_revision: current.revision, input_version: current.input_version, latest_adoption: current.latest_adoption, request })
          loadJob = submitted.job_id
          await expect.poll(async () => (await json(page, '/jobs/' + loadJob)).state, { timeout: 30000 }).toBe('running')
        }
        let saveBefore
        if (operation === 'batch_preview') {
          if (scenario.kind !== 'loose') throw new Error('This fixed batch preview uses the loose source family.')
          await page.getByRole('button', { name: '取消生产段选择', exact: true }).click()
          const batch = ['M00', 'M02'].map(model => source.plan.segments.find(item => item.date === '2027-01-24' && item.model_code === model))
          for (const item of batch) await selectRow(page, item)
          await page.getByLabel('调整范围线体', { exact: true }).selectOption([...new Set(batch.map(item => item.line_code))])
          await page.getByLabel('调整范围机种', { exact: true }).selectOption(batch.map(item => item.model_code))
          await page.getByLabel('手动操作', { exact: true }).selectOption('clear_segments')
        }
        if (operation === 'shared_save') { await change(page, row.quantity); saveBefore = await json(page, '/plans/current') }
        entry.phase = 'measured_action'
        entry.clicked_ms = performance.now()
        if (operation === 'selection') {
          const control = page.getByLabel('选择生产段 ' + row.id, { exact: true })
          await control.uncheck(); await expect(control).not.toBeChecked()
        } else if (operation === 'week') {
          await page.getByLabel('计划周次', { exact: true }).selectOption('15')
          await expect(page.getByRole('table', { name: '十五周日期线体计划', exact: true })).toContainText(head.weeks[14].dates[6])
        } else if (operation === 'day') {
          await page.getByRole('button', { name: `${row.date} ${row.line_code} 日内详情`, exact: true }).click()
          await expect(page.getByRole('heading', { name: `${row.date} · ${row.line_code} 日内详情`, exact: true })).toBeVisible()
          await expect(page.getByLabel('选择生产段 ' + row.id, { exact: true })).toBeVisible()
        } else if (operation === 'single_cell') {
          const result = await change(page, row.quantity - 1)
          if (!result.evaluation.computation_complete || !result.evaluation.capacity_valid) throw new Error('Preview is incomplete or invalid.')
          const observed = result.plan.segments.find(item => item.id === row.id)
          if (observed?.quantity !== (row.quantity - 1)) throw new Error('Displayed quantity differs from the action.')
          entry.observed_totals = result.evaluation.output_totals
        } else if (operation === 'batch_preview') {
          const response = page.waitForResponse(item => item.url().endsWith('/api/plans/preview'), { timeout: 180000 })
          await page.getByRole('button', { name: '加入完整动作并预览', exact: true }).click()
          const received = await response
          if (!received.ok()) throw new Error('Batch preview failed.')
          const result = await received.json()
          await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible({ timeout: 180000 })
          if (!result.evaluation.capacity_valid || !result.evaluation.computation_complete || result.evaluation.output_totals.expected !== 419800 || result.evaluation.output_totals.gap !== 200) throw new Error('Two-cell batch quantity reference differs.')
          entry.observed_totals = result.evaluation.output_totals
        } else if (operation === 'shared_save') {
          entry.committed = await save(page, saveBefore)
        } else if (operation === 'import_validation') {
          await page.goto(config.origin + config.base_path + '/initialize')
          const workbook = resolve(config.workbook)
          entry.workbook_sha256 = createHash('sha256').update(readFileSync(workbook)).digest('hex')
          entry.clicked_ms = performance.now()
          const response = page.waitForResponse(item => item.url().includes('/api/initialization/excel/preview'))
          await page.getByLabel('上传标准工作簿并预览', { exact: true }).setInputFiles(workbook)
          const checked = await response
          if (!checked.ok()) throw new Error('Import validation failed.')
          const prepared = await checked.json()
          if (!prepared.valid) throw new Error('Import is not valid.')
          await expect(page.getByText('预览通过，尚未写入。', { exact: true })).toBeVisible({ timeout: 180000 })
          await settle(page); entry.rendered = performance.now()
          entry.import_observed_totals = prepared.evaluation?.output_totals
          const after = await json(page, '/plans/current')
          if (after.revision !== 0 || after.input_version !== head.input_version) throw new Error('Import preview wrote shared state.')
        } else {
          Object.assign(entry, await compute(page, operation, `${operation}-${repeat}`))
          entry.clicked_ms = entry.clicked
        }
        if (!['local', 'new_week', 'all_replan'].includes(operation)) entry.clicked_ms = await inputTime(page)
        entry.clicked_epoch_ms = entry.clicked_ms + performance.timeOrigin
        await settle(page)
        entry.full_render_ms = entry.rendered ?? performance.now()
        entry.feedback_ms ??= entry.full_render_ms
        entry.seconds = (entry.full_render_ms - entry.clicked_ms) / 1000
        if (!Number.isFinite(entry.seconds) || entry.seconds < 0) throw new Error('Browser input timestamp is missing.')
        entry.status = entry.seconds <= entry.gate_seconds ? 'pass' : 'fail'
        if (loadJob && (await json(page, '/jobs/' + loadJob)).state !== 'running') { entry.status = 'fail'; entry.reason = 'Computation did not remain active through the complete measured interval.' }
        await page.screenshot({ path: resolve(output, `${operation}-${repeat}.png`), fullPage: true })
      } catch (error) { entry.reason = error.name; entry.seconds = entry.clicked_ms === undefined ? null : (performance.now() - entry.clicked_ms) / 1000; entry.measurement_started = entry.clicked_ms !== undefined }
      finally {
        if (loadJob) {
          await json(page, '/jobs/' + loadJob + '/stop', {}).catch(() => {})
          await expect.poll(async () => (await json(page, '/jobs/' + loadJob)).state, { timeout: 30000 }).not.toBe('running').catch(() => {})
        }
        const undo = page.getByRole('button', { name: '撤销最后完整动作', exact: true })
        for (let remaining = 0; !importOnly && remaining < 4 && await undo.isEnabled().catch(() => false); remaining++) await undo.click()
        evidence.repeats.push(entry); persist()
        process.stdout.write(JSON.stringify({ operation, repeat, seconds: entry.seconds, status: entry.status }) + '\n')
      }
    }
  }
  stopped = true
  await Promise.all(loadPromises)
  for (const entry of evidence.repeats) {
    if (mode !== 'solo') {
      entry.active_editor_contexts = [1, 2, 3, 4].filter(index => {
        const intervals = evidence.editor_actions.filter(row => row.editor === index && row.started_ms <= entry.full_render_ms && row.finished_ms >= entry.clicked_ms).sort((a, b) => a.started_ms - b.started_ms)
        let through = entry.clicked_ms
        for (const interval of intervals) { if (interval.started_ms > through + 100) return false; through = Math.max(through, interval.finished_ms) }
        return through >= entry.full_render_ms
      }).length + 1
      if (entry.active_editor_contexts !== 5) { entry.status = 'fail'; entry.load_reason = 'Five editor action intervals did not overlap the complete measurement.' }
    }
  }
  evidence.automated_observation = evidence.repeats.every(row => row.status === 'pass') && evidence.editor_actions.every(row => row.status === 'pass') ? 'pass' : 'fail'
  evidence.acceptance = 'not_run'
} catch (error) { evidence.preflight = { status: 'fail', reason: error.name }; process.exitCode = 1 }
finally { stopped = true; if (browser) await browser.close(); evidence.client_wrapper_peak_rss_kib = process.resourceUsage().maxRSS; persist() }
if (evidence.automated_observation !== 'pass') process.exitCode = 1
