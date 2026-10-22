/** Public browser/API contract probe; this never establishes an SLA pass. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { createHash } from 'node:crypto'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync, openSync, closeSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gunzipSync, gzipSync } from 'node:zlib'
import { get as httpsGet } from 'node:https'
import { setTimeout as delay } from 'node:timers/promises'
import { INPUT_CONTENT_DIGEST_ALGORITHM, inputContentDigest, resolveInputCompanion } from './input-load.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const enabled = process.env.AUTO_PLAN_INPUT_EDITORS_BROWSER === '1'
const primaryPinProbe = process.env.AUTO_PLAN_INPUT_PRIMARY_PIN_PROBE === '1'
const primaryJobProbe = process.env.AUTO_PLAN_INPUT_PRIMARY_JOB_PROBE === '1'
const outputTextProbe = process.env.AUTO_PLAN_INPUT_OUTPUT_TEXT_PROBE === '1'
const sha = value => createHash('sha256').update(value).digest('hex')

function fixture(directory) {
  const frozen = resolve(root, 'docs/acceptance/frozen')
  const predecessorManifest = readFileSync(resolve(frozen, 'manifest.json'))
  const originalManifest = JSON.parse(predecessorManifest)
  const original = originalManifest.scenarios.find(row => row.id === 'r20-loose')
  const bytes = gunzipSync(readFileSync(resolve(frozen, original.source.path)))
  assert.equal(sha(bytes), original.source.sha256)
  const source = JSON.parse(bytes), models = ['M00', 'M02', 'M04', 'M06', 'M08'], lines = ['L00', 'L01', 'L02', 'L03', 'L04']
  const recipes = ['R00', 'R01', 'R02', 'R03', 'R04']
  const contains = (values, value) => values.includes(value)
  source.inputs.masters.models = source.inputs.masters.models.filter(row => contains(models, row.code))
  source.inputs.masters.lines = source.inputs.masters.lines.filter(row => contains(lines, row.code))
  source.inputs.masters.recipes = source.inputs.masters.recipes.filter(row => contains(recipes, row.code))
  source.inputs.masters.capabilities = source.inputs.masters.capabilities.filter(row => contains(recipes, row.recipe_code) && contains(lines, row.line_code))
  const parameters = source.inputs.parameters
  parameters.line_yields = parameters.line_yields.flatMap(row => row.model_code
    ? contains(models, row.model_code) ? [row] : []
    : models.map(model_code => ({ ...row, model_code })))
  parameters.model_yields = parameters.model_yields.filter(row => contains(models, row.model_code))
  parameters.capacities = parameters.capacities.filter(row => contains(recipes, row.recipe_code) && contains(lines, row.line_code))
  parameters.cycle_times = parameters.cycle_times.filter(row => contains(models, row.model_code))
  parameters.switch_durations = parameters.switch_durations.filter(row => contains(recipes, row.from_recipe) && contains(recipes, row.to_recipe))
  source.inputs.business.demands = source.inputs.business.demands.filter(row => contains(models, row.model_code))
  source.plan.segments = source.plan.segments.filter(row => contains(models, row.model_code))
  source.plan.switches = []
  source.plan.initial_states = lines.map((line_code, index) => ({ line_code, recipe_code: recipes[index] }))
  source.plan.ordinary_idle = []; source.plan.current_day_witnesses = []
  source.baseline.plan = structuredClone(source.plan)
  for (const scope of [...source.execution.coverage, ...source.baseline.coverage]) { scope.line_codes = [...lines]; scope.model_codes = [...models] }
  // Independent literal quantities for this NEW correctness fixture only.
  assert.equal(source.plan.segments.length, 525)
  assert.ok(source.plan.segments.every(row => row.quantity === 100))
  assert.equal(source.inputs.business.demands.length, 75)
  assert.ok(source.inputs.business.demands.every(row => row.quantity === 700))
  assert.equal(source.plan.segments.reduce((sum, row) => sum + row.quantity, 0), 52500)
  const freeze = (name, value) => {
    const payload = Buffer.from(JSON.stringify(value))
    writeFileSync(resolve(directory, name + '.json.gz'), gzipSync(payload))
    return { path: name + '.json.gz', sha256: sha(payload) }
  }
  const scope = { start_date: '2026-10-12', end_date: '2027-01-24', line_codes: lines, model_codes: models }
  const scenario = { id: primaryJobProbe ? 'input-editors-job-adapter-v1' : 'input-editors-adapter-v1', kind: 'browser_editing_contract',
    source: freeze('source', source), reference: freeze('reference', source),
    action: freeze('action', { request: { mode: 'all_replan', scope,
      strategy: 'hybrid', time_limit_seconds: primaryJobProbe ? 5 : 90, max_candidates: 1, native_threads: 1 } }),
    expected: { objective: Array(33).fill('0') },
    predecessor: { manifest_sha256: sha(predecessorManifest), source_sha256: original.source.sha256 },
    acceptance: 'not_run', purpose: 'Five-model public editing regression; not a fixed-scale acceptance family.' }
  if (primaryJobProbe) {
    scenario.scope = scope
    scenario.expected = { capacity_valid: true, computation_complete: true, executable: true,
      totals: { expected: 52500, on_time: 52500, late: 0, gap: 0, pending: 0, unallocated: 0, outside_window: 0 },
      objective: Array(33).fill('0') }
    scenario.purpose = 'Five-model public computation and accepted-pin regression; no fixed-scale timing alias.'
  }
  const manifest = { schema_version: 1, scenarios: [scenario], gates_seconds: originalManifest.gates_seconds,
    reference_confirmation: { status: 'not_run', reason: 'Correctness adapter; no user quality or timing acceptance.' } }
  const predecessorBytes = Buffer.from(JSON.stringify(manifest))
  writeFileSync(resolve(directory, 'predecessor-manifest.json'), predecessorBytes)
  const successor = { ...structuredClone(scenario), id: scenario.id + '-input-names-v1',
    predecessor: { scenario: scenario.id, manifest: { path: 'predecessor-manifest.json', sha256: sha(predecessorBytes) } } }
  return { initial: source.inputs, scenario: successor,
    manifest: { ...manifest, scenarios: [successor] }, predecessorBytes }
}

async function stop(child, finished, exited) {
  if (!exited() && child.pid) {
    try { process.kill(-child.pid, 'SIGTERM') } catch (error) { if (error.code !== 'ESRCH') throw error }
    await Promise.race([finished, delay(3000)])
    if (!exited()) { try { process.kill(-child.pid, 'SIGKILL') } catch (error) { if (error.code !== 'ESRCH') throw error }; await finished }
  }
}

function healthy(url) {
  return new Promise(done => {
    const request = httpsGet(url, { rejectUnauthorized: false, timeout: 1000 }, response => { response.resume(); done(response.statusCode === 200) })
    request.on('timeout', () => request.destroy()); request.on('error', () => done(false))
  })
}

test(outputTextProbe ? 'the public shared output displays literal totals and weekly quantities' : primaryJobProbe ? 'a primary computation retains original UUID outcomes and its accepted input pin while editors continue' : primaryPinProbe ? 'a primary preview retains its accepted input pin and original retry clock while editors continue' : 'four browser editors commit existing names through the Inputs workflow',
  { skip: !enabled && 'Set AUTO_PLAN_INPUT_EDITORS_BROWSER=1 for the owned HTTPS adapter.', timeout: 180000 }, async () => {
    const python = process.env.AUTO_PLAN_INPUT_EDITORS_PYTHON
    assert.ok(python, 'Provide the already installed test Python interpreter.')
    const port = Number(process.env.AUTO_PLAN_INPUT_EDITORS_PORT ?? '18869')
    assert.ok(Number.isInteger(port) && port >= 1024 && port < 65536)
    const output = mkdtempSync(resolve(tmpdir(), 'auto-plan-input-editors-contract-'))
    const fixtures = resolve(output, 'fixtures'); mkdirSync(fixtures)
    const { initial, scenario, manifest, predecessorBytes } = fixture(fixtures)
    const config = { origin: 'https://localhost:' + port, base_path: '/planning', fixtures,
      target: { kind: 'adapter', disposable: true, production: false }, scenario: scenario.id,
      bindings: { source_revision: 1, input_version: 1, latest_adoption: 1 },
      python, allow_test_certificate: true, environment: {} }
    const targets = ['M02', 'M04', 'M06', 'M08'].map((code, index) => {
      const row = initial.masters.models.find(item => item.code === code)
      assert.equal(row.name, code)
      return { editor: index + 1, kind: 'models', code, names: [code, code + ' · editor' + (index + 1)] }
    })
    const companion = { schema_version: 1, kind: 'maintained_input_names', digest_algorithm: INPUT_CONTENT_DIGEST_ALGORITHM,
      scenario: scenario.id, source_manifest_sha256: sha(predecessorBytes), source_fixture_sha256: scenario.source.sha256,
      source_inputs_sha256: inputContentDigest(initial), targets,
      states: Array.from({ length: 16 }, (_, mask) => {
        const inputs = structuredClone(initial)
        for (const [index, target] of targets.entries()) inputs.masters.models.find(row => row.code === target.code).name = target.names[(mask >> index) & 1]
        return { id: String(mask), inputs, inputs_sha256: inputContentDigest(inputs) }
      }) }
    const companionPath = resolve(output, 'companion.json'), childConfigPath = resolve(output, 'config.json')
    writeFileSync(companionPath, JSON.stringify(companion) + '\n')
    scenario.input_companion = { path: '../companion.json', encoding: 'json', sha256: sha(readFileSync(companionPath)) }
    writeFileSync(resolve(fixtures, 'manifest.json'), JSON.stringify(manifest))
    writeFileSync(childConfigPath, JSON.stringify({ ...config,
      primary_segment: 'S-2027-01-24-M00',
      load_segments: targets.map(target => 'S-2027-01-24-' + target.code),
      input_companion: { path: companionPath, sha256: sha(readFileSync(companionPath)) } }) + '\n')
    const env = { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('AUTO_PLAN_'))), PYTHONPATH: 'src:.' }
    const serverLog = openSync(resolve(output, 'server.log'), 'w')
    const server = spawn(python, ['-m', 'scripts.acceptance.adapter_server', '--fixtures', fixtures,
      '--scenario', scenario.id, '--port', String(port)], { cwd: root, detached: true, stdio: ['ignore', serverLog, serverLog], env })
    let serverExited = false
    const serverFinished = once(server, 'exit').then(value => { serverExited = true; return value })
    try {
    const readyBy = Date.now() + 30000
    while (!serverExited && Date.now() < readyBy && !await healthy(config.origin + config.base_path + '/api/ready')) await delay(100)
    assert.ok(!serverExited && await healthy(config.origin + config.base_path + '/api/ready'), 'Owned adapter did not become ready: ' + output)
    // The ignored flag preserves the legacy real behavior for RED. GREEN uses
    // the same input-editing loop as full runs, with no measured primary action.
    const log = openSync(resolve(output, 'runner.log'), 'w')
    const child = spawn(process.execPath, [resolve(root, 'frontend/tests/acceptance/run.mjs'), '--config', childConfigPath,
      '--output', resolve(output, 'run'), '--mode', 'five_automated_editors_one_computation', '--operations', 'all_replan',
      '--editing-contract-probe', 'true', ...(primaryPinProbe ? ['--primary-pin-probe', 'true'] : []),
      ...(primaryJobProbe ? ['--primary-job-pin-probe', 'true'] : []), ...(outputTextProbe ? ['--output-text-probe','true'] : [])], { cwd: root, detached: true, stdio: ['pipe', log, log],
      env })
    writeFileSync(resolve(output, 'owned-processes.json'), JSON.stringify({ controller_pid: process.pid,
      server_pid: server.pid, server_pgid: server.pid, runner_pid: child.pid, runner_pgid: child.pid }) + '\n')
    child.stdin.end(JSON.stringify({ accounts: Array.from({ length: 5 }, (_, index) =>
      ({ username: 'ACCEPTANCE' + index, password: 'company-secret' })) }))
    let exited = false
    const finished = once(child, 'exit').then(value => { exited = true; return value })
    const reportPath = resolve(output, 'run/report.json')
    let report
    const assertNoLegacy = value => {
      const legacy = value?.request_spans?.find(row => row.editor > 0 && row.path.endsWith('/plans/preview'))
      assert.ok(!legacy, 'A background editor used the legacy Plan preview instead of changing its assigned existing input name: ' + output)
    }
    try {
      const until = Date.now() + 150000
      while (!exited && Date.now() < until) {
        if (existsSync(reportPath)) {
          try { report = JSON.parse(readFileSync(reportPath)) } catch {}
          assertNoLegacy(report)
        }
        await delay(100)
      }
      assert.ok(exited, 'The editing-contract probe did not finish: ' + output)
      const [code, signal] = await finished
      assert.ok(existsSync(reportPath), 'The approved companion run ended before reporting its source contract: ' +
        readFileSync(resolve(output, 'runner.log'), 'utf8') + '\nArtifacts: ' + output)
      report = JSON.parse(readFileSync(reportPath))
      assertNoLegacy(report)
      assert.notEqual(report?.preflight?.status, 'fail', 'The workflow failed after runner cleanup; retained report identifies its actual phase: ' + output)
      assert.equal(signal, null); assert.equal(code, 0, 'The editor probe failed; retained artifacts: ' + output)
      assert.equal(report.acceptance, 'not_run')
      assert.deepEqual(report.repeats, [], 'A focused editing probe must not fabricate timed repeats.')
      if(outputTextProbe)assert.deepEqual(report.output_text_contract,{status:'pass',expected:52500,outside_window:0,unallocated:0,
        first_week:{model_code:'M00',week:'2026-10-12',demand:700,on_time:700,gap:0}})
      assert.equal(report.input_editing_contract?.status, 'pass')
      assert.equal(report.input_companion.approval.kind, 'manifest_successor')
      assert.equal(report.input_companion.approval.manifest_sha256, sha(readFileSync(resolve(fixtures, 'manifest.json'))))
      assert.equal(report.input_companion.approval.predecessor_manifest_sha256, sha(predecessorBytes))
      assert.equal(report.input_companion.approval.predecessor_scenario, primaryJobProbe ? 'input-editors-job-adapter-v1' : 'input-editors-adapter-v1')
      assert.ok(report.helper_sha256['scripts/acceptance/input_companion.py'])
      assert.deepEqual([...new Set(report.editor_actions.filter(row => row.commit?.kind === 'modified_input_name').map(row => row.editor))].sort(), [1, 2, 3, 4])
      assert.ok(report.editor_actions.some(row => row.response_status === 409), 'The synchronized first public previews must exercise actual optimistic conflict/reload.')
      const accepted = resolveInputCompanion(companion, initial, report.input_editing_contract.last_modified_inputs,
        { history: report.input_history })
      assert.ok(accepted.history.length >= 4)
      const captured = pointer => {
        assert.match(pointer.path, /^input-bodies\/[a-f0-9]{64}\.json$/)
        const bytes = readFileSync(resolve(output, 'run', pointer.path))
        assert.equal(sha(bytes), pointer.sha256, 'Captured wire body differs from its retained bytes.')
        return JSON.parse(bytes)
      }
      for (const event of report.input_history) {
        assert.deepEqual(captured(event.request_artifact), event.command)
        assert.deepEqual(captured(event.response_artifact), event.after)
      }
      for (const conflict of report.editor_actions.filter(row => row.response_status === 409)) {
        assert.equal(conflict.commit, undefined, 'An optimistic conflict cannot earn modified-edit credit.')
        captured(conflict.request_artifact); captured(conflict.response_artifact)
      }
      if (primaryPinProbe) {
        const primary = report.request_spans.filter(row => row.editor === 0 && row.path.endsWith('/plans/preview'))
        assert.ok(primary.some(row => row.status === 409), 'The primary must actually submit its stale UI input pin and observe the public conflict.')
        assert.ok(primary.some(row => row.status === 200), 'The primary must actually complete a successful preview through the public latest-input retry.')
        const pin = report.primary_pin_probe
        assert.equal(pin.accepted.command.input_version, pin.accepted.response.snapshot.inputs.input_version)
        assert.deepEqual(captured(pin.accepted.request_artifact), pin.accepted.command)
        assert.deepEqual(captured(pin.accepted.response_artifact), pin.accepted.response)
        resolveInputCompanion(companion, initial, pin.accepted.response.snapshot.inputs)
        assert.ok(pin.displayed_reload.head.input_version > pin.accepted.command.input_version,
          'The later displayed source must really advance beyond the accepted primary pin.')
        assert.equal(pin.displayed_reload.head.input_version, pin.displayed_reload.snapshot.inputs.input_version)
        resolveInputCompanion(companion, initial, pin.displayed_reload.snapshot.inputs)
        assert.equal(pin.clock.origin_epoch_ms, pin.clock.first_clicked_epoch_ms)
        assert.ok(pin.clock.retry_clicked_epoch_ms.length >= 1)
        assert.ok(pin.clock.retry_clicked_epoch_ms.every(value => value > pin.clock.origin_epoch_ms))
        assert.ok(pin.clock.rendered_epoch_ms > pin.clock.retry_clicked_epoch_ms.at(-1))
        assert.equal(pin.clock.seconds, (pin.clock.rendered_epoch_ms - pin.clock.origin_epoch_ms) / 1000)
        assert.equal(pin.accepted.response.evaluation.output_totals.expected, 52499)
        assert.equal(pin.accepted.response.evaluation.output_totals.gap, 1)
        const changed = pin.accepted.response.plan.segments.find(row => row.id === 'S-2027-01-24-M00')
        assert.equal(changed.quantity, 99)
        assert.equal(pin.local_cleanup.queue_empty, true)
        assert.equal(pin.local_cleanup.original_plan_restored, true)
      }
      if (primaryJobProbe) {
        assert.ok(report.submissions.some(row => row.purpose === 'primary_job_pin_probe'),
          'The primary must actually submit a computation while four real editors continue, not finish as an editing-only probe: ' + output)
        const job = report.primary_job_probe
        assert.ok(job.attempts.some(row => row.response_status === 409), 'The stale primary UI must really exercise a rejected job submission.')
        assert.ok(job.attempts.some(row => row.response_status === 200), 'The primary must really obtain one accepted computation.')
        for (const attempt of job.attempts) {
          assert.deepEqual(captured(attempt.request_artifact), attempt.command)
          assert.deepEqual(captured(attempt.response_artifact), attempt.response)
          assert.equal(attempt.resolution.submission_id, attempt.command.submission_id)
          assert.ok(['ACCEPTED', 'ABORTED_VOID'].includes(attempt.resolution.status), 'Every original job UUID must settle before a fresh explicit retry.')
        }
        const accepted = job.attempts.filter(row => row.resolution.status === 'ACCEPTED')
        assert.equal(accepted.length, 1, 'The workflow must own exactly one accepted computation.')
        assert.equal(job.candidate.source_revision, accepted[0].command.base_revision)
        assert.equal(job.candidate.input_version, accepted[0].command.input_version)
        assert.equal(job.candidate.snapshot.inputs.input_version, accepted[0].command.input_version)
        assert.equal(job.candidate.latest_adoption, accepted[0].command.latest_adoption)
        const sourceBody=captured(job.source.artifact)
        assert.equal(job.source.kind,'public_computation_source');assert.equal(job.source.status,200)
        assert.deepEqual(sourceBody.snapshot,job.source.snapshot)
        assert.equal(sourceBody.source_revision,accepted[0].command.base_revision)
        assert.equal(sourceBody.input_version,accepted[0].command.input_version)
        assert.equal(sourceBody.latest_adoption,accepted[0].command.latest_adoption)
        assert.equal(sourceBody.snapshot.time.as_of,accepted[0].command.reviewed_as_of)
        assert.deepEqual(job.candidate.snapshot,sourceBody.snapshot)
        assert.equal(job.source.head.revision, accepted[0].command.base_revision)
        assert.equal(job.source.head.latest_adoption, accepted[0].command.latest_adoption)
        assert.ok(job.source.head.input_version >= accepted[0].command.input_version)
        resolveInputCompanion(companion, initial, job.source.snapshot.inputs)
        assert.ok(job.displayed_reload.head.input_version > accepted[0].command.input_version)
        assert.deepEqual(job.objective, Array(33).fill('0'))
        for (const key of ['capacity_valid', 'computation_complete', 'executable']) assert.equal(job.candidate.evaluation[key], true)
        assert.equal(job.candidate.evaluation.output_totals.expected, 52500)
        assert.equal(job.candidate.evaluation.output_totals.on_time, 52500)
        assert.equal(job.candidate.evaluation.output_totals.gap, 0)
        assert.equal(job.clock.origin_epoch_ms, job.clock.first_clicked_epoch_ms)
        assert.ok(job.clock.retry_clicked_epoch_ms.length >= 1)
        assert.ok(job.clock.retry_clicked_epoch_ms.every(value => value > job.clock.origin_epoch_ms))
        assert.ok(job.clock.rendered_epoch_ms > job.clock.retry_clicked_epoch_ms.at(-1))
        assert.equal(job.clock.seconds, (job.clock.rendered_epoch_ms - job.clock.origin_epoch_ms) / 1000)
        assert.ok(['completed', 'stopped', 'interrupted', 'failed'].includes(job.cleanup.state))
        assert.equal(job.local_queue_empty, true)
      }
      assert.equal(inputContentDigest(report.input_editing_contract.restored_inputs), inputContentDigest(initial))
      assert.equal(report.input_editing_contract.cleanup, 'complete')
    } finally {
      await stop(child, finished, () => exited)
      closeSync(log)
    }
    } finally { await stop(server, serverFinished, () => serverExited); closeSync(serverLog) }
  })
