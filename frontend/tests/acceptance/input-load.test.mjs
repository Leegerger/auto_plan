import test from 'node:test'
import assert from 'node:assert/strict'
import { assertState, canonical } from './contracts.mjs'
import { INPUT_CONTENT_DIGEST_ALGORITHM, inputContentDigest, resolveInputCompanion } from './input-load.mjs'

const digest = value => inputContentDigest(value)
const withoutVersion = inputs => { const value = structuredClone(inputs); delete value.input_version; return value }
const plan = { segments: [{ id: 'one', date: '2026-10-19', line_code: 'L0', model_code: 'M', quantity: 100, hours: '1', order: 0 }],
  switches: [], initial_states: [], ordinary_idle: [], current_day_witnesses: [] }
const inputs = { schema_version: 1, input_version: 1,
  masters: { recipes: [{ code: 'R', name: 'Recipe', active: true }],
    models: [{ code: 'M', name: 'Model', recipe_code: 'R', active: true }],
    lines: ['L0', 'L1', 'L2'].map(code => ({ code, name: code, active: true })), capabilities: [] },
  parameters: { line_yields: [], model_yields: [], capacities: [], cycle_times: [], switch_durations: [] },
  calendar: { holidays: [], opening_exceptions: [], maintenance: [], experiments: [] },
  business: { demands: [{ model_code: 'M', week: '2026-10-19', quantity: 100 }], confirmations: [] } }

function fixture() {
  const initial = { head: { revision: 1, input_version: 1, latest_adoption: 1, context: { marker: 'original' },
    coverage: [], lineage: {}, unallocated: [], operation_issues: [], pending: false },
    snapshot: { inputs: structuredClone(inputs), plan: structuredClone(plan), baseline: null, execution: { facts: [] }, reset: null, locks: [], preferences: {} } }
  const targets = [
    { editor: 1, kind: 'models', code: 'M', names: ['Model', 'Model · editor1'] },
    ...['L0', 'L1', 'L2'].map((code, index) => ({ editor: index + 2, kind: 'lines', code, names: [code, code + ' · editor' + (index + 2)] })),
  ]
  const states = Array.from({ length: 16 }, (_, mask) => {
    const value = structuredClone(inputs)
    for (const [index, target] of targets.entries()) value.masters[target.kind].find(row => row.code === target.code).name = target.names[(mask >> index) & 1]
    return { id: String(mask), inputs: value, inputs_sha256: digest(withoutVersion(value)) }
  })
  return { initial, declaration: { schema_version: 1, kind: 'maintained_input_names',
    digest_algorithm: INPUT_CONTENT_DIGEST_ALGORITHM,
    source_inputs_sha256: digest(withoutVersion(inputs)), targets, states } }
}

test('a declared complete name-only input version preserves the frozen business source', () => {
  const { initial, declaration } = fixture()
  const changed = structuredClone(initial)
  changed.head.input_version = 2
  changed.snapshot.inputs = structuredClone(declaration.states[1].inputs)
  changed.snapshot.inputs.input_version = 2

  assert.doesNotThrow(() => assertState(changed, initial, [plan], { inputCompanion: declaration }))
  assert.throws(() => assertState(changed, initial), 'Solo source checks still require their original immutable input version.')

  const demand = structuredClone(changed); demand.snapshot.inputs.business.demands[0].quantity = 101
  assert.throws(() => assertState(demand, initial, [plan], { inputCompanion: declaration }))
  const recipe = structuredClone(changed); recipe.snapshot.inputs.masters.models[0].recipe_code = 'another'
  assert.throws(() => assertState(recipe, initial, [plan], { inputCompanion: declaration }))
  const context = structuredClone(changed); context.head.context.marker = 'changed'
  assert.throws(() => assertState(context, initial, [plan], { inputCompanion: declaration }))
  const wrongPin = structuredClone(changed); wrongPin.head.input_version = 3
  assert.throws(() => assertState(wrongPin, initial, [plan], { inputCompanion: declaration }))
  const sameVersion = structuredClone(changed); sameVersion.head.input_version = sameVersion.snapshot.inputs.input_version = 1
  assert.throws(() => assertState(sameVersion, initial, [plan], { inputCompanion: declaration }))
})

test('complete input hashes declare their exact decoded numeric representation', () => {
  const { declaration } = fixture()
  const missing = structuredClone(declaration); delete missing.digest_algorithm
  assert.throws(() => resolveInputCompanion(missing, inputs, inputs), /digest algorithm/i)
  const wrong = structuredClone(declaration); wrong.digest_algorithm = 'exact-rational-json-v1'
  assert.throws(() => resolveInputCompanion(wrong, inputs, inputs), /digest algorithm/i)

  const numeric = { input_version: 7, hours: '1/2', days: '5.0', percent: '95.00', rows: [{ hours: '0.50' }] }
  assert.equal(inputContentDigest(numeric), '10cfec11b331891ae5270329036026152131be257071fe192af8efe51533851b')
  const next = structuredClone(numeric); next.input_version = 8
  assert.equal(inputContentDigest(next), inputContentDigest(numeric))
  const normalized = { ...numeric, hours: '0.5', days: '5', percent: '95', rows: [{ hours: '1/2' }] }
  assert.notEqual(inputContentDigest(normalized), inputContentDigest(numeric))
  assert.deepEqual(canonical(normalized), canonical(numeric), 'The existing semantic hash intentionally has different rules.')
})

test('an acknowledged unchanged input is not a genuine editor commit', () => {
  const { declaration } = fixture()
  const before = structuredClone(inputs)
  const changed = structuredClone(declaration.states[1].inputs); changed.input_version = 2
  const command = { base_version: 1, changes: { models: [structuredClone(changed.masters.models[0])] } }
  const accepted = resolveInputCompanion(declaration, inputs, changed,
    { transition: { editor: 1, before, command, response_status: 200 } })
  assert.equal(accepted.transition.kind, 'modified_input_name')
  assert.equal(accepted.transition.editor, 1)
  assert.equal(accepted.transition.before_version, 1)
  assert.equal(accepted.transition.input_version, 2)
  assert.equal(accepted.transition.state_id, '1')

  const unchangedCommand = { base_version: 1, changes: { models: [structuredClone(before.masters.models[0])] } }
  assert.throws(() => resolveInputCompanion(declaration, inputs, before,
    { transition: { editor: 1, before, command: unchangedCommand, response_status: 200 } }), /modified|changed|advance/i)
  assert.throws(() => resolveInputCompanion(declaration, inputs, changed,
    { transition: { editor: 2, before, command, response_status: 200 } }))
  const twoNames = structuredClone(declaration.states[3].inputs); twoNames.input_version = 2
  assert.throws(() => resolveInputCompanion(declaration, inputs, twoNames,
    { transition: { editor: 1, before, command, response_status: 200 } }))
  const gap = structuredClone(changed); gap.input_version = 3
  assert.throws(() => resolveInputCompanion(declaration, inputs, gap,
    { transition: { editor: 1, before, command, response_status: 200 } }))
  assert.throws(() => resolveInputCompanion(declaration, inputs, before,
    { transition: { editor: 1, before, command, response_status: 409 } }))
})

test('current-run history must attribute every intervening immutable input version', () => {
  const { declaration } = fixture()
  const afterOne = structuredClone(declaration.states[1].inputs); afterOne.input_version = 2
  const afterTwo = structuredClone(declaration.states[3].inputs); afterTwo.input_version = 3
  const event = (before, after, editor, kind, code) => ({ before, after, editor, response_status: 200,
    command: { base_version: before.input_version, changes: { [kind]: [structuredClone(after.masters[kind].find(row => row.code === code))] } } })
  const one = event(inputs, afterOne, 1, 'models', 'M')
  const two = event(afterOne, afterTwo, 2, 'lines', 'L0')
  assert.doesNotThrow(() => resolveInputCompanion(declaration, inputs, afterTwo, { history: [one, two] }))
  assert.throws(() => resolveInputCompanion(declaration, inputs, afterTwo, { history: [two] }), /history|version|snapshot/i)
  assert.throws(() => resolveInputCompanion(declaration, inputs, afterTwo, { history: [one] }), /history|version|snapshot/i)
  assert.throws(() => resolveInputCompanion(declaration, inputs, afterTwo, { history: [two, one] }))
})
