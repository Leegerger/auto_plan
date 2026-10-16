/** Independent private-draft business checks; browser activity is checked by callers. */
import assert from 'node:assert/strict'
import { canonical, assertPlan } from './contracts.mjs'

export function privateExpectation(source, target, reference) {
  const objective = [...reference.objective]
  objective[14] = objective[29] = '1'; objective[31] = '1'
  const row = source.plan.segments.find(row => row.id === target.segment_id)
  assert.ok(row)
  const effects = [['CAP', row.line_code, row.date],
    ...source.inputs.masters.lines.map(line => ['CELL', row.date, line.code, row.model_code]),
    ...source.inputs.business.demands.filter(demand => demand.model_code === row.model_code).map(demand => ['REQ', row.model_code, demand.week])]
  if (target.expected.weekly_count !== undefined) assert.equal(target.expected.weekly_count, source.inputs.business.demands.length)
  if (target.expected.objective) assert.deepEqual(target.expected.objective, objective)
  if (target.expected.effects) assert.deepEqual([...target.expected.effects].sort(), [...effects].sort())
  return { weekly_count: source.inputs.business.demands.length, objective, effects,
    original_totals: reference.totals, original_objective: reference.objective }
}
function vector(value) {
  const weeks = [...new Set(value.weekly_results.map(row => row.week))].sort()
  assert.equal(weeks.length, 15, 'All fifteen demand weeks are required.')
  return [...weeks.map(week => String(value.weekly_results.filter(row => row.week === week).reduce((sum, row) => sum + row.demand - row.on_time, 0))),
    ...weeks.map(week => String(value.weekly_results.filter(row => row.week === week).reduce((sum, row) => sum + row.gap, 0))),
    String(value.switch_count), String(value.stability.weighted_cost), String(value.stability.order_change_count)]
}
function totals(value, expected) {
  assert.ok(value.capacity_valid && value.computation_complete && value.executable)
  assert.deepEqual(Object.fromEntries(Object.keys(expected).map(key => [key, value.output_totals[key]])), expected)
}
export function assertPrivateCycleBusiness({ source, baseline, target, changed, undo, expectation }) {
  const expected = structuredClone(source.plan), actual = structuredClone(changed.plan)
  const wanted = expected.segments.find(row => row.id === target.segment_id)
  const selected = actual.segments.find(row => row.id === target.segment_id)
  assert.ok(wanted && selected); wanted.quantity = target.expected.quantity
  const fraction = value => canonical({ hours: value }).hours.split('/').map(BigInt)
  const [n,d] = fraction(selected.hours), [lo,ld] = fraction(target.expected.hours_lower_exclusive), [hi,hd] = fraction(target.expected.hours_upper_inclusive)
  assert.ok(n * ld > lo * d && n * hd <= hi * d, 'Selected hours violate the declared strict capacity range.')
  delete wanted.hours; delete selected.hours
  assertPlan(actual, expected, 'Private preview changed another complete Plan field.')
  assertPlan(baseline.plan, source.plan)
  assert.deepEqual(canonical(changed.snapshot), canonical({ ...baseline, plan: changed.plan }), 'Private preview changed its complete source.')
  assert.deepEqual([...changed.effects].sort(), [...expectation.effects].sort(), 'Private dependency effects differ.')
  totals(changed.evaluation, target.expected.totals)
  const weekly = changed.evaluation.weekly_results
  assert.equal(weekly.length, expectation.weekly_count)
  const identities = new Set(weekly.map(row => row.model_code + ':' + row.week))
  assert.equal(identities.size, expectation.weekly_count)
  for (const demand of source.inputs.business.demands) {
    const rows = weekly.filter(row => row.model_code === demand.model_code && row.week === demand.week)
    assert.equal(rows.length, 1)
    const row = rows[0], affected = row.model_code === target.expected.changed_week.model_code && row.week === target.expected.changed_week.week
    assert.equal(row.demand, demand.quantity)
    assert.equal(row.on_time, affected ? target.expected.changed_week.on_time : demand.quantity)
    assert.equal(row.gap, affected ? target.expected.changed_week.gap : 0)
    assert.equal(row.pending, 0); assert.equal(row.late, 0)
  }
  assert.deepEqual(vector(changed.evaluation), expectation.objective, 'Complete private objective differs.')
  assertPlan(undo.plan, source.plan)
  assert.deepEqual(canonical(undo.snapshot), canonical(baseline), 'Undo changed the complete source.')
  assert.deepEqual(undo.effects, [])
  totals(undo.evaluation, expectation.original_totals)
  assert.deepEqual(vector(undo.evaluation), expectation.original_objective)
}
