import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { gunzipSync } from 'node:zlib'
import { assertEpochPlan, assertFullPrivateEpochResult, assertSmallPrivateEpochResult } from './private-shared-epoch.mjs'

const frozen = new URL('../../../docs/acceptance/frozen/', import.meta.url)
const manifest = JSON.parse(readFileSync(new URL('manifest.json', frozen)))
const pointer = manifest.scenarios.find(row => row.id === 'r20-loose').source
const source = JSON.parse(gunzipSync(readFileSync(new URL(pointer.path, frozen))))

function sample() {
  const snapshot = structuredClone(source), parentPlan = structuredClone(source.plan)
  parentPlan.segments.find(row => row.id === 'S-2027-01-24-M00').quantity = 99
  const plan = structuredClone(parentPlan)
  plan.segments.find(row => row.id === 'S-2027-01-24-M39').quantity = 99
  const weekly = source.inputs.business.demands.map(row => {
    assert.equal(row.quantity, 700)
    const affected = ['M00', 'M39'].includes(row.model_code) && row.week === '2027-01-18'
    return { model_code: row.model_code, week: row.week, demand: 700, on_time: affected ? 699 : 700, gap: affected ? 1 : 0, late: 0, pending: 0 }
  })
  const objective = Array(30).fill('0').concat(['418', '2', '0']); objective[14] = objective[29] = '2'
  const effects = [['CAP', 'L01', '2027-01-24'], ...Array.from({ length: 18 }, (_, i) => ['CELL', '2027-01-24', 'L' + String(i).padStart(2, '0'), 'M39']),
    ...Array.from({ length: 15 }, (_, i) => ['REQ', 'M39', weekly[i * 40].week])]
  const totals = { expected: 419998, on_time: 419998, late: 0, gap: 2, pending: 0, unallocated: 0, outside_window: 0 }
  const expected = { snapshot, parentPlan, context: { execution: snapshot.execution, reset: snapshot.reset, locks: snapshot.locks },
    pins: { parent_revision: 2, base_revision: 1, input_version: 1, latest_adoption: 1 },
    deltas: [{ segment_id: 'S-2027-01-24-M39', quantity: 99, hours_lower_exclusive: '49/50', hours_upper_inclusive: '18' }], totals, weekly, objective, effects }
  return { expected, result: { parent_revision: 2, base_revision: 1, revision: 2, input_version: 1, latest_adoption: 1, merged: true,
    plan, snapshot: { ...structuredClone(snapshot), plan }, context: structuredClone(expected.context), context_changes: [], unallocated: [], operation_issues: [], adoption_blocked: false,
    effects: structuredClone(effects), evaluation: { capacity_valid: true, computation_complete: true, executable: true, output_totals: structuredClone(totals), weekly_results: structuredClone(weekly), switch_count: 418, stability: { weighted_cost: 2, order_change_count: 0 } } } }
}

test('declared full parent epoch preserves all 600 weeks and 33 coordinates', () => {
  const { result, expected } = sample()
  assert.equal(expected.weekly.length, 600); assert.equal(expected.effects.length, 34)
  assertFullPrivateEpochResult(result, expected)
  result.plan.switches.reverse()
  assertFullPrivateEpochResult(result, expected)
})

test('small checker keeps its original 75-week behavior', () => {
  const snapshot = structuredClone(source), plan = structuredClone(source.plan)
  plan.segments.find(row => row.id === 'S-2027-01-24-M00').quantity = 99
  const weekly = sample().expected.weekly.filter(row => ['M00', 'M02', 'M04', 'M06', 'M08'].includes(row.model_code))
  const result = { plan, snapshot: { ...snapshot, plan }, evaluation: { capacity_valid: true, computation_complete: true, executable: true,
    weekly_results: weekly, output_totals: { expected: 52499, on_time: 52499, late: 0, gap: 1, pending: 0, unallocated: 0, outside_window: 0 } } }
  assertSmallPrivateEpochResult(result, { snapshot }, snapshot)
  result.evaluation.weekly_results.pop()
  assert.throws(() => assertSmallPrivateEpochResult(result, { snapshot }, snapshot))
})

const negatives = {
  'unrelated quantity': value => { value.result.plan.segments[0].quantity++ },
  'selected quantity': value => { value.result.plan.segments.find(row => row.id.endsWith('M39') && row.date === '2027-01-24').quantity = 98 },
  'strict lower hour': value => { value.result.plan.segments.find(row => row.id === 'S-2027-01-24-M39').hours = '49/50' },
  'upper hour': value => { value.result.plan.segments.find(row => row.id === 'S-2027-01-24-M39').hours = '1801/100' },
  'event order': value => { value.result.plan.segments[0].order++ },
  'production collection order': value => { value.result.plan.segments.reverse() },
  'duplicate switch': value => { value.result.plan.switches[1] = structuredClone(value.result.plan.switches[0]) },
  'switch allocation order': value => { value.result.plan.switches[0].allocations[0].order++ },
  'switch allocation hour': value => { value.result.plan.switches[0].allocations[0].hours = '99' },
  'switch recipe': value => { value.result.plan.switches[0].from_recipe = 'R99' },
  'complete source': value => { value.result.snapshot.inputs.masters.models[0].name = 'changed' },
  'context': value => { value.result.context.locks = [{ date: '2027-01-24' }] },
  'parent pin': value => { value.result.parent_revision = 1 },
  'input pin': value => { value.result.input_version = 2 },
  'merge claim': value => { value.result.merged = false },
  'weekly identity': value => { value.result.evaluation.weekly_results[0].model_code = 'M99' },
  'weekly value': value => { value.result.evaluation.weekly_results[0].on_time = 699 },
  'missing weekly': value => { value.result.evaluation.weekly_results.pop() },
  'total': value => { value.result.evaluation.output_totals.expected++ },
  'objective': value => { value.result.evaluation.stability.weighted_cost++ },
  'effect': value => { value.result.effects.pop() },
  'incomplete': value => { value.result.evaluation.computation_complete = false },
}
for (const [name, change] of Object.entries(negatives)) test('rejects ' + name, () => {
  const value = sample(); change(value)
  assert.throws(() => assertFullPrivateEpochResult(value.result, value.expected))
})

test('allocation array order stays exact even when collection positions may move', () => {
  const plan = { segments: [], switches: [{ id: 'X', allocations: [{ date: '2027-01-23', order: 1, hours: '1' }, { date: '2027-01-24', order: 0, hours: '1' }] }] }
  const changed = structuredClone(plan); changed.switches[0].allocations.reverse()
  assert.throws(() => assertEpochPlan(changed, plan))
})
