/** Acceptance-only shared-epoch business proof; browser clocks remain in the caller. */
import assert from 'node:assert/strict'
import { canonical } from './contracts.mjs'

export function assertSmallPrivateEpochResult(result, initialState, source) {
  assert.deepEqual(canonical(result.snapshot), canonical({ ...initialState.snapshot, plan: result.plan }), 'A private preview changed its complete frozen inputs/context/baseline.')
  const changed = source.plan.segments.filter(row => result.plan.segments.find(item => item.id === row.id)?.quantity !== row.quantity)
  assert.ok(changed.every(row => ['S-2027-01-24-M00', 'S-2027-01-24-M02'].includes(row.id)))
  const gap = changed.length
  for (const [key, value] of Object.entries({ expected: 52500-gap, on_time: 52500-gap, late: 0, gap, pending: 0, unallocated: 0, outside_window: 0 })) assert.equal(result.evaluation.output_totals[key], value)
  assert.ok(result.evaluation.capacity_valid && result.evaluation.computation_complete && result.evaluation.executable)
  assert.equal(result.evaluation.weekly_results.length, 75)
  assert.equal(new Set(result.evaluation.weekly_results.map(row => row.model_code+':'+row.week)).size, 75)
  for (const row of result.evaluation.weekly_results) {
    const affected = row.week === '2027-01-18' && changed.some(item => item.model_code === row.model_code)
    assert.equal(row.demand, 700); assert.equal(row.on_time, affected ? 699 : 700); assert.equal(row.gap, affected ? 1 : 0)
    assert.equal(row.late, 0); assert.equal(row.pending, 0)
  }
}

function uniqueRows(rows, name) {
  assert.ok(Array.isArray(rows), name + ' collection is missing.')
  const ids = rows.map(row => row.id)
  assert.ok(ids.every(id => typeof id === 'string' && id.length > 0))
  assert.equal(new Set(ids).size, rows.length, name + ' identities repeat.')
  return rows
}

export function assertEpochPlan(actual, expected) {
  actual = structuredClone(actual); expected = structuredClone(expected)
  uniqueRows(actual.segments, 'Production'); uniqueRows(expected.segments, 'Expected production')
  const left = uniqueRows(actual.switches, 'Switch'), right = uniqueRows(expected.switches, 'Expected switch')
  assert.equal(left.length, right.length, 'The complete switch collection changed length.')
  // Collection position is not event order. Allocation arrays and every field stay exact.
  assert.deepEqual([...left].sort((a, b) => a.id.localeCompare(b.id)), [...right].sort((a, b) => a.id.localeCompare(b.id)), 'A complete switch record changed.')
  delete actual.switches; delete expected.switches
  assert.deepEqual(canonical(actual), canonical(expected), 'Another complete Plan field changed.')
}

export function assertFullPrivateEpochResult(result, expected) {
  const { snapshot, parentPlan, context, pins, deltas, totals, weekly, objective, effects } = expected
  assert.deepEqual([result.parent_revision, result.base_revision, result.revision, result.input_version, result.latest_adoption],
    [pins.parent_revision, pins.base_revision, pins.parent_revision, pins.input_version, pins.latest_adoption], 'The actual shared epoch or source pin differs.')
  assert.equal(result.merged, pins.parent_revision !== pins.base_revision)
  const wanted = structuredClone(parentPlan), actual = structuredClone(result.plan)
  assert.equal(new Set(deltas.map(delta => delta.segment_id)).size, deltas.length)
  for (const delta of deltas) {
    const before = wanted.segments.find(row => row.id === delta.segment_id), after = actual.segments.find(row => row.id === delta.segment_id)
    assert.ok(before && after); assert.ok(Number.isInteger(delta.quantity) && delta.quantity !== before.quantity)
    before.quantity = delta.quantity
    const rational = value => canonical({ hours: value }).hours.split('/').map(BigInt)
    const [n, d] = rational(after.hours), [lo, ld] = rational(delta.hours_lower_exclusive), [hi, hd] = rational(delta.hours_upper_inclusive)
    assert.ok(n * ld > lo * d && n * hd <= hi * d, 'Selected hours violate the declared strict range.')
    delete before.hours; delete after.hours
  }
  assertEpochPlan(actual, wanted)
  assert.deepEqual(canonical(result.snapshot), canonical({ ...snapshot, plan: result.plan }), 'The complete frozen source changed.')
  assert.deepEqual(canonical(result.context), canonical(context), 'The shared context changed.')
  assert.deepEqual(result.context_changes, []); assert.deepEqual(result.unallocated, []); assert.deepEqual(result.operation_issues, [])
  assert.equal(result.adoption_blocked, false)
  assert.deepEqual([...result.effects].sort(), [...effects].sort(), 'Dependency effects differ.')
  const evaluation = result.evaluation
  for (const key of ['capacity_valid', 'computation_complete', 'executable']) assert.equal(evaluation[key], true)
  assert.deepEqual(Object.fromEntries(Object.keys(totals).map(key => [key, evaluation.output_totals[key]])), totals)
  const fields = ['model_code', 'week', 'demand', 'on_time', 'gap', 'late', 'pending']
  const observed = evaluation.weekly_results.map(row => Object.fromEntries(fields.map(key => [key, row[key]])))
  assert.equal(new Set(observed.map(row => row.model_code + ':' + row.week)).size, observed.length)
  assert.deepEqual(observed, weekly, 'Complete dated weekly quantities differ.')
  const weeks = [...new Set(weekly.map(row => row.week))].sort()
  assert.equal(weeks.length, 15); assert.equal(objective.length, 33)
  const vector = [...weeks.map(week => String(observed.filter(row => row.week === week).reduce((sum, row) => sum + row.demand - row.on_time, 0))),
    ...weeks.map(week => String(observed.filter(row => row.week === week).reduce((sum, row) => sum + row.gap, 0))),
    String(evaluation.switch_count), String(evaluation.stability.weighted_cost), String(evaluation.stability.order_change_count)]
  assert.deepEqual(vector, objective, 'The complete 33-coordinate objective differs.')
}
