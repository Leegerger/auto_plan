import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { assertState, canonical } from './contracts.mjs'

const digest = value => createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex')
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
