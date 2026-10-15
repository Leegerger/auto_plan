/** External Chromium acceptance. Public APIs only; login secrets stay in memory. */
import { chromium, expect } from '@playwright/test'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gunzipSync } from 'node:zlib'
import { createHash, randomUUID } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { performance } from 'node:perf_hooks'
import { prepareWeekNavigation, navigateWeek, prepareDayNavigation, navigateDay, assertSelected, unselectSegment } from './navigation.mjs'
import { canonical, assertPlan, assertState, manualRows, scopeFor, assertManualRequest, assertManualPlan, assertManualEvaluation, failEntry, terminal } from './contracts.mjs'
import { inputEditorLoop } from './input-editors.mjs'
import { inputContentDigest, resolveInputCompanion } from './input-load.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const flags = Object.fromEntries(process.argv.slice(2).map((arg, i, all) => arg.startsWith('--') ? [arg.slice(2), all[i + 1]] : null).filter(Boolean))
assert.ok(flags.config && flags.output, 'Provide --config and a new --output directory.')
const configBytes = readFileSync(flags.config), config = JSON.parse(configBytes)
const origin = new URL(config.origin)
assert.ok(origin.protocol === 'https:' && origin.pathname === '/' && !origin.username && !origin.password, 'Provide an HTTPS origin without credentials.')
assert.ok(['PLAN_TEST', 'adapter'].includes(config.target?.kind) && config.target.disposable === true && config.target.production === false, 'Select an explicit disposable test target.')
assert.ok(config.target.kind !== 'PLAN_TEST' || /^AP_TEST_[A-F0-9]{6}$/.test(config.target.namespace), 'Provide the authorized disposable PLAN_TEST namespace.')
assert.ok(config.base_path?.startsWith('/') && !config.base_path.endsWith('/'), 'Provide the retained application prefix.')
const mode = flags.mode ?? 'solo', operations = (flags.operations ?? 'selection,week,day,single_cell,batch_preview,shared_save').split(',')
assert.ok(['solo', 'five_automated_editors_one_computation'].includes(mode), 'Unknown load mode.')
const importOnly = operations.length === 1 && operations[0] === 'import_validation'
assert.ok(!operations.includes('import_validation') || importOnly, 'Initial import validation requires a separate fresh-target run.')
const fixtures = resolve(root, config.fixtures ?? 'docs/acceptance/frozen')
const manifestBytes = readFileSync(resolve(fixtures, 'manifest.json')), manifest = JSON.parse(manifestBytes)
const scenario = manifest.scenarios.find(row => row.id === config.scenario)
assert.ok(scenario, 'Unknown frozen scenario.')
const sha = bytes => createHash('sha256').update(bytes).digest('hex')
function asset(pointer) {
  const bytes = gunzipSync(readFileSync(resolve(fixtures, pointer.path)))
  assert.equal(sha(bytes), pointer.sha256, 'Frozen asset hash mismatch.')
  return JSON.parse(bytes)
}
const source = asset(scenario.source), action = asset(scenario.action), output = resolve(flags.output)
mkdirSync(output, { recursive: false })
const editingProbe = flags['editing-contract-probe'] === 'true'
const primaryPinProbe = flags['primary-pin-probe'] === 'true'
assert.ok(!editingProbe || mode !== 'solo' && config.target.kind === 'adapter', 'The editing-contract probe requires five disposable adapter accounts.')
assert.ok(!primaryPinProbe || editingProbe, 'The primary-pin probe is part of the explicit adapter correctness workflow.')
let inputCompanion
if (mode !== 'solo') {
  assert.ok(config.input_companion?.path && config.input_companion.sha256, 'New five-editor runs require their frozen maintained-input companion.')
  const bytes = readFileSync(resolve(config.input_companion.path))
  assert.equal(sha(bytes), config.input_companion.sha256, 'Input companion bytes differ from their run configuration.')
  inputCompanion = JSON.parse(bytes)
  assert.equal(inputCompanion.scenario, scenario.id, 'The input companion names another scenario.')
  assert.equal(inputCompanion.source_manifest_sha256, sha(manifestBytes), 'The input companion names another manifest.')
  assert.equal(inputCompanion.source_fixture_sha256, scenario.source.sha256, 'The input companion names another frozen source.')
  resolveInputCompanion(inputCompanion, source.inputs, source.inputs)
  writeFileSync(resolve(output, 'input-companion.json'), bytes)
  mkdirSync(resolve(output, 'input-bodies'))
}
const helperFiles = ['frontend/tests/acceptance/navigation.mjs', 'frontend/tests/acceptance/contracts.mjs', 'frontend/tests/acceptance/input-load.mjs', 'frontend/tests/acceptance/input-editors.mjs', 'scripts/acceptance/check_result.py', 'scripts/acceptance/assertions.py', 'scripts/acceptance/compare.py', 'scripts/acceptance/workbook.py']
const requiredEnvironment = ['image_digest', 'source_commit', 'migration_versions', 'python', 'native_libraries', 'server_hardware', 'client_hardware', 'cgroup_cpu_max', 'cgroup_memory_max', 'solver_threads', 'worker_count', 'oracle_pool_maximum', 'oracle_pool_observed_usage', 'network_proxy_path', 'peak_cpu_memory']
const missingEnvironment = requiredEnvironment.filter(key => config.environment?.[key] === null || config.environment?.[key] === undefined || config.environment[key] === '')
const evidence = { schema_version: inputCompanion ? 3 : 2, kind: editingProbe ? 'public browser input-editing correctness probe; no timed acceptance' : config.target.kind === 'adapter' ? 'external browser with explicit test adapters' : 'external browser to operator-attested disposable PLAN_TEST',
  runner_sha256: sha(readFileSync(fileURLToPath(import.meta.url))), helper_sha256: Object.fromEntries(helperFiles.map(path => [path, sha(readFileSync(resolve(root, path)))])),
  run_config_sha256: sha(configBytes), manifest_sha256: sha(manifestBytes), fixture_sha256: scenario.source.sha256, reference_sha256: scenario.reference.sha256, action_sha256: scenario.action.sha256,
  mode, scenario: scenario.id, reference_confirmation: manifest.reference_confirmation, environment: config.environment, target: config.target, origin: config.origin, base_path: config.base_path,
  reproducible_environment: { status: missingEnvironment.length ? 'not_run' : 'operator_recorded', missing_fields: missingEnvironment },
  reset_policy: 'Each measured shared save persists a real 100-to-99 edit. Its exact original source is explicitly restored outside timing. Every local undo is awaited and an empty queue plus complete source is required before the next repeat. Caches are retained; the first repeat counts.',
  editor_schedule: 'Four background contexts preview99 then100 locally, await both undos, then acknowledge a complete zero-net public patch of their original line/date witnesses. Every commit is UUID-bound and the full input/context/baseline/plan is checked. The primary is editor five and owns one computation. Save admission is held only while pinning/submitting that computation, then resumed before result measurement completes. Known complete primary99 snapshots are permitted only during the measured modified-save/restoration interval; each next repeat requires the exact original source.',
  repeats: [], editor_actions: [], request_spans: [], operations: [], submissions: [], jobs: [], acceptance: 'not_run',
  real_user_acceptance: { status: 'not_run', reason: 'Synthetic contexts do not establish five humans, twenty real accounts, company authentication or user-confirmed reference quality.' } }
if (inputCompanion) {
  evidence.input_companion = { path: 'input-companion.json', sha256: config.input_companion.sha256 }
  evidence.editor_schedule = 'Four actual Inputs browsers alternate their assigned existing names through preview/apply. First previews synchronize to exercise actual optimistic conflicts. Each actor waits after its first commit until all four have committed; this is premeasurement warmup. Later iterations continue independently with public reload/retry. Every genuine changed immutable version and raw request/reply is retained. Names are restored after stopping. Primary-operation load qualification remains pending.'
  evidence.input_history = []
  evidence.input_editing_contract = { status: 'not_run', continuing_activity: 'not_qualified', probe_only: editingProbe }
}
function persist() { writeFileSync(resolve(output, 'report.json'), JSON.stringify(evidence, null, 2) + '\n') }
function privateAccounts() {
  try { return JSON.parse(readFileSync(0, 'utf8')).accounts }
  catch { throw new Error('Private stdin accounts JSON is invalid.') }
}
const accounts = privateAccounts(), count = mode === 'solo' ? 1 : 5
assert.ok(Array.isArray(accounts) && accounts.length === count, 'Supply exactly one or five authorized test accounts through private stdin.')
assert.ok(accounts.every(row => typeof row?.username === 'string' && row.username.trim() && typeof row?.password === 'string'), 'Each private account needs a username and password string.')
assert.equal(new Set(accounts.map(row => row.username.trim().toUpperCase())).size, count, 'Editor identities must be distinct.')
const headers = { Origin: config.origin, 'X-Auto-Plan-Request': '1', 'Cache-Control': 'no-cache' }, api = path => config.origin + config.base_path + '/api' + path
let browser, stopped = false, blocked = null, initialState, primaryPlans = [], loadPromises = [], saveAdmissionHeld = false, inFlightSaves = 0
let inputEditors
const pages = [], createdJobs = new Map()
const importCaptureSessions = new WeakMap()
function responseFor(page, predicate, options) { const pending = page.waitForResponse(predicate, options); pending.catch(() => {}); return pending }
function requestFor(page, predicate, options) { const pending = page.waitForRequest(predicate, options); pending.catch(() => {}); return pending }
async function retainImportResponse(page, entry) {
  let session = importCaptureSessions.get(page)
  if (!session) { session = await page.context().newCDPSession(page); importCaptureSessions.set(page, session) }
  // Configure after navigation. Chromium otherwise evicts this complete body
  // before Playwright can read it, although the application already consumed it.
  const options = { maxTotalBufferSize: 128 * 1024 * 1024, maxResourceBufferSize: 64 * 1024 * 1024, enableDurableMessages: true }
  await session.send('Network.enable', options)
  entry.response_capture = { protocol: 'Chromium Network.enable', phase: 'before upload input', ...options }
}
async function holdSaveAdmission(entry) {
  saveAdmissionHeld = true; entry.save_admission_hold_started_ms = performance.now()
  try { await expect.poll(() => inFlightSaves, { timeout: 30000 }).toBe(0) } catch (error) { saveAdmissionHeld = false; throw error }
  return () => { saveAdmissionHeld = false; entry.save_admission_resumed_ms = performance.now() }
}
async function json(page, path, data) {
  const response = data === undefined ? await page.request.get(api(path), { headers }) : await page.request.post(api(path), { headers, data })
  assert.ok(response.ok(), `Public ${path.split('?')[0]} returned HTTP ${response.status()}.`)
  return response.json()
}
async function readState(page, browserRead = false) {
  const get = browserRead ? path => page.evaluate(async url => { const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', headers: { 'X-Auto-Plan-Request': '1' } }); if (!response.ok) throw new Error('Fresh browser read returned HTTP ' + response.status); return response.json() }, api(path)) : path => json(page, path)
  const head = await get('/plans/current')
  const inputs = await get('/inputs?version=' + head.input_version)
  const adopted = head.latest_adoption ? await get('/plans/adoptions/' + head.latest_adoption) : null
  return { head, snapshot: { time: head.time, inputs, plan: head.plan, execution: head.context.execution, locks: head.context.locks, reset: head.context.reset, preferences: head.context.preferences,
    baseline: adopted ? { version: head.latest_adoption, plan: adopted.snapshot.plan, coverage: adopted.coverage } : null } }
}
function allowedPlans() { return [initialState.snapshot.plan, ...primaryPlans] }
function assertAllowed(state, plans = allowedPlans()) { assertState(state, initialState, plans, { inputCompanion }) }
const tokens = head => ({ base_revision: head.revision, input_version: head.input_version, latest_adoption: head.latest_adoption })
const queue = page => page.getByRole('region', { name: '本机完整动作队列', exact: true }).getByRole('checkbox')
async function idle(page) { await expect(page.getByRole('button', { name: '预览本机修改', exact: true })).toBeEnabled({ timeout: 180000 }) }
async function settle(page) { return page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(() => done(performance.timeOrigin + performance.now()))))) }
async function arm(page) { await page.evaluate(() => { window.__acceptanceInputEpoch = undefined }) }
async function clicked(page, entry) {
  const value = await page.evaluate(() => window.__acceptanceInputEpoch)
  assert.ok(Number.isFinite(value), 'Browser input timestamp is missing.')
  entry.clicked_epoch_ms = value; entry.clicked_ms = value - performance.timeOrigin; entry.measurement_started = true
  if (Number.isFinite(entry.setup_started_ms)) entry.setup_seconds = (entry.clicked_ms - entry.setup_started_ms) / 1000
}
async function rendered(page, entry) {
  entry.browser_after_result_frames_epoch_ms = await settle(page)
  entry.full_render_ms = performance.now(); entry.feedback_ms ??= entry.full_render_ms
  entry.seconds = (entry.full_render_ms - entry.clicked_ms) / 1000
  entry.browser_after_result_frames_seconds = (entry.browser_after_result_frames_epoch_ms - entry.clicked_epoch_ms) / 1000
  entry.node_observed_seconds = entry.seconds
  assert.ok(Number.isFinite(entry.seconds) && entry.seconds >= 0, 'Browser completion interval is invalid.')
}
function check(kind, evaluation, plan, stem, proof, observedSource) {
  const args = [resolve(root, 'scripts/acceptance/check_result.py'), '--fixtures', fixtures, '--scenario', scenario.id, '--kind', kind]
  const save = (name, value, flag) => { const path = resolve(output, stem + '-' + name + '.json'); writeFileSync(path, JSON.stringify(value)); args.push(flag, path) }
  save('evaluation', evaluation, '--evaluation')
  if (plan) save('plan', plan, '--candidate')
  if (proof) save('proof', proof, '--proof')
  if (observedSource) save('snapshot', observedSource, '--observed-source')
  const result = spawnSync(config.python ?? 'python', args, { cwd: root, encoding: 'utf8' })
  writeFileSync(resolve(output, stem + '-comparison.json'), JSON.stringify({ status: result.status, stdout: result.stdout, stderr: result.stderr, error: result.error?.message }, null, 2))
  assert.equal(result.status, 0, `Independent ${kind} comparison failed; retained ${stem} artifacts identify the inputs.`)
  return JSON.parse(result.stdout)
}
async function shared(page) {
  await page.goto(config.origin + config.base_path + '/plans/shared', { timeout: 180000 })
  await expect(page.getByLabel('计划周次', { exact: true })).toBeVisible({ timeout: 180000 }); await idle(page)
  await expect(queue(page)).toHaveCount(0)
}
async function selectRow(page, row) {
  assert.ok(row, 'A requested source segment is absent.')
  const week = Math.floor((Date.parse(row.date) - Date.parse(source.time.window_start)) / 604800000) + 1
  await page.getByLabel('计划周次', { exact: true }).selectOption(String(week))
  await page.getByRole('button', { name: `${row.date} ${row.line_code} 日内详情`, exact: true }).click()
  await page.getByLabel('选择生产段 ' + row.id, { exact: true }).check()
  await scopeInputs(page, scopeFor([row]))
  await page.getByLabel('顺延截止日', { exact: true }).fill(row.date)
}
async function scopeInputs(page, scope) {
  await page.getByLabel('调整范围起日', { exact: true }).fill(scope.start_date)
  await page.getByLabel('调整范围终日', { exact: true }).fill(scope.end_date)
  await page.getByLabel('调整范围线体', { exact: true }).selectOption(scope.line_codes)
  await page.getByLabel('调整范围机种', { exact: true }).selectOption(scope.model_codes)
}
async function assertDaily(page, row, quantity) {
  const tr = page.getByLabel('选择生产段 ' + row.id, { exact: true }).locator('xpath=ancestor::tr')
  await expect(tr).toContainText(row.model_code)
  await expect(tr.locator('td').nth(2)).toHaveText(`${quantity}片`)
}
async function assertOutput(page, evaluation, parent = page) {
  const result = parent === page
    ? page.getByRole('heading', { name: '共享计划', exact: true }).locator('..').locator(':scope > section[aria-label="产出需求与确认核算"]')
    : parent.getByRole('region', { name: '产出需求与确认核算', exact: true })
  await expect(result.locator('p').filter({ hasText: /(?:预计产出|当前已算产出)/ })).toContainText(new RegExp(`预计产出\\s*${evaluation.output_totals.expected}\\s*片；窗口外\\s*${evaluation.output_totals.outside_window}\\s*片；未分配\\s*${evaluation.output_totals.unallocated}\\s*片。`))
  const first = evaluation.weekly_results[0]
  if (first) {
    const row = result.getByRole('table', { name: '周需求覆盖', exact: true }).locator('tbody tr').first()
    for (const [index, value] of [[0, first.model_code], [1, first.week], [2, first.demand], [3, first.on_time], [6, first.gap]]) await expect(row.locator('td').nth(index)).toHaveText(String(value))
  }
}
async function previewChange(page, row, quantity, entry) {
  await page.getByLabel('改量后总量', { exact: true }).fill(String(quantity))
  await expect(page.getByLabel('改量后总量', { exact: true })).toHaveValue(String(quantity))
  const response = responseFor(page, r => r.url() === api('/plans/preview') && r.request().method() === 'POST', { timeout: 180000 })
  await arm(page); await page.getByRole('button', { name: '预览完整改量顺延', exact: true }).click(); await clicked(page, entry)
  const received = await response; assert.ok(received.ok(), 'Quantity preview failed.')
  const result = await received.json(), command = received.request().postDataJSON()
  await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible({ timeout: 180000 })
  await idle(page); await assertDaily(page, row, quantity); await assertOutput(page, result.evaluation)
  await rendered(page, entry)
  return { result, command }
}
async function resetQueue(page, expectedCount) {
  await idle(page)
  let remaining = await queue(page).count()
  if (expectedCount !== undefined) assert.equal(remaining, expectedCount, 'Unexpected action count before settled undo.')
  const countBefore = remaining
  let lastResult
  while (remaining) {
    const response = responseFor(page, r => r.url() === api('/plans/preview') && r.request().method() === 'POST', { timeout: 180000 })
    await page.getByRole('button', { name: '撤销最后完整动作', exact: true }).click()
    const received = await response; assert.ok(received.ok(), 'Undo preview failed.'); lastResult = await received.json()
    await idle(page); await expect(queue(page)).toHaveCount(--remaining)
  }
  await settle(page); await expect(queue(page)).toHaveCount(0)
  const local = await page.evaluate(() => Object.keys(localStorage).map(key => { try { const value = JSON.parse(localStorage.getItem(key)); return value?.draft ? { count: value.draft.operations.length, outstanding: !!value.outstanding } : null } catch { return null } }).filter(Boolean))
  assert.ok(local.every(row => row.count === 0 && !row.outstanding), 'A local queue or unresolved UUID remains after reset.')
  if (lastResult) assert.ok(allowedPlans().some(plan => { try { assertPlan(lastResult.plan, plan); return true } catch { return false } }), 'Empty local replay did not restore a complete permitted shared source witness.')
  return { actions_undone: countBefore, queue_empty: true, effective_plan_sha256: lastResult ? sha(JSON.stringify(canonical(lastResult.plan))) : null }
}
function registerOperation(command, purpose) {
  const record = { operation_id: command.operation_id, purpose, command, status: 'submitted' }
  evidence.operations.push(record); persist(); return record
}
async function verifySave(page, command, result, expectedPlan, record) {
  assert.match(command.operation_id, /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i, 'Save operation identity is not a UUID.')
  const verified = await json(page, '/plans/verify', { operation_id: command.operation_id })
  assert.equal(verified.status, 'COMMITTED', 'The submitted save UUID is not committed.')
  assert.equal(verified.revision, result.revision, 'The UUID resolves to a different revision.')
  assert.ok(Number.isInteger(result.revision) && result.revision > command.base_revision, 'Save did not create a new persisted revision.')
  assert.equal(result.input_version, command.input_version, 'Save changed its input version.')
  assert.equal(result.latest_adoption, command.latest_adoption, 'Save changed adoption.')
  const revision = await json(page, '/plans/revisions/' + result.revision)
  assert.equal(revision.revision, result.revision, 'Fresh immutable revision identity differs.')
  assert.deepEqual(canonical(revision.operations), canonical(command.operations), 'Persisted complete action differs from the submitted UUID action.')
  if (expectedPlan) assertPlan(revision.snapshot.plan, expectedPlan, 'Exact saved revision plan differs from its preview.')
  const current = await readState(page, true)
  record.fresh_read = 'browser fetch with cache no-store'
  if (expectedPlan) assertPlan(current.snapshot.plan, expectedPlan, 'Fresh persisted head differs from the modified saved preview.')
  assertAllowed(current)
  record.status = 'COMMITTED'; record.revision = result.revision; record.latest_adoption = result.latest_adoption
  record.fresh_head_revision = current.head.revision; persist()
  return current
}
async function apiSave(page, operation, purpose, expectedPlan) {
  const before = await readState(page); assertAllowed(before)
  if (purpose.startsWith('background_')) while (saveAdmissionHeld) await new Promise(done => setTimeout(done, 10))
  const command = { ...tokens(before.head), operations: [operation], operation_id: randomUUID(), pending: false }
  const record = registerOperation(command, purpose)
  let result
  try { inFlightSaves++; try { result = await json(page, '/plans/save', command) } finally { inFlightSaves-- } }
  catch (error) { record.acknowledgment_failure = String(error.message).split('\n')[0]; result = await json(page, '/plans/verify', { operation_id: command.operation_id }); record.resolution = result; record.status = result.status; persist(); if (result.status !== 'COMMITTED') throw error }
  assert.equal(result.status, 'COMMITTED', 'Public save did not acknowledge a commit.')
  await verifySave(page, command, result, expectedPlan, record)
  return { ...result, operation_id: command.operation_id }
}
async function uiSave(page, entry, expectedPlan) {
  const request = requestFor(page, r => r.url() === api('/plans/save') && r.method() === 'POST', { timeout: 180000 })
  const response = responseFor(page, r => r.url() === api('/plans/save') && r.request().method() === 'POST', { timeout: 180000 })
  await arm(page); await page.getByRole('button', { name: '共享保存', exact: true }).click(); await clicked(page, entry)
  const command = (await request).postDataJSON()
  const record = registerOperation(command, 'measured_modified_shared_save')
  entry.submitted_save = command
  assert.deepEqual(canonical(command.operations), canonical(entry.prepared_save_command.operations), 'Saved actions differ from the modified checked preview.')
  assert.equal(command.input_version, initialState.head.input_version, 'Save input binding changed.')
  assert.equal(command.latest_adoption, initialState.head.latest_adoption, 'Save adoption binding changed.')
  const received = await response; assert.ok(received.ok(), 'Modified shared save was not acknowledged.')
  const result = await received.json(); assert.equal(result.status, 'COMMITTED', 'Save did not commit.')
  await expect(page.getByText(`已共享保存修订 ${result.revision}，最近采用版未改变`, { exact: true })).toBeVisible({ timeout: 180000 })
  await idle(page); await expect(queue(page)).toHaveCount(0); entry.expected_queue_count = 0
  const changed = expectedPlan.segments.find(row => row.id === config.primary_segment); await assertDaily(page, changed, 99)
  await rendered(page, entry)
  entry.phase = 'verify_uuid_revision_and_fresh_modified_read'
  await verifySave(page, command, result, expectedPlan, record)
  entry.committed = { ...result, operation_id: command.operation_id }
}
async function activeJobs(page) { return (await json(page, '/jobs')).jobs.filter(job => ['queued', 'running'].includes(job.state)) }
function registerSubmission(entry, submission, purpose) {
  const record = { submission_id: submission.submission_id, submission, purpose, status: 'submitted' }
  evidence.submissions.push(record); (entry.submission_records ??= []).push(record); persist(); return record
}
function trackJob(jobId, submission, purpose) {
  if (createdJobs.has(jobId)) return
  const value = { job_id: jobId, submission, purpose, cleanup: 'pending' }
  createdJobs.set(jobId, value); evidence.jobs.push(value); persist()
}
async function resolveSubmissions(page, entry) {
  for (const record of entry.submission_records ?? []) {
    const resolution = await json(page, '/jobs/verify', { submission_id: record.submission_id })
    record.resolution = resolution; record.status = resolution.status
    if (resolution.status === 'ACCEPTED') {
      if (record.job_id) assert.equal(resolution.job_id, record.job_id, 'Original submission UUID resolved to another job.')
      record.job_id = resolution.job_id; trackJob(resolution.job_id, record.submission, record.purpose)
      if (record.purpose === 'measured_computation') entry.job_id = resolution.job_id
      else entry.load_job_id = resolution.job_id
    } else assert.equal(resolution.status, 'ABORTED_VOID', 'A job submission outcome remains unknown.')
    persist()
  }
}
async function finishJob(page, jobId) {
  const record = createdJobs.get(jobId), before = await json(page, '/jobs/' + jobId)
  if (!terminal(before.state)) record.stop_response = await json(page, '/jobs/' + jobId + '/stop', {})
  let last
  await expect.poll(async () => { last = await json(page, '/jobs/' + jobId); return terminal(last.state) }, { timeout: 30000 }).toBe(true)
  record.cleanup = 'terminal'; record.terminal_state = last.state; record.terminal_timestamps = Object.fromEntries(Object.entries(last).filter(([key]) => key.endsWith('_at'))); persist()
  return { job_id: jobId, state: last.state }
}
async function startLoad(page, entry) {
  const release = await holdSaveAdmission(entry)
  try {
  const current = await readState(page); assertAllowed(current)
  const submission = { submission_id: randomUUID(), ...tokens(current.head), request: { ...action.request, time_limit_seconds: 90 } }
  const pending = registerSubmission(entry, submission, 'single_background_computation')
  const accepted = await json(page, '/jobs', submission); pending.status = accepted.status; pending.job_id = accepted.job_id; trackJob(accepted.job_id, submission, 'single_background_computation')
  entry.load_job_id = accepted.job_id
  let running
  await expect.poll(async () => { running = await json(page, '/jobs/' + accepted.job_id); return running.state }, { timeout: 30000 }).toBe('running')
  entry.load_running_before = running; return accepted.job_id
  } finally { release() }
}
async function compute(page, operation, entry, stem) {
  assert.equal(operation, action.request.mode, 'Measured computation differs from the frozen mode.')
  const release = await holdSaveAdmission(entry)
  try {
  await shared(page)
  for (const [label, value] of [['计算模式', operation], ['求解策略', action.request.strategy]]) await page.getByLabel(label, { exact: true }).selectOption(value)
  for (const [label, value] of [['计算范围起日', action.request.scope.start_date], ['计算范围终日', action.request.scope.end_date], ['计算时限', String(action.request.time_limit_seconds)], ['候选数量', String(action.request.max_candidates)]]) await page.getByLabel(label, { exact: true }).fill(value)
  await page.getByLabel('计算线体', { exact: true }).selectOption(action.request.scope.line_codes)
  await page.getByLabel('计算机种', { exact: true }).selectOption(action.request.scope.model_codes ?? [])
  const captured = await readState(page); assertState(captured, initialState)
  const request = requestFor(page, r => r.url() === api('/jobs') && r.method() === 'POST', { timeout: 180000 })
  const response = responseFor(page, r => r.url() === api('/jobs') && r.request().method() === 'POST', { timeout: 180000 })
  await arm(page); await page.getByRole('button', { name: '提交计算任务', exact: true }).click(); await clicked(page, entry)
  const submitted = (await request).postDataJSON(), pending = registerSubmission(entry, submitted, 'measured_computation')
  const received = await response; assert.ok(received.ok(), 'Computation was not accepted.')
  const accepted = await received.json(); pending.status = accepted.status; pending.job_id = accepted.job_id
  trackJob(accepted.job_id, submitted, 'measured_computation'); entry.job_id = accepted.job_id; entry.submission_id = submitted.submission_id
  release()
  await expect(page.getByRole('region', { name: '计算任务', exact: true })).toContainText('计算任务已接收'); entry.feedback_ms = performance.now()
  const item = page.getByRole('list', { name: '持久计算任务', exact: true }).locator('li').filter({ hasText: accepted.job_id })
  const button = item.getByRole('button', { name: '查看候选内容', exact: true }).first()
  await expect(button).toBeVisible({ timeout: 180000 })
  const viewResponse = responseFor(page, r => r.url().startsWith(api(`/jobs/${accepted.job_id}/candidates/`)), { timeout: 180000 })
  await button.click(); const viewed = await viewResponse; assert.ok(viewed.ok(), 'Candidate content request failed.')
  const view = await viewed.json(), candidate = view.candidate
  const region = page.getByRole('region', { name: '候选内容', exact: true })
  await expect(region.getByRole('heading', { name: '候选 ' + view.candidate_id, exact: true })).toBeVisible()
  await expect(region).toContainText(`原共享修订 ${submitted.base_revision} · 输入版本 ${submitted.input_version} · 最近采用版 ${submitted.latest_adoption}`)
  await expect(region).toContainText('可执行候选')
  await region.locator('summary').filter({ hasText: '查看原始候选业务检查' }).click()
  await assertOutput(page, candidate.evaluation, region)
  await rendered(page, entry)
  entry.phase = 'candidate_frozen_provenance_and_independent_quality'
  assert.equal(view.job_id, accepted.job_id, 'Displayed candidate belongs to another job.')
  const proof = { candidate, submitted, source: captured }
  const evaluation = await json(page, '/plans/evaluate', { snapshot: { ...candidate.snapshot, plan: candidate.plan } })
  const compared = check('candidate', evaluation, candidate.plan, stem, proof)
  entry.candidate_id = view.candidate_id; entry.objective = compared.objective; entry.stage_bounds = candidate.stage_bounds
  entry.verification_finished_ms = performance.now()
  } finally { if (saveAdmissionHeld) release() }
}

function captureInputBody(bytes) {
  const digest = sha(bytes), path = 'input-bodies/' + digest + '.json'
  writeFileSync(resolve(output, path), bytes)
  return { path, sha256: digest }
}
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  promise.catch(() => {})
  return { promise, resolve, reject }
}
function startInputEditors() {
  const first = deferred(), ready = deferred(), previews = new Set(), committed = new Set()
  evidence.input_editor_sessions = []
  loadPromises = inputCompanion.targets.map(target => {
    const session = { editor: target.editor, started_ms: performance.now(), finished_ms: null }
    evidence.input_editor_sessions.push(session)
    return inputEditorLoop({ page: pages[target.editor], editor: target.editor, declaration: inputCompanion,
      initialInputs: initialState.snapshot.inputs, origin: config.origin, basePath: config.base_path,
      stopped: () => stopped, captureBody: captureInputBody,
      firstPreview: () => { previews.add(target.editor); if (previews.size === 4) first.resolve(); return first.promise },
      firstCommit: () => ready.promise,
      onAttempt: entry => {
        const { response, before, after, ...record } = entry
        evidence.editor_actions.push(record)
        if (entry.status === 'unknown') evidence.input_editing_contract.unknown_request = true
        persist()
      },
      onCommit: entry => {
        evidence.input_history.push({ editor: entry.editor, before: entry.before, after: entry.after,
          command: entry.command, response_status: entry.response_status,
          request_artifact: entry.request_artifact, response_artifact: entry.response_artifact })
        committed.add(entry.editor)
        if (committed.size === 4) ready.resolve()
        persist()
      } }).catch(error => {
        const entry = { editor: target.editor, phase: 'real_input_editor', status: 'fail', finished_ms: performance.now() }
        failEntry(entry, error); evidence.editor_actions.push(entry)
        stopped = true; blocked ??= 'A real input editor failed; primary load qualification is incomplete.'
        first.reject(error); ready.reject(error); persist()
      }).finally(() => { session.finished_ms = performance.now(); persist() })
  })
  return { ready: ready.promise, finished: false }
}
async function finishInputEditors() {
  if (!inputEditors || inputEditors.finished) return
  stopped = true; await Promise.all(loadPromises)
  const contract = evidence.input_editing_contract
  contract.status = 'fail'
  // Unknown write replies require their own reconciliation slice. They cannot
  // earn credit or trigger a blind resubmission/restoration here.
  assert.ok(!contract.unknown_request, 'An original input request is unresolved; no blind input write was attempted.')
  const current = await json(pages[0], '/inputs')
  contract.last_modified_inputs = current
  evidence.input_history.sort((a, b) => a.after.input_version - b.after.input_version)
  let historyError
  try {
    resolveInputCompanion(inputCompanion, initialState.snapshot.inputs, current, { history: evidence.input_history })
    assert.deepEqual([...new Set(evidence.input_history.map(row => row.editor))].sort(), [1, 2, 3, 4], 'Four actors did not commit genuine current-run name changes.')
  } catch (error) { historyError = error }
  const restored = structuredClone(current), changes = {}
  for (const target of inputCompanion.targets) {
    restored.masters[target.kind].find(row => row.code === target.code).name = target.names[0]
    changes[target.kind] = restored.masters[target.kind]
  }
  const preview = await json(pages[0], '/inputs/preview', { base_version: current.input_version, changes })
  assert.ok(preview.valid, 'Original input-name restoration preview failed.')
  const command = { base_version: preview.base_version, changes: preview.changes }
  const result = await json(pages[0], '/inputs/apply', command)
  contract.restoration = { command, result, credited_as_editor: false }
  contract.restored_inputs = await json(pages[0], '/inputs?version=' + result.input_version)
  assert.equal(inputContentDigest(contract.restored_inputs), inputContentDigest(initialState.snapshot.inputs), 'Original complete input contents were not restored.')
  assertAllowed(await readState(pages[0], true))
  contract.cleanup = 'complete'; inputEditors.finished = true
  if (historyError) throw historyError
  assert.ok(!blocked, blocked)
  contract.status = 'pass'; persist()
}
async function primaryInputPinProbe() {
  const page = pages[0], row = source.plan.segments.find(item => item.id === config.primary_segment)
  await selectRow(page, row)
  await page.getByLabel('改量后总量', { exact: true }).fill('99')
  const entry = {}, record = { attempts: [], clock: { retry_clicked_epoch_ms: [] } }
  evidence.primary_pin_probe = record
  const until = performance.now() + 30000
  let first = true
  while (performance.now() < until) {
    const pending = responseFor(page, response => response.url() === api('/plans/preview') && response.request().method() === 'POST', { timeout: 180000 })
    if (first) {
      await arm(page); await page.getByRole('button', { name: '预览完整改量顺延', exact: true }).click(); await clicked(page, entry)
      record.clock.origin_epoch_ms = record.clock.first_clicked_epoch_ms = entry.clicked_epoch_ms
    } else {
      await page.getByRole('button', { name: '按最新资料重新预览', exact: true }).click()
      record.clock.retry_clicked_epoch_ms.push(await page.evaluate(() => window.__acceptanceInputEpoch))
    }
    const received = await pending, request = received.request(), bytes = await received.body()
    const attempt = { command: request.postDataJSON(), response_status: received.status(), response: JSON.parse(bytes),
      request_artifact: captureInputBody(request.postDataBuffer()), response_artifact: captureInputBody(bytes) }
    record.attempts.push(attempt); persist()
    if (first) assert.equal(attempt.response_status, 409, 'The primary probe did not begin with its actual stale UI source.')
    first = false
    if (attempt.response_status === 409) { await idle(page); continue }
    assert.equal(attempt.response_status, 200, 'The latest-input primary preview failed.')
    assertManualRequest(attempt.command, [row], 'change_quantity', 99)
    assertManualPlan(attempt.response.plan, source, [row], 'change_quantity')
    resolveInputCompanion(inputCompanion, initialState.snapshot.inputs, attempt.response.snapshot.inputs)
    assert.equal(attempt.command.input_version, attempt.response.snapshot.inputs.input_version)
    await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible()
    await idle(page); await assertDaily(page, row, 99); await assertOutput(page, attempt.response.evaluation)
    await rendered(page, entry)
    record.accepted = attempt
    Object.assign(record.clock, { rendered_epoch_ms: entry.browser_after_result_frames_epoch_ms,
      seconds: entry.browser_after_result_frames_seconds, node_observed_seconds: entry.seconds })
    await expect.poll(async () => {
      record.displayed_reload = await readState(page, true)
      assertAllowed(record.displayed_reload)
      return record.displayed_reload.head.input_version > attempt.command.input_version
    }, { timeout: 30000 }).toBe(true)
    persist(); return
  }
  throw new Error('The primary preview did not complete through actual input conflicts within the adapter probe window.')
}
async function cleanPrimaryInputPinProbe() {
  const page = pages[0], attempts = []
  let undo = true, result
  for (let number = 0; number < 3; number++) {
    const pending = responseFor(page, response => response.url() === api('/plans/preview') && response.request().method() === 'POST', { timeout: 180000 })
    await page.getByRole('button', { name: undo ? '撤销最后完整动作' : '按最新资料重新预览', exact: true }).click()
    const received = await pending
    attempts.push({ response_status: received.status(), command: received.request().postDataJSON() })
    if (received.status() === 409) { undo = false; await idle(page); continue }
    assert.equal(received.status(), 200, 'The original local plan could not be rechecked after input-name restoration.')
    result = await received.json(); break
  }
  assert.ok(result, 'The original local queue remains unresolved.')
  assertPlan(result.plan, source.plan, 'Undo did not restore the original complete local plan.')
  await resetQueue(page, 0)
  evidence.primary_pin_probe.local_cleanup = { attempts, queue_empty: true, original_plan_restored: true }
  persist()
}
try {
  assert.ok(!inputCompanion || editingProbe, 'Primary load timing/pin/retry qualification is pending; only the explicit adapter editing probe is enabled.')
  assert.ok(!importOnly || mode === 'solo', 'Concurrent initial import is not_run: an empty target has no shared editor state.')
  if (operations.some(value => ['single_cell', 'batch_preview', 'shared_save', 'import_validation'].includes(value))) assert.equal(scenario.kind, 'loose', 'No matching independent manual/workbook result exists for this family.')
  const primary = source.plan.segments.find(row => row.id === config.primary_segment); assert.ok(primary, 'Primary segment is absent from the frozen source.')
  if (operations.some(value => ['single_cell', 'shared_save'].includes(value))) manualRows(source, [primary.id])
  let workbook, workbookProof
  if (importOnly) {
    workbook = resolve(config.workbook); workbookProof = JSON.parse(readFileSync(config.workbook_provenance ?? workbook + '.acceptance.json'))
    assert.equal(workbookProof.workbook_sha256, sha(readFileSync(workbook)), 'Workbook bytes differ from their prepared provenance.')
    assert.equal(workbookProof.import_sha256, scenario.import.sha256, 'Workbook provenance names another frozen import.')
    assert.equal(workbookProof.scenario, scenario.id, 'Workbook provenance names another scenario.')
    assert.equal(workbookProof.manifest_sha256, sha(manifestBytes), 'Workbook provenance names another frozen manifest.')
    evidence.workbook_provenance = workbookProof
  }
  browser = await chromium.launch({ headless: true }); evidence.client = { chromium: browser.version(), node: process.version, platform: process.platform }
  for (let editor = 0; editor < count; editor++) {
    evidence.phase = `login_editor_${editor}`; persist()
    const context = await browser.newContext({ ignoreHTTPSErrors: config.allow_test_certificate === true })
    await context.addInitScript(() => { for (const type of ['click', 'change']) window.addEventListener(type, () => { window.__acceptanceInputEpoch = performance.timeOrigin + performance.now() }, true) })
    const page = await context.newPage(); pages.push(page)
    page.on('response', response => { const url = new URL(response.url()); if (url.pathname.startsWith(config.base_path + '/api/') && !url.pathname.includes('/auth/')) evidence.request_spans.push({ editor, path: url.pathname, status: response.status(), received_ms: performance.now(), request_started_epoch_ms: response.request().timing().startTime }) })
    await page.goto(config.origin + config.base_path + '/login', { timeout: 180000 })
    await page.getByRole('textbox', { name: '公司账号', exact: true }).fill(accounts[editor].username)
    await page.getByLabel('公司密码', { exact: true }).fill(accounts[editor].password)
    const login = responseFor(page, r => r.url() === api('/auth/login') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '登录', exact: true }).click(); assert.ok((await login).ok(), 'Login was not acknowledged.'); accounts[editor].password = ''
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeVisible({ timeout: 180000 }); await shared(page)
  }
  evidence.phase = 'complete_source_preflight'; persist()
  initialState = await readState(pages[0]); const head = initialState.head
  evidence.initial_tokens = { source_revision: head.revision, input_version: head.input_version, latest_adoption: head.latest_adoption }
  assert.equal(head.time.as_of, source.time.as_of, 'Controlled clock differs from frozen source.')
  if (importOnly) assert.ok(head.revision === 0 && head.latest_adoption === 0, 'Initial-import target is not empty.')
  else {
    assert.deepEqual(evidence.initial_tokens, config.bindings, 'Prepared source tokens differ from the run configuration.')
    check('source', head.evaluation, head.plan, 'source-preflight', null, initialState.snapshot)
  }
  const loads = mode === 'solo' || inputCompanion ? [] : (config.load_segments ?? []).map(id => source.plan.segments.find(row => row.id === id))
  assert.ok(mode === 'solo' || inputCompanion || loads.length === 4 && loads.every(Boolean), 'Four frozen load segments are required.')
  if (!inputCompanion) {
    assert.equal(new Set([primary, ...loads].map(row => row.model_code)).size, count, 'Load edits need disjoint models.')
    assert.equal(new Set([primary, ...loads].map(row => row.line_code)).size, count, 'Load edits need disjoint lines.')
  }
  if (loads.length) manualRows(source, loads.map(row => row.id))
  assert.equal((await activeJobs(pages[0])).length, 0, 'An unreported active/queued job predates the run.')
  if (inputCompanion) {
    evidence.phase = 'real_input_editor_warmup'; persist()
    inputEditors = startInputEditors()
    await inputEditors.ready
    if (editingProbe) {
      if (primaryPinProbe) await primaryInputPinProbe()
      await finishInputEditors()
      if (primaryPinProbe) await cleanPrimaryInputPinProbe()
    }
  }
  for (const operation of editingProbe ? [] : operations) {
    assert.ok(operation in manifest.gates_seconds, 'Unknown measured operation.')
    for (let repeat = 1; repeat <= 5; repeat++) {
      const page = pages[0], stem = `${operation}-${repeat}`, entry = { operation, repeat, first: repeat === 1, gate_seconds: manifest.gates_seconds[operation], status: 'fail', phase: 'setup', setup_started_ms: performance.now(), setup_seconds: null, measurement_started: false, seconds: null, expected_queue_count: 0 }
      let loadJob, modifiedPlan
      evidence.phase = stem; persist()
      if (blocked) { entry.status = 'not_run'; entry.reason = blocked; evidence.repeats.push(entry); persist(); continue }
      try {
        assert.ok(!stopped, 'A background editor stopped unexpectedly.')
        entry.active_jobs_before = await activeJobs(page); assert.equal(entry.active_jobs_before.length, 0, 'A preceding job is still queued/running.')
        if (!importOnly) { await shared(page); const current = await readState(page); assertState(current, initialState); entry.source_tokens = { source_revision: current.head.revision, input_version: current.head.input_version, latest_adoption: current.head.latest_adoption }; await selectRow(page, primary) }
        if (operation === 'selection') await assertSelected(page, primary)
        if (operation === 'week') entry.navigation = await prepareWeekNavigation(page, head, primary.line_code)
        if (operation === 'day') entry.navigation = await prepareDayNavigation(page, source, primary)
        const batch = operation === 'batch_preview' ? manualRows(source, ['S-2027-01-24-M00', 'S-2027-01-24-M02']) : null
        if (batch) {
          await page.getByRole('button', { name: '取消生产段选择', exact: true }).click()
          for (const row of batch) { await selectRow(page, row); await expect(page.getByLabel('选择生产段 ' + row.id, { exact: true })).toBeChecked() }
          await scopeInputs(page, scopeFor(batch)); await page.getByLabel('手动操作', { exact: true }).selectOption('clear_segments')
          await expect(page.getByText(`已选 2 段：${batch.map(row => `${row.date}/${row.line_code}/${row.model_code} 100片`).join('；')}`, { exact: true })).toBeVisible()
        }
        if (operation === 'shared_save') {
          entry.phase = 'prepare_real_modified_save'; entry.expected_queue_count = 1
          const preview = await previewChange(page, primary, 99, {})
          assertManualRequest(preview.command, [primary], 'change_quantity', 99); assertManualPlan(preview.result.plan, source, [primary], 'change_quantity'); assertManualEvaluation(preview.result.evaluation, [primary])
          modifiedPlan = preview.result.plan; primaryPlans.push(modifiedPlan); entry.prepared_save_command = preview.command; entry.prepared_modified_plan_sha256 = sha(JSON.stringify(canonical(modifiedPlan)))
          assertState(await readState(page), initialState)
        }
        if (mode !== 'solo' && !['local', 'new_week', 'all_replan'].includes(operation)) { entry.phase = 'setup_running_computation'; loadJob = await startLoad(page, entry) }
        entry.phase = 'measured_action'
        if (operation === 'single_cell' || operation === 'batch_preview') entry.expected_queue_count = 1
        await arm(page)
        if (operation === 'selection') { await unselectSegment(page, primary); await clicked(page, entry); await rendered(page, entry) }
        else if (operation === 'week') { await navigateWeek(page, entry.navigation); await clicked(page, entry); await rendered(page, entry) }
        else if (operation === 'day') { await navigateDay(page, entry.navigation); await clicked(page, entry); await rendered(page, entry) }
        else if (operation === 'single_cell') {
          const preview = await previewChange(page, primary, 99, entry)
          entry.phase = 'exact_single_cell_postconditions'
          assertManualRequest(preview.command, [primary], 'change_quantity', 99); assertManualPlan(preview.result.plan, source, [primary], 'change_quantity'); assertManualEvaluation(preview.result.evaluation, [primary])
          entry.submitted_action = preview.command; entry.observed_totals = preview.result.evaluation.output_totals
        } else if (batch) {
          const response = responseFor(page, r => r.url() === api('/plans/preview') && r.request().method() === 'POST', { timeout: 180000 })
          await page.getByRole('button', { name: '加入完整动作并预览', exact: true }).click(); await clicked(page, entry)
          const received = await response; assert.ok(received.ok(), 'Batch preview failed.'); const result = await received.json(), command = received.request().postDataJSON()
          await expect(page.getByText('预览完成，尚未共享', { exact: true })).toBeVisible({ timeout: 180000 }); await idle(page); await assertOutput(page, result.evaluation); await rendered(page, entry)
          entry.phase = 'exact_two_named_batch_postconditions'; assertManualRequest(command, batch, 'clear_segments'); assertManualPlan(result.plan, source, batch, 'clear_segments'); assertManualEvaluation(result.evaluation, batch, 0, true)
          for (const row of batch) assert.ok(!result.plan.segments.some(item => item.id === row.id), 'A named batch segment remains.')
          entry.submitted_action = command; entry.observed_totals = result.evaluation.output_totals
        } else if (operation === 'shared_save') await uiSave(page, entry, modifiedPlan)
        else if (importOnly) {
          await page.goto(config.origin + config.base_path + '/initialize'); const before = await readState(page)
          assertState(before, initialState); assert.ok(before.head.revision === 0 && before.head.latest_adoption === 0, 'Import target changed before upload.')
          await expect(page.getByRole('heading', { name: '首次接入与需求变更', exact: true })).toBeVisible()
          await retainImportResponse(page, entry)
          const response = responseFor(page, r => r.url().startsWith(api('/initialization/excel/preview')), { timeout: 180000 })
          await arm(page); await page.getByLabel('上传标准工作簿并预览', { exact: true }).setInputFiles(workbook); await clicked(page, entry)
          const received = await response; assert.ok(received.ok(), 'Workbook preview request failed.'); const prepared = await received.json()
          await expect(page.getByText('预览通过，尚未写入。', { exact: true })).toBeVisible({ timeout: 180000 }); await rendered(page, entry)
          entry.phase = 'frozen_workbook_payload_and_no_write'; const after = await readState(page)
          check('workbook', prepared.evaluation, null, stem, { prepared, before, after }); entry.workbook_sha256 = workbookProof.workbook_sha256; entry.observed_totals = prepared.evaluation.output_totals
        } else await compute(page, operation, entry, stem)
        entry.phase = 'postcondition_and_artifact_verification'
        if (loadJob) {
          const after = await json(page, '/jobs/' + loadJob); entry.load_running_after = after
          assert.equal(after.state, 'running', 'The load computation ended before the complete measured interval.')
          for (const key of ['job_id', 'source_revision', 'input_version', 'latest_adoption', 'created_at', 'started_at', 'generation']) assert.deepEqual(after[key], entry.load_running_before[key], 'Load job identity/generation changed during the measured interval.')
        }
        if (!importOnly && operation !== 'shared_save') assertState(await readState(page), initialState)
        await page.screenshot({ path: resolve(output, stem + '.png'), fullPage: true })
        entry.status = entry.seconds <= entry.gate_seconds ? 'pass' : 'fail'
        if (entry.status === 'fail') entry.reason = 'The complete result exceeded the unchanged gate.'
      } catch (error) { failEntry(entry, error); entry.failure_detail = String(error.message).slice(0, 8000); if (entry.setup_seconds === null) entry.setup_seconds = (performance.now() - entry.setup_started_ms) / 1000 }
      finally {
        try {
          entry.phase = 'resolve_original_job_submissions'; await resolveSubmissions(page, entry)
          entry.phase = 'terminal_job_cleanup'
          for (const id of new Set([entry.job_id, entry.load_job_id, loadJob].filter(Boolean))) (entry.job_cleanup ??= []).push(await finishJob(page, id))
          if (!importOnly) {
            entry.phase = 'settled_queue_cleanup'
            if (entry.submitted_save) {
              const resolved = await json(page, '/plans/verify', { operation_id: entry.submitted_save.operation_id })
              entry.save_uuid_cleanup_resolution = resolved
              assert.ok(['COMMITTED', 'ABORTED_VOID'].includes(resolved.status), 'The original save UUID remains unresolved.')
              const record = evidence.operations.find(item => item.operation_id === entry.submitted_save.operation_id)
              record.resolution = resolved; record.status = resolved.status
              entry.expected_queue_count = resolved.status === 'COMMITTED' ? 0 : 1
            }
            const verify = page.getByRole('button', { name: '核实前次操作', exact: true })
            if (await verify.isVisible()) {
              await verify.click(); await idle(page); entry.unknown_save_verified = true
              if (entry.submitted_save) { const resolved = await json(page, '/plans/verify', { operation_id: entry.submitted_save.operation_id }); entry.unknown_save_result = resolved; const operation = evidence.operations.find(record => record.operation_id === entry.submitted_save.operation_id); if (operation) { operation.resolution = resolved; operation.status = resolved.status }
                entry.expected_queue_count = resolved.status === 'COMMITTED' ? 0 : 1 }
            }
            entry.queue_reset = await resetQueue(page, entry.expected_queue_count)
            if (modifiedPlan) {
              entry.phase = 'restore_exact_original_source_outside_timer'
              entry.restoration = await apiSave(page, { kind: 'replace_plan', plan: source.plan }, 'explicit_original_source_restoration', source.plan)
            }
            entry.phase = 'fresh_full_source_after_cleanup'; assertState(await readState(page), initialState)
          }
          const active = await activeJobs(page); assert.equal(active.length, 0, 'A queued/running job remains after cleanup.')
          entry.cleanup = 'complete'
        } catch (error) { failEntry(entry, error); entry.cleanup = 'failed'; blocked = 'A preceding source/queue/job cleanup failed; dependent repeats were not run.'; stopped = true }
        entry.verification_finished_ms = performance.now(); evidence.repeats.push(entry); persist()
        process.stdout.write(JSON.stringify({ operation, repeat, seconds: entry.seconds, status: entry.status, phase: entry.failure_phase ?? entry.phase }) + '\n')
      }
    }
  }
  stopped = true; await Promise.all(loadPromises)
  for (const entry of evidence.repeats) if (mode !== 'solo' && entry.measurement_started) {
    entry.active_editor_contexts = [1, 2, 3, 4].filter(editor => {
      const intervals = evidence.editor_actions.filter(row => row.editor === editor && row.started_ms <= entry.full_render_ms && row.finished_ms >= entry.clicked_ms).sort((a, b) => a.started_ms - b.started_ms)
      let through = entry.clicked_ms
      for (const interval of intervals) { if (interval.started_ms > through + 100) return false; through = Math.max(through, interval.finished_ms) }
      return through >= entry.full_render_ms
    }).length + 1
    if (entry.active_editor_contexts !== 5) failEntry(entry, null, 'Five automated editor action intervals did not cover the complete measured result.')
  }
  evidence.automated_observation = editingProbe ? 'not_run' : evidence.repeats.length === operations.length * 5 && evidence.repeats.every(row => row.status === 'pass') && evidence.editor_actions.every(row => row.status === 'pass') ? 'pass' : 'fail'
} catch (error) { evidence.preflight = { status: 'fail', phase: evidence.phase, reason: evidence.phase?.startsWith('login') ? error.name : String(error.message).split('\n')[0].slice(0, 300) }; process.exitCode = 1 }
finally {
  stopped = true; await Promise.allSettled(loadPromises)
  if (inputEditors && !inputEditors.finished) {
    try { await finishInputEditors() }
    catch (error) { evidence.input_editing_contract.status = 'fail'; evidence.input_editing_contract.cleanup = 'failed'; evidence.input_editing_contract.reason = String(error.message).split('\n')[0].slice(0, 300) }
  }
  if (pages[0]) for (const [id, record] of createdJobs) if (record.cleanup !== 'terminal') {
    try { await finishJob(pages[0], id) } catch (error) { record.cleanup = 'failed'; record.reason = String(error.message).split('\n')[0].slice(0, 300); evidence.automated_observation = 'fail' }
  }
  if (browser) await browser.close(); evidence.client_wrapper_peak_rss_kib = process.resourceUsage().maxRSS; persist()
}
if (editingProbe ? evidence.input_editing_contract?.status !== 'pass' : evidence.automated_observation !== 'pass') process.exitCode = 1
