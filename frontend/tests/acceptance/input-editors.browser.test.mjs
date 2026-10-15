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
  const scenario = { id: 'input-editors-adapter-v1', kind: 'browser_editing_contract',
    source: freeze('source', source), reference: freeze('reference', source),
    action: freeze('action', { request: { mode: 'all_replan', scope: { start_date: '2026-10-12', end_date: '2027-01-24', line_codes: lines, model_codes: models },
      strategy: 'hybrid', time_limit_seconds: 90, max_candidates: 1, native_threads: 1 } }),
    expected: { objective: Array(33).fill('0') },
    predecessor: { manifest_sha256: sha(predecessorManifest), source_sha256: original.source.sha256 },
    acceptance: 'not_run', purpose: 'Five-model public editing regression; not a fixed-scale acceptance family.' }
  const manifest = { schema_version: 1, scenarios: [scenario], gates_seconds: originalManifest.gates_seconds,
    reference_confirmation: { status: 'not_run', reason: 'Correctness adapter; no user quality or timing acceptance.' } }
  const manifestBytes = Buffer.from(JSON.stringify(manifest))
  writeFileSync(resolve(directory, 'manifest.json'), manifestBytes)
  return { initial: source.inputs, scenario, manifestBytes }
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

test(primaryPinProbe ? 'a primary preview retains its accepted input pin and original retry clock while editors continue' : 'four browser editors commit existing names through the Inputs workflow',
  { skip: !enabled && 'Set AUTO_PLAN_INPUT_EDITORS_BROWSER=1 for the owned HTTPS adapter.', timeout: 180000 }, async () => {
    const python = process.env.AUTO_PLAN_INPUT_EDITORS_PYTHON
    assert.ok(python, 'Provide the already installed test Python interpreter.')
    const port = Number(process.env.AUTO_PLAN_INPUT_EDITORS_PORT ?? '18869')
    assert.ok(Number.isInteger(port) && port >= 1024 && port < 65536)
    const output = mkdtempSync(resolve(tmpdir(), 'auto-plan-input-editors-contract-'))
    const fixtures = resolve(output, 'fixtures'); mkdirSync(fixtures)
    const { initial, scenario, manifestBytes } = fixture(fixtures)
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
      scenario: scenario.id, source_manifest_sha256: sha(manifestBytes), source_fixture_sha256: scenario.source.sha256,
      source_inputs_sha256: inputContentDigest(initial), targets,
      states: Array.from({ length: 16 }, (_, mask) => {
        const inputs = structuredClone(initial)
        for (const [index, target] of targets.entries()) inputs.masters.models.find(row => row.code === target.code).name = target.names[(mask >> index) & 1]
        return { id: String(mask), inputs, inputs_sha256: inputContentDigest(inputs) }
      }) }
    const companionPath = resolve(output, 'companion.json'), childConfigPath = resolve(output, 'config.json')
    writeFileSync(companionPath, JSON.stringify(companion) + '\n')
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
      '--editing-contract-probe', 'true', ...(primaryPinProbe ? ['--primary-pin-probe', 'true'] : [])], { cwd: root, detached: true, stdio: ['pipe', log, log],
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
      assert.notEqual(value?.preflight?.status, 'fail', 'The adapter preflight failed before the editing behavior: ' + output)
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
      report = JSON.parse(readFileSync(reportPath))
      assertNoLegacy(report)
      assert.equal(signal, null); assert.equal(code, 0, 'The editor probe failed; retained artifacts: ' + output)
      assert.equal(report.acceptance, 'not_run')
      assert.deepEqual(report.repeats, [], 'A focused editing probe must not fabricate timed repeats.')
      assert.equal(report.input_editing_contract?.status, 'pass')
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
      assert.equal(inputContentDigest(report.input_editing_contract.restored_inputs), inputContentDigest(initial))
      assert.equal(report.input_editing_contract.cleanup, 'complete')
    } finally {
      await stop(child, finished, () => exited)
      closeSync(log)
    }
    } finally { await stop(server, serverFinished, () => serverExited); closeSync(serverLog) }
  })
