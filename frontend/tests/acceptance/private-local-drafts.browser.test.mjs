/** Public private-draft workflow regression, never an SLA observation. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { createHash } from 'node:crypto'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, openSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gunzipSync, gzipSync } from 'node:zlib'
import { get as httpsGet } from 'node:https'
import { setTimeout as delay } from 'node:timers/promises'
import { assertPlan, canonical } from './contracts.mjs'
import { assertPrivateCycleBusiness, privateExpectation } from './private-draft-contract.mjs'
import { writeFullPrivateFixture } from './private-full-fixture.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const sha = value => createHash('sha256').update(value).digest('hex')
const enabled = process.env.AUTO_PLAN_PRIVATE_DRAFT_EDITORS_BROWSER === '1'
const fullSource = process.env.AUTO_PLAN_PRIVATE_DRAFT_FULL_R20 === '1'
const sharedSaveProbe = process.env.AUTO_PLAN_PRIVATE_DRAFT_SHARED_SAVE_PROBE === '1'
const primaryJobProbe = sharedSaveProbe || process.env.AUTO_PLAN_PRIVATE_DRAFT_PRIMARY_JOB_PROBE === '1'

assert.ok(!fullSource || !primaryJobProbe, 'Full-r20 job and shared-save coverage need separate qualification.')

function fixture(directory) {
  if (fullSource) return writeFullPrivateFixture(root, directory)
  const frozen = resolve(root, 'docs/acceptance/frozen')
  const originalBytes = readFileSync(resolve(frozen, 'manifest.json'))
  const originalManifest = JSON.parse(originalBytes)
  const original = originalManifest.scenarios.find(row => row.id === 'r20-loose')
  const payload = gunzipSync(readFileSync(resolve(frozen, original.source.path)))
  assert.equal(sha(payload), original.source.sha256)
  const source = JSON.parse(payload)
  const models = ['M00', 'M02', 'M04', 'M06', 'M08']
  const lines = ['L00', 'L01', 'L02', 'L03', 'L04']
  const recipes = ['R00', 'R01', 'R02', 'R03', 'R04']
  source.inputs.masters.models = source.inputs.masters.models.filter(row => models.includes(row.code))
  source.inputs.masters.lines = source.inputs.masters.lines.filter(row => lines.includes(row.code))
  source.inputs.masters.recipes = source.inputs.masters.recipes.filter(row => recipes.includes(row.code))
  source.inputs.masters.capabilities = source.inputs.masters.capabilities.filter(row => recipes.includes(row.recipe_code) && lines.includes(row.line_code))
  const parameters = source.inputs.parameters
  parameters.model_yields = parameters.model_yields.filter(row => models.includes(row.model_code))
  parameters.capacities = parameters.capacities.filter(row => recipes.includes(row.recipe_code) && lines.includes(row.line_code))
  parameters.cycle_times = parameters.cycle_times.filter(row => models.includes(row.model_code))
  parameters.switch_durations = parameters.switch_durations.filter(row => recipes.includes(row.from_recipe) && recipes.includes(row.to_recipe))
  source.inputs.business.demands = source.inputs.business.demands.filter(row => models.includes(row.model_code))
  source.plan.segments = source.plan.segments.filter(row => models.includes(row.model_code))
  source.plan.switches = []
  source.plan.initial_states = lines.map((line_code, index) => ({ line_code, recipe_code: recipes[index] }))
  source.plan.ordinary_idle = []; source.plan.current_day_witnesses = []
  source.baseline.plan = structuredClone(source.plan)
  for (const scope of [...source.execution.coverage, ...source.baseline.coverage]) {
    scope.line_codes = [...lines]; scope.model_codes = [...models]
  }
  assert.equal(source.plan.segments.length, 525)
  assert.ok(source.plan.segments.every(row => row.quantity === 100))
  assert.equal(source.inputs.business.demands.length, 75)
  assert.ok(source.inputs.business.demands.every(row => row.quantity === 700))
  const asset = (name, value) => {
    const bytes = Buffer.from(JSON.stringify(value))
    writeFileSync(resolve(directory, name + '.json.gz'), gzipSync(bytes))
    return { path: name + '.json.gz', sha256: sha(bytes) }
  }
  const scenario = { id: sharedSaveProbe ? 'private-draft-shared-save-source-adapter-v1' : primaryJobProbe ? 'private-draft-primary-source-adapter-v1' : 'private-draft-source-adapter-v1', kind: 'browser_private_draft_contract',
    source: asset('source', source), reference: asset('reference', source),
    action: asset('action', { request: { mode: 'all_replan', scope: {
      start_date: '2026-10-12', end_date: '2027-01-24', line_codes: lines, model_codes: models },
      strategy: 'hybrid', time_limit_seconds: primaryJobProbe ? 90 : 5, max_candidates: 1, native_threads: 1 } }),
    expected: { capacity_valid: true, computation_complete: true, executable: true,
      totals: { expected: 52500, on_time: 52500, late: 0, gap: 0, pending: 0, unallocated: 0, outside_window: 0 },
      objective: Array(33).fill('0') },
    predecessor: { manifest_sha256: sha(originalBytes), source_sha256: original.source.sha256 },
    acceptance: 'not_run', purpose: 'Five-model private-draft workflow correctness; no fixed-scale alias.' }
  if (primaryJobProbe) scenario.scope = {
    start_date: '2026-10-12', end_date: '2027-01-24', line_codes: lines, model_codes: models }
  const parent = { schema_version: 1, scenarios: [scenario], gates_seconds: originalManifest.gates_seconds,
    reference_confirmation: { status: 'not_run', reason: 'Correctness fixture, no quality or timing acceptance.' } }
  const parentBytes = Buffer.from(JSON.stringify(parent))
  writeFileSync(resolve(directory, 'predecessor-manifest.json'), parentBytes)
  const successor = { ...structuredClone(scenario), id: sharedSaveProbe ? 'private-draft-shared-save-adapter-v1' : primaryJobProbe ? 'private-draft-primary-job-adapter-v1' : 'private-draft-editors-adapter-v2-shared-M00',
    predecessor: { scenario: scenario.id, manifest: { path: 'predecessor-manifest.json', sha256: sha(parentBytes) } } }
  const declaration = { schema_version: 1, kind: 'private_local_drafts_v1', scenario: successor.id,
    source_manifest_sha256: sha(parentBytes), source_fixture_sha256: scenario.source.sha256,
    reference_sha256: scenario.reference.sha256, action_sha256: scenario.action.sha256,
    targets: models.map((_, editor) => ({ editor, segment_id: sharedSaveProbe && editor > 0 ? 'S-2027-01-24-M02' : 'S-2027-01-24-M00',
      operation: { kind: 'change_quantity', quantity: 99 },
      expected: { quantity: 99, hours_lower_exclusive: '49/50', hours_upper_inclusive: '24',
        totals: { expected: 52499, on_time: 52499, late: 0, gap: 1, pending: 0, unallocated: 0, outside_window: 0 },
        changed_week: { model_code: sharedSaveProbe && editor > 0 ? 'M02' : 'M00', week: '2027-01-18', demand: 700, on_time: 699, gap: 1 } },
      restoration: sharedSaveProbe && editor === 0 ? 'ordinary_uuid_full_original_plan' : 'undo_complete_action_to_origin' })) }
  if (sharedSaveProbe) declaration.shared_save = { kind: 'actual_commit_reply_loss_original_uuid', primary_model: 'M00', background_model: 'M02', revisions: [1,2,3], input_version: 1, latest_adoption: 1, shared_commits: 2, source_total: 52500, saved_total: 52499, background_saved_total: 52498, restore: 'separate_ordinary_uuid_full_original_plan', clock: 'first_native_save_click_through_verified_complete_result_two_frames' }
  let workloadPath = 'editing-workload.json'
  if (sharedSaveProbe) {
    // Preserve the actually observed v1 declaration, including its inconsistent
    // primary undo label, as an immutable predecessor rather than relabel it.
    const observedV1 = structuredClone(declaration)
    for (const target of observedV1.targets) target.restoration = 'undo_complete_action_to_origin'
    const observedBytes = Buffer.from(JSON.stringify(observedV1))
    writeFileSync(resolve(directory, 'editing-workload.json'), observedBytes)
    const observedScenario = { ...structuredClone(successor), editing_workload: { path: 'editing-workload.json', encoding: 'json', sha256: sha(observedBytes) } }
    const observedManifest = Buffer.from(JSON.stringify({ ...parent, scenarios: [observedScenario] }))
    writeFileSync(resolve(directory, 'predecessor-v1-manifest.json'), observedManifest)
    successor.id = 'private-draft-shared-save-adapter-v2'
    successor.predecessor = { scenario: observedScenario.id, manifest: { path: 'predecessor-v1-manifest.json', sha256: sha(observedManifest) } }
    declaration.scenario = successor.id; declaration.source_manifest_sha256 = sha(observedManifest)
    for (const target of declaration.targets) target.restoration = target.editor === 0 ? 'ordinary_uuid_full_original_plan' : 'undo_complete_action_to_actual_shared_epoch'
    workloadPath = 'editing-workload-v2.json'
  }
  const bytes = Buffer.from(JSON.stringify(declaration))
  writeFileSync(resolve(directory, workloadPath), bytes)
  successor.editing_workload = { path: workloadPath, encoding: 'json', sha256: sha(bytes) }
  writeFileSync(resolve(directory, 'manifest.json'), JSON.stringify({ ...parent, scenarios: [successor] }))
  return { scenario: successor, declaration, source }
}

function healthy(url) {
  return new Promise(done => {
    const request = httpsGet(url, { rejectUnauthorized: false, timeout: 1000 }, response => {
      response.resume(); done(response.statusCode === 200)
    })
    request.on('timeout', () => request.destroy()); request.on('error', () => done(false))
  })
}

async function stop(child, finished, exited) {
  if (!exited() && child.pid) {
    try { process.kill(-child.pid, 'SIGTERM') } catch (error) { if (error.code !== 'ESRCH') throw error }
    await Promise.race([finished, delay(3000)])
    if (!exited()) {
      try { process.kill(-child.pid, 'SIGKILL') } catch (error) { if (error.code !== 'ESRCH') throw error }
      await finished
    }
  }
}

test(fullSource ? 'five browsers privately edit and undo the full canonical r20 source' : primaryJobProbe ? 'the primary genuinely edits during its accepted running job while four private editors continue' : 'five browsers change private production quantities and undo without shared writes',
  { skip: !enabled && 'Set AUTO_PLAN_PRIVATE_DRAFT_EDITORS_BROWSER=1.', timeout: primaryJobProbe ? 240000 : 180000 }, async () => {
    const python = process.env.AUTO_PLAN_PRIVATE_DRAFT_EDITORS_PYTHON
    assert.ok(python, 'Provide the already installed test Python interpreter.')
    const port = Number(process.env.AUTO_PLAN_PRIVATE_DRAFT_EDITORS_PORT ?? '18869')
    const output = mkdtempSync(resolve(tmpdir(), 'auto-plan-private-draft-editors-'))
    const fixtures = resolve(output, 'fixtures'); mkdirSync(fixtures)
    const { scenario, declaration, source } = fixture(fixtures)
    const config = { origin: 'https://localhost:' + port, base_path: '/planning', fixtures,
      target: { kind: 'adapter', disposable: true, production: false }, scenario: scenario.id,
      bindings: { source_revision: 1, input_version: 1, latest_adoption: 1 }, python,
      allow_test_certificate: true, environment: {}, primary_segment: declaration.targets[0].segment_id,
      editing_workload: { path: resolve(fixtures, scenario.editing_workload.path), sha256: scenario.editing_workload.sha256 } }
    const configPath = resolve(output, 'config.json'); writeFileSync(configPath, JSON.stringify(config))
    const env = { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('AUTO_PLAN_'))), PYTHONPATH: 'src:.' }
    const serverLog = openSync(resolve(output, 'server.log'), 'w')
    const server = spawn(python, ['-m', 'scripts.acceptance.adapter_server', '--fixtures', fixtures,
      '--scenario', scenario.id, '--port', String(port)], { cwd: root, detached: true, stdio: ['ignore', serverLog, serverLog], env })
    let serverExited = false
    const serverFinished = once(server, 'exit').then(value => { serverExited = true; return value })
    try {
      const readyBy = Date.now() + 30000
      while (!serverExited && Date.now() < readyBy && !await healthy(config.origin + config.base_path + '/api/ready')) await delay(100)
      assert.ok(!serverExited && await healthy(config.origin + config.base_path + '/api/ready'), 'Adapter did not become ready: ' + output)
      const runnerLog = openSync(resolve(output, 'runner.log'), 'w')
      const child = spawn(process.execPath, [resolve(root, 'frontend/tests/acceptance/run.mjs'), '--config', configPath,
        '--output', resolve(output, 'run'), '--mode', 'five_automated_editors_one_computation',
        '--local-editing-contract-probe', 'true', ...(primaryJobProbe ? ['--private-primary-job-probe', 'true'] : []), ...(sharedSaveProbe ? ['--private-shared-save-probe', 'true'] : [])], { cwd: root, detached: true, stdio: ['pipe', runnerLog, runnerLog], env })
      writeFileSync(resolve(output, 'owned-processes.json'), JSON.stringify({ controller_pid: process.pid,
        server_pid: server.pid, server_pgid: server.pid, runner_pid: child.pid, runner_pgid: child.pid }))
      child.stdin.end(JSON.stringify({ accounts: Array.from({ length: 5 }, (_, index) => ({ username: 'ACCEPTANCE' + index, password: 'company-secret' })) }))
      let exited = false
      const finished = once(child, 'exit').then(value => { exited = true; return value })
      try {
        const until = Date.now() + (primaryJobProbe ? 210000 : 150000)
        while (!exited && Date.now() < until) await delay(100)
        assert.ok(exited, 'Private editing workflow did not finish: ' + output)
        const [code, signal] = await finished
        const diagnostic = readFileSync(resolve(output, 'runner.log'), 'utf8')
        assert.equal(signal, null)
        assert.equal(code, 0, 'Approved private-draft workload was rejected or failed: ' + diagnostic + '\nArtifacts: ' + output)
        const report = JSON.parse(readFileSync(resolve(output, 'run/report.json')))
        assert.equal(report.local_editing_contract?.status, 'pass')
        assert.equal(report.acceptance, 'not_run'); assert.deepEqual(report.repeats, [])
        const captured = artifact => {
          const bytes = readFileSync(resolve(output, 'run', artifact.path))
          assert.equal(sha(bytes), artifact.sha256, 'Captured public response bytes differ.')
          return JSON.parse(bytes)
        }
        if (sharedSaveProbe && report.private_shared_save) {
          const save = report.private_shared_save, job = report.private_primary_job
          assert.equal(save.status, 'pass'); assert.equal(save.acknowledgment_status, null)
          assert.equal(save.readonly_unknown_observed, true)
          assert.equal(save.resolution.status, 'COMMITTED'); assert.equal(save.resolution.revision, 2)
          assert.deepEqual(save.verification_command, { operation_id: save.submitted.operation_id })
          assert.equal(save.clock.origin_epoch_ms, save.clock.first_clicked_epoch_ms)
          assert.ok(save.clock.rendered_epoch_ms > save.clock.origin_epoch_ms)
          const before = captured(save.epochs.before.source_artifact), saved = captured(save.epochs.saved.source_artifact), restored = captured(save.epochs.restored.source_artifact)
          assert.equal(before.source_revision, 1); assert.equal(saved.source_revision, 2); assert.equal(restored.source_revision, 3)
          assertPlan(before.snapshot.plan, source.plan)
          assert.deepEqual(restored, { ...before, source_revision: 3 })
          const expectedSaved = structuredClone(source.plan), actualSaved = structuredClone(saved.snapshot.plan)
          expectedSaved.segments.find(row => row.id === 'S-2027-01-24-M00').quantity = 99
          const witness = plan => {
            const row = plan.segments.find(row => row.id === 'S-2027-01-24-M00')
            const [n,d] = canonical({ hours: row.hours }).hours.split('/').map(BigInt)
            assert.ok(100n*n > 98n*d && n <= 24n*d); delete row.hours
          }
          witness(actualSaved); delete expectedSaved.segments.find(row => row.id === 'S-2027-01-24-M00').hours
          assertPlan(actualSaved, expectedSaved)
          assert.deepEqual(saved, { ...before, source_revision: 2, snapshot: { ...before.snapshot, plan: saved.snapshot.plan } })
          assertPlan(captured(save.epochs.saved.revision_artifact).snapshot.plan, saved.snapshot.plan)
          assertPlan(captured(save.epochs.restored.revision_artifact).snapshot.plan, source.plan)
          assert.deepEqual(captured(save.epochs.saved.revision_artifact).operations, save.submitted.operations)
          assert.equal(save.restoration.result.revision, 3)
          assert.notEqual(save.restoration.submitted.operation_id, save.submitted.operation_id)
          assert.equal(report.operations.length, 2)
          assert.ok(report.operations.every(row => row.status === 'COMMITTED'))
          assert.equal(report.local_editing_contract.shared_source_restored, true)
          const epoch = revision => { assert.ok([1,2,3].includes(revision)); return revision === 2 ? saved.snapshot.plan : source.plan }
          for (let editor = 1; editor < 5; editor++) {
            const cycles = report.editor_actions.filter(row => row.editor === editor)
            assert.ok(cycles.length >= 2 && cycles.every(row => row.status === 'pass'))
            for (const cycle of cycles) {
              assert.equal(cycle.segment_id, 'S-2027-01-24-M02')
              const changed = captured(cycle.changed_response_artifact), undo = captured(cycle.undo.response_artifact)
              const expected = structuredClone(epoch(changed.parent_revision)), actual = structuredClone(changed.plan)
              expected.segments.find(row => row.id === cycle.segment_id).quantity = 99
              delete expected.segments.find(row => row.id === cycle.segment_id).hours
              const selected = actual.segments.find(row => row.id === cycle.segment_id)
              const [n,d] = canonical({ hours: selected.hours }).hours.split('/').map(BigInt)
              assert.ok(100n*n > 98n*d && n <= 24n*d); delete selected.hours
              assertPlan(actual, expected); assertPlan(undo.plan, epoch(undo.parent_revision))
              assert.deepEqual(changed.snapshot, { ...before.snapshot, plan: changed.plan })
              assert.deepEqual(undo.snapshot, { ...before.snapshot, plan: undo.plan })
              assert.equal(cycle.undo.queue_empty, true)
            }
            assert.ok(cycles.some(row => row.undo_parent_revision === 3))
            const events = cycles.flatMap(row => row.events)
            assert.ok(Math.min(...events.filter(row => row.phase === 'native_quantity_submit').map(row => row.epoch_ms)) <= save.clock.origin_epoch_ms)
            assert.ok(Math.max(...events.filter(row => row.phase === 'complete_undo').map(row => row.epoch_ms)) >= save.clock.rendered_epoch_ms)
          }
          for (const observed of [save.running_before_save, save.running_after_confirmed_paint]) {
            assert.equal(observed.body.state, 'running'); assert.equal(observed.body.job_id, job.accepted.job_id)
          }
          assert.equal(save.running_before_save.body.generation, save.running_after_confirmed_paint.body.generation)
          assert.ok(save.running_before_save.epoch_ms <= save.clock.origin_epoch_ms && save.running_after_confirmed_paint.epoch_ms >= save.clock.rendered_epoch_ms)
          const frozen = captured(job.source_artifact), view = captured(job.candidate_artifact)
          assert.deepEqual(frozen, before); assert.deepEqual(view.candidate.snapshot, before.snapshot)
          assert.equal(view.job_id, job.accepted.job_id); assert.equal(view.candidate.source_revision, 1)
          assert.deepEqual(view.candidate.objective, Array(33).fill('0')); assert.deepEqual(job.objective, Array(33).fill('0'))
          assert.ok(['completed','stopped'].includes(job.cleanup.state))
          return
        }
        for (const target of declaration.targets) {
          const cycle = report.editor_actions.find(row => row.editor === target.editor && row.status === 'pass')
          assert.ok(cycle, 'A declared actor did not complete a genuine local edit: ' + output)
          assert.equal(cycle.segment_id, target.segment_id)
          assert.equal(cycle.before_quantity, 100); assert.equal(cycle.changed_quantity, 99)
          assert.deepEqual(cycle.observed_totals, target.expected.totals)
          assert.equal(cycle.undo.queue_empty, true); assert.equal(cycle.undo.origin_restored, true)
          assertPrivateCycleBusiness({ source, target,
            baseline: captured(report.local_editing_contract.shared_before_source_artifact).snapshot,
            changed: captured(cycle.changed_response_artifact), undo: captured(cycle.undo.response_artifact),
            expectation: privateExpectation(source, target, scenario.expected) })
        }
        assert.ok(report.request_spans.every(row => typeof row.method === 'string'), 'Request methods are required for the no-write proof.')
        for (let editor = 0; editor < 5; editor++) assert.ok(report.request_spans.filter(row => row.editor === editor && row.method === 'POST' && row.path.endsWith('/plans/preview')).length >= 2)
        const writes = report.request_spans.filter(row => row.method === 'POST' && /\/(?:plans\/(?:save|adopt)|inputs\/apply)$/.test(row.path))
        assert.deepEqual(writes, [], 'Private drafts unexpectedly changed shared state.')
        assert.equal(report.local_editing_contract.shared_source_unchanged, true)
        const before = captured(report.local_editing_contract.shared_before_source_artifact)
        const after = captured(report.local_editing_contract.shared_after_source_artifact)
        assert.deepEqual(after, before, 'Private editing changed the complete public computation source.')
        assert.equal(before.source_revision, 1); assert.equal(before.input_version, 1); assert.equal(before.latest_adoption, 1)
        assertPlan(before.snapshot.plan, source.plan)
        if (primaryJobProbe) {
          const job = report.private_primary_job
          assert.ok(job?.accepted, 'The requested primary computation never became a real accepted job: ' + output)
          assert.ok(job.attempts.length > 0)
          const accepted = job.attempts.find(row => row.response_status === 200)
          assert.ok(accepted, 'No public job acceptance was captured.')
          assert.match(accepted.command.submission_id, /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i)
          for (const attempt of job.attempts) {
            assert.ok(['ACCEPTED', 'ABORTED_VOID'].includes(attempt.resolution.status))
            assert.equal(attempt.command.submission_id, attempt.resolution.submission_id)
            if (attempt.response_status === 409) assert.equal(attempt.resolution.status, 'ABORTED_VOID')
          }
          assert.equal(accepted.resolution.status, 'ACCEPTED')
          assert.equal(accepted.resolution.job_id, job.accepted.job_id)
          const action = JSON.parse(gunzipSync(readFileSync(resolve(fixtures, scenario.action.path))))
          assert.deepEqual(accepted.command.request, action.request)
          assert.equal(accepted.command.base_revision, before.source_revision)
          assert.equal(accepted.command.input_version, before.input_version)
          assert.equal(accepted.command.latest_adoption, before.latest_adoption)
          assert.equal(accepted.command.reviewed_as_of, before.snapshot.time.as_of)
          const primary = report.editor_actions.find(row => row.editor === 0 && row.status === 'pass')
          assert.ok(primary?.events?.length > 0, 'Primary editing needs real phase events.')
          const edit = primary.events.find(row => row.phase === 'native_quantity_submit')
          const undo = primary.events.find(row => row.phase === 'complete_undo')
          assert.ok(edit && undo && edit.epoch_ms < undo.epoch_ms)
          const runningBefore = job.running_before_primary_edit, runningAfter = job.running_after_primary_undo
          for (const observed of [runningBefore, runningAfter]) {
            assert.equal(observed.body.job_id, job.accepted.job_id)
            assert.equal(observed.body.state, 'running', 'A real running job must cover the complete primary edit and undo.')
          }
          for (const key of ['generation', 'source_revision', 'input_version', 'latest_adoption', 'started_at']) assert.deepEqual(runningAfter.body[key], runningBefore.body[key])
          assert.ok(runningBefore.epoch_ms <= edit.epoch_ms && runningAfter.epoch_ms >= undo.epoch_ms)
          assert.equal(job.clock.origin_epoch_ms, job.clock.first_clicked_epoch_ms)
          assert.equal(job.clock.origin_epoch_ms, job.attempts[0].clicked_epoch_ms)
          assert.ok(job.clock.origin_epoch_ms < edit.epoch_ms && job.clock.rendered_epoch_ms > undo.epoch_ms)
          for (let editor = 1; editor < 5; editor++) {
            const cycles = report.editor_actions.filter(row => row.editor === editor)
            assert.ok(cycles.every(row => row.status === 'pass'))
            const clicks = cycles.flatMap(row => row.events).filter(row => row.phase === 'native_quantity_submit')
            const undos = cycles.flatMap(row => row.events).filter(row => row.phase === 'complete_undo')
            assert.ok(Math.min(...clicks.map(row => row.epoch_ms)) <= job.clock.origin_epoch_ms)
            assert.ok(Math.max(...undos.map(row => row.epoch_ms)) >= job.clock.rendered_epoch_ms)
          }
          const frozen = captured(job.source_artifact)
          assert.deepEqual(frozen, before, 'Accepted independent public source changed during private editing.')
          const view = captured(job.candidate_artifact), candidate = view.candidate
          assert.equal(view.job_id, job.accepted.job_id)
          assert.deepEqual(candidate.snapshot, frozen.snapshot)
          assert.equal(candidate.source_revision, 1); assert.equal(candidate.input_version, 1); assert.equal(candidate.latest_adoption, 1)
          assert.deepEqual(candidate.request, action.request)
          assert.deepEqual(candidate.objective, Array(33).fill('0'))
          assert.deepEqual(job.objective, Array(33).fill('0'))
          assert.ok(candidate.evaluation.capacity_valid && candidate.evaluation.computation_complete && candidate.evaluation.executable)
          for (const [key, value] of Object.entries(scenario.expected.totals)) assert.equal(candidate.evaluation.output_totals[key], value)
          assert.equal(candidate.evaluation.weekly_results.length, 75)
          assert.ok(candidate.evaluation.weekly_results.every(row => row.demand === 700 && row.on_time === 700 && row.gap === 0 && row.pending === 0 && row.late === 0))
          assert.ok(['completed', 'stopped'].includes(job.cleanup.state))
        }
        if (sharedSaveProbe) {
          const save = report.private_shared_save
          assert.ok(save?.submitted?.operation_id, 'The requested real shared save never submitted an original operation UUID: ' + output)
          assert.equal(save.acknowledgment_status, null, 'The original lost reply must remain unknown.')
          assert.equal(save.resolution?.status, 'COMMITTED')
          assert.equal(save.verification_command.operation_id, save.submitted.operation_id)
          assert.equal(save.epochs?.saved?.revision, 2)
          assert.equal(save.epochs?.restored?.revision, 3)
        }
      } finally { await stop(child, finished, () => exited) }
    } finally { await stop(server, serverFinished, () => serverExited) }
  })
