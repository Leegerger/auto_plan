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

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const sha = value => createHash('sha256').update(value).digest('hex')
const enabled = process.env.AUTO_PLAN_PRIVATE_DRAFT_EDITORS_BROWSER === '1'

function fixture(directory) {
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
  const scenario = { id: 'private-draft-source-adapter-v1', kind: 'browser_private_draft_contract',
    source: asset('source', source), reference: asset('reference', source),
    action: asset('action', { request: { mode: 'all_replan', scope: {
      start_date: '2026-10-12', end_date: '2027-01-24', line_codes: lines, model_codes: models },
      strategy: 'hybrid', time_limit_seconds: 5, max_candidates: 1, native_threads: 1 } }),
    expected: { capacity_valid: true, computation_complete: true, executable: true,
      totals: { expected: 52500, on_time: 52500, late: 0, gap: 0, pending: 0, unallocated: 0, outside_window: 0 },
      objective: Array(33).fill('0') },
    predecessor: { manifest_sha256: sha(originalBytes), source_sha256: original.source.sha256 },
    acceptance: 'not_run', purpose: 'Five-model private-draft workflow correctness; no fixed-scale alias.' }
  const parent = { schema_version: 1, scenarios: [scenario], gates_seconds: originalManifest.gates_seconds,
    reference_confirmation: { status: 'not_run', reason: 'Correctness fixture, no quality or timing acceptance.' } }
  const parentBytes = Buffer.from(JSON.stringify(parent))
  writeFileSync(resolve(directory, 'predecessor-manifest.json'), parentBytes)
  const successor = { ...structuredClone(scenario), id: 'private-draft-editors-adapter-v2-shared-M00',
    predecessor: { scenario: scenario.id, manifest: { path: 'predecessor-manifest.json', sha256: sha(parentBytes) } } }
  const declaration = { schema_version: 1, kind: 'private_local_drafts_v1', scenario: successor.id,
    source_manifest_sha256: sha(parentBytes), source_fixture_sha256: scenario.source.sha256,
    reference_sha256: scenario.reference.sha256, action_sha256: scenario.action.sha256,
    targets: models.map((_, editor) => ({ editor, segment_id: 'S-2027-01-24-M00',
      operation: { kind: 'change_quantity', quantity: 99 },
      expected: { quantity: 99, hours_lower_exclusive: '49/50', hours_upper_inclusive: '24',
        totals: { expected: 52499, on_time: 52499, late: 0, gap: 1, pending: 0, unallocated: 0, outside_window: 0 },
        changed_week: { model_code: 'M00', week: '2027-01-18', demand: 700, on_time: 699, gap: 1 } },
      restoration: 'undo_complete_action_to_origin' })) }
  const bytes = Buffer.from(JSON.stringify(declaration))
  writeFileSync(resolve(directory, 'editing-workload.json'), bytes)
  successor.editing_workload = { path: 'editing-workload.json', encoding: 'json', sha256: sha(bytes) }
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

test('five browsers change private production quantities and undo without shared writes',
  { skip: !enabled && 'Set AUTO_PLAN_PRIVATE_DRAFT_EDITORS_BROWSER=1.', timeout: 180000 }, async () => {
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
      editing_workload: { path: resolve(fixtures, 'editing-workload.json'), sha256: scenario.editing_workload.sha256 } }
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
        '--local-editing-contract-probe', 'true'], { cwd: root, detached: true, stdio: ['pipe', runnerLog, runnerLog], env })
      writeFileSync(resolve(output, 'owned-processes.json'), JSON.stringify({ controller_pid: process.pid,
        server_pid: server.pid, server_pgid: server.pid, runner_pid: child.pid, runner_pgid: child.pid }))
      child.stdin.end(JSON.stringify({ accounts: Array.from({ length: 5 }, (_, index) => ({ username: 'ACCEPTANCE' + index, password: 'company-secret' })) }))
      let exited = false
      const finished = once(child, 'exit').then(value => { exited = true; return value })
      try {
        const until = Date.now() + 150000
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
        for (const target of declaration.targets) {
          const cycle = report.editor_actions.find(row => row.editor === target.editor && row.status === 'pass')
          assert.ok(cycle, 'A declared actor did not complete a genuine local edit: ' + output)
          assert.equal(cycle.segment_id, target.segment_id)
          assert.equal(cycle.before_quantity, 100); assert.equal(cycle.changed_quantity, 99)
          assert.deepEqual(cycle.observed_totals, target.expected.totals)
          assert.equal(cycle.undo.queue_empty, true); assert.equal(cycle.undo.origin_restored, true)
          const changed = captured(cycle.changed_response_artifact)
          const expectedPlan = structuredClone(source.plan)
          const expectedRow = expectedPlan.segments.find(row => row.id === target.segment_id)
          expectedRow.quantity = 99; delete expectedRow.hours
          const actualPlan = structuredClone(changed.plan), actualRow = actualPlan.segments.find(row => row.id === target.segment_id)
          const [numerator, denominator] = canonical({ hours: actualRow.hours }).hours.split('/').map(BigInt)
          assert.ok(100n * numerator > 98n * denominator && numerator <= 24n * denominator, 'The selected exact witness violates its independently derived strict capacity range.')
          delete actualRow.hours; assertPlan(actualPlan, expectedPlan)
          assert.deepEqual(Object.fromEntries(Object.keys(target.expected.totals).map(key => [key, changed.evaluation.output_totals[key]])), target.expected.totals)
          assert.equal(changed.evaluation.output_totals.computation_complete, true)
          assert.equal(changed.evaluation.weekly_results.length, 75)
          for (const row of changed.evaluation.weekly_results) {
            const affected = row.model_code === target.expected.changed_week.model_code && row.week === target.expected.changed_week.week
            assert.equal(row.demand, 700); assert.equal(row.on_time, affected ? 699 : 700)
            assert.equal(row.gap, affected ? 1 : 0); assert.equal(row.pending, 0); assert.equal(row.late, 0)
          }
          assertPlan(captured(cycle.undo.response_artifact).plan, source.plan)
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
      } finally { await stop(child, finished, () => exited) }
    } finally { await stop(server, serverFinished, () => serverExited) }
  })
