import test from 'node:test'
import assert from 'node:assert/strict'
import { assertManualRequest, assertManualPlan, assertManualEvaluation, assertState, failEntry, terminal } from './contracts.mjs'

const row = { id: 'last-M00', date: '2027-01-24', line_code: 'L00', model_code: 'M00', quantity: 100, hours: '1', order: 0 }
const other = { ...row, id: 'other-M02', line_code: 'L01', model_code: 'M02' }
const source = { plan: { segments: [row, other], switches: [], initial_states: [], ordinary_idle: [] } }
const scope = { start_date: row.date, end_date: row.date, line_codes: ['L00'], model_codes: ['M00'] }

test('a matching quantity cannot substitute another segment or scope in the submitted action', () => {
  const command = { operations: [{ kind: 'change_quantity', segment_id: row.id, quantity: 99, end_date: row.date, scope, action_id: 'one' }] }
  assertManualRequest(command, [row], 'change_quantity', 99)
  assert.throws(() => assertManualRequest({ operations: [{ ...command.operations[0], segment_id: other.id }] }, [row], 'change_quantity', 99))
  assert.throws(() => assertManualRequest({ operations: [{ ...command.operations[0], scope: { ...scope, line_codes: ['L00', 'L01'] } }] }, [row], 'change_quantity', 99))
})

test('two missing rows must be the named cleared identities and outside witnesses stay exact', () => {
  assertManualPlan({ ...source.plan, segments: [] }, source, [row, other], 'clear_segments')
  const third = { ...other, id: 'unaffected', model_code: 'M03', date: '2027-01-23' }
  const complete = { plan: { ...source.plan, segments: [row, other, third] } }
  assert.throws(() => assertManualPlan({ ...source.plan, segments: [{ ...third, hours: '2' }] }, complete, [row, other], 'clear_segments'))
  assertManualPlan({ ...source.plan, segments: [{ ...third, hours: '2/2' }] }, complete, [row, other], 'clear_segments')
})

test('single-cell coverage requires the correct final model/week and every unaffected result', () => {
  const evaluation = { capacity_valid: true, computation_complete: true, executable: true,
    output_totals: { expected: 419999, on_time: 419999, late: 0, gap: 1, pending: 0, unallocated: 0, outside_window: 0 },
    weekly_results: Array.from({ length: 600 }, (_, i) => ({ model_code: `M${String(i % 40).padStart(2, '0')}`, week: new Date(Date.UTC(2026, 9, 12 + 7 * Math.floor(i / 40))).toISOString().slice(0, 10), demand: 700, on_time: i === 560 ? 699 : 700, gap: i === 560 ? 1 : 0, pending: 0, late: 0 })) }
  assertManualEvaluation(evaluation, [row])
  const bad = structuredClone(evaluation); bad.weekly_results[560].gap = 0; bad.weekly_results[561].gap = 1
  assert.throws(() => assertManualEvaluation(bad, [row]))
})

test('zero-net quantity is insufficient when a switch or retained context changed', () => {
  const initial = { head: { input_version: 1, latest_adoption: 1, context: { marker: 'original' }, coverage: [], lineage: {}, unallocated: [], operation_issues: [], pending: false }, snapshot: { plan: source.plan, inputs: { input_version: 1 }, baseline: null } }
  assertState(structuredClone(initial), initial)
  const bad = structuredClone(initial); bad.head.context.marker = 'changed'
  assert.throws(() => assertState(bad, initial))
  const witness = structuredClone(initial); witness.snapshot.plan.switches.push({ id: 'new', allocations: [] })
  assert.throws(() => assertState(witness, initial))
})

test('a screenshot or cleanup failure invalidates an earlier timed pass and setup has no duration', () => {
  const passed = { status: 'pass', phase: 'screenshot', clicked_ms: 10, seconds: 0.1 }
  failEntry(passed, new Error('Artifact write failed.'))
  assert.equal(passed.status, 'fail'); assert.equal(passed.failure_phase, 'screenshot'); assert.equal(passed.seconds, 0.1)
  const setup = { status: 'pass', phase: 'setup', seconds: 0 }
  failEntry(setup, new Error('Source differs.'))
  assert.equal(setup.seconds, null); assert.equal(setup.measurement_started, false)
  assert.equal(terminal('queued'), false); assert.equal(terminal('running'), false); assert.equal(terminal('interrupted'), true)
})
