import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { gunzipSync } from 'node:zlib'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { captureExpectedState } from './expected-source.mjs'
import { canonical } from './contracts.mjs'

const root = resolve(fileURLToPath(new URL('../../..', import.meta.url)))
const frozen = resolve(root, 'docs/acceptance/frozen')
const scenario = JSON.parse(readFileSync(resolve(frozen, 'manifest.json'))).scenarios.find(row => row.id === 'r20-loose')
function rawState() {
  const source = JSON.parse(gunzipSync(readFileSync(resolve(frozen, scenario.source.path))))
  const plan = structuredClone(source.plan); delete plan.current_day_witnesses
  return { head: { revision: 1, input_version: 1, latest_adoption: 1 },
    adopted: { snapshot: { plan: structuredClone(plan) } }, snapshot: { ...source, baseline: { ...source.baseline, plan } } }
}
const hash = bytes => createHash('sha256').update(bytes).digest('hex')

test('independent expected source materializes typed defaults while preserving raw adoption bytes and pins', async () => {
  const output = mkdtempSync(resolve(tmpdir(), 'auto-plan-expected-source-'))
  try {
    const raw = rawState(), original = structuredClone(raw)
    let eventLoopAdvanced = false
    const pendingTick = new Promise(done => setTimeout(() => { eventLoopAdvanced = true; done() }, 0))
    const captured = await captureExpectedState(raw, { root, output, stem: 'read', python: process.env.AUTO_PLAN_ACCEPTANCE_PYTHON ?? 'python' })
    assert.equal(eventLoopAdvanced, true, 'Expected-source materialization must not block other editor contexts.')
    await pendingTick
    assert.equal(captured.capture.schema.model_module, resolve(root, 'src/auto_plan/plan_models.py'))
    assert.equal(captured.capture.schema.model_sha256, hash(readFileSync(resolve(root, 'src/auto_plan/plan_models.py'))))
    assert.deepEqual(raw, original)
    const expected = structuredClone(original.snapshot)
    expected.plan.current_day_witnesses = []
    expected.baseline.plan.current_day_witnesses = []
    Object.assign(expected.execution, { association_cells: [], associations: [], audit: [], day_capacities: [] })
    expected.preferences = { near_weeks: 4, near_tolerance_percent: '10', far_tolerance_percent: '30', near_week_weight: '3', far_week_weight: '1' }
    assert.deepEqual(canonical(captured.state.snapshot), canonical(expected))
    assert.deepEqual(captured.state.head, raw.head)
    assert.deepEqual(captured.state.adopted, raw.adopted)
    const bytes = readFileSync(resolve(output, captured.capture.raw.path))
    assert.equal(hash(bytes), captured.capture.raw.sha256)
    assert.deepEqual(JSON.parse(bytes), original)
    assert.equal('current_day_witnesses' in JSON.parse(bytes).snapshot.baseline.plan, false)
    assert.equal(hash(readFileSync(resolve(output, captured.capture.expected.path))), captured.capture.expected.sha256)
    await assert.rejects(captureExpectedState(raw, { root, output, stem: 'read', python: process.env.AUTO_PLAN_ACCEPTANCE_PYTHON ?? 'python' }), 'A duplicate capture identity must not overwrite its immutable source proof.')
    assert.equal(hash(readFileSync(resolve(output, captured.capture.raw.path))), captured.capture.raw.sha256)
    // Literal expected schema default; no observed public reply supplies this value.
    const observed = structuredClone(expected)
    assert.deepEqual(observed.baseline.plan.current_day_witnesses, [])
    for (const mutate of [
      value => { delete value.baseline.plan.current_day_witnesses },
      value => { value.baseline.plan.current_day_witnesses.push({ date: '2026-10-12', line_code: 'L00', basis_digest: 'changed', plan_digest: 'changed' }) },
      value => { value.baseline.plan.unexpected = [] },
      value => { value.inputs.input_version++ },
      value => { value.inputs.business.demands[0].quantity++ },
      value => { value.time.as_of = '2026-10-12T01:00:01Z' },
      value => { value.baseline.version++ },
    ]) {
      const changed = structuredClone(observed); mutate(changed)
      assert.throws(() => assert.deepEqual(canonical(changed), canonical(captured.state.snapshot)))
    }
  } finally { rmSync(output, { recursive: true, force: true }) }
})

test('an invalid expected source is rejected and its raw capture is retained', async () => {
  const output = mkdtempSync(resolve(tmpdir(), 'auto-plan-expected-source-invalid-'))
  try {
    const raw = rawState(); raw.snapshot.baseline.plan.unexpected = []
    await assert.rejects(captureExpectedState(raw, { root, output, stem: 'invalid', python: process.env.AUTO_PLAN_ACCEPTANCE_PYTHON ?? 'python' }))
    assert.deepEqual(JSON.parse(readFileSync(resolve(output, 'invalid-raw-state.json'))), raw)
  } finally { rmSync(output, { recursive: true, force: true }) }
})

test('concurrent expected-source captures keep distinct complete inputs and immutable artifact identities', async () => {
  const output = mkdtempSync(resolve(tmpdir(), 'auto-plan-expected-source-concurrent-'))
  try {
    const first = rawState(), second = rawState()
    second.head.input_version = second.snapshot.inputs.input_version = 2
    second.snapshot.inputs.masters.models[0].name = 'Independent second captured input'
    second.snapshot.plan.segments[0].quantity++
    const captured = await Promise.all([first, second].map((raw, index) => captureExpectedState(raw,
      { root, output, stem: 'read-' + index, python: process.env.AUTO_PLAN_ACCEPTANCE_PYTHON ?? 'python' })))
    for (const key of ['raw', 'schema', 'expected']) assert.notEqual(captured[0].capture[key].path, captured[1].capture[key].path)
    for (const [index, raw] of [first, second].entries()) {
      const result = captured[index]
      assert.deepEqual(result.state.snapshot.inputs, raw.snapshot.inputs)
      assert.deepEqual(canonical(result.state.snapshot.plan.segments), canonical(raw.snapshot.plan.segments))
      const schemaBytes = readFileSync(resolve(output, result.capture.schema.path))
      assert.equal(hash(schemaBytes), result.capture.schema.sha256)
      assert.deepEqual(JSON.parse(schemaBytes), { model_module: resolve(root, 'src/auto_plan/plan_models.py'), model_sha256: hash(readFileSync(resolve(root, 'src/auto_plan/plan_models.py'))) })
      assert.deepEqual(result.capture.head, raw.head)
      const bytes = readFileSync(resolve(output, result.capture.raw.path))
      assert.equal(hash(bytes), result.capture.raw.sha256)
      assert.deepEqual(JSON.parse(bytes), raw)
      assert.equal(hash(readFileSync(resolve(output, result.capture.expected.path))), result.capture.expected.sha256)
    }
  } finally { rmSync(output, { recursive: true, force: true }) }
})
