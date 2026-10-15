import assert from 'node:assert/strict'
import { resolveInputCompanion } from './input-load.mjs'

function fraction(value) {
  const text = String(value)
  if (text.includes('/')) { const [a, b] = text.split('/'); return reduce(BigInt(a), BigInt(b)) }
  const match = /^(-?)(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/i.exec(text)
  assert.ok(match, 'An exact rational value is required.')
  const decimals = match[3] ?? '', exponent = Number(match[4] ?? 0) - decimals.length
  let numerator = BigInt((match[1] || '') + match[2] + decimals), denominator = 1n
  if (exponent >= 0) numerator *= 10n ** BigInt(exponent)
  else denominator = 10n ** BigInt(-exponent)
  return reduce(numerator, denominator)
}
function reduce(a, b) {
  assert.notEqual(b, 0n, 'A rational denominator must be nonzero.')
  if (b < 0n) { a = -a; b = -b }
  let x = a < 0n ? -a : a, y = b
  while (y) [x, y] = [y, x % y]
  return `${a / x}/${b / x}`
}
export function canonical(value, key = '') {
  if (['hours', 'idle_hours', 'remaining_hours', 'prior_switch_hours', 'percent', 'days'].includes(key) && value !== null) return fraction(value)
  if (Array.isArray(value)) return value.map(item => canonical(item))
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(name => [name, canonical(value[name], name)]))
  return value
}
export function planValue(plan) {
  const result = structuredClone(plan)
  for (const key of ['segments', 'switches', 'initial_states', 'ordinary_idle', 'current_day_witnesses']) {
    result[key] ??= []
    result[key].sort((a, b) => JSON.stringify([a.id ?? '', a.date ?? '', a.line_code ?? '']).localeCompare(JSON.stringify([b.id ?? '', b.date ?? '', b.line_code ?? ''])))
  }
  return canonical(result)
}
export function assertPlan(actual, expected, message = 'Complete plan differs from the expected frozen witness.') {
  assert.deepEqual(planValue(actual), planValue(expected), message)
}
export function stateValue(state) {
  return canonical({ ...state.snapshot, plan: planValue(state.snapshot.plan),
    coverage: state.head.coverage, context: state.head.context, lineage: state.head.lineage,
    unallocated: state.head.unallocated, operation_issues: state.head.operation_issues, pending: state.head.pending })
}
export function assertState(actual, initial, allowedPlans = [initial.snapshot.plan], options = {}) {
  assert.equal(actual.head.input_version, actual.snapshot.inputs.input_version, 'Head and immutable input snapshot pins differ.')
  const inputState = options.inputCompanion ? resolveInputCompanion(options.inputCompanion, initial.snapshot.inputs, actual.snapshot.inputs) : null
  if (!inputState) assert.equal(actual.head.input_version, initial.head.input_version, 'Input version changed.')
  assert.equal(actual.head.latest_adoption, initial.head.latest_adoption, 'Adopted baseline changed.')
  const value = stateValue(actual)
  const matches = allowedPlans.some(plan => {
    const expected = stateValue(initial); expected.plan = planValue(plan)
    if (inputState) expected.inputs = canonical({ ...inputState.inputs, input_version: actual.head.input_version })
    try { assert.deepEqual(value, expected); return true } catch { return false }
  })
  assert.ok(matches, 'Full input/context/baseline/plan or retained result state changed outside the declared primary edit.')
}
export function manualRows(source, ids) {
  assert.equal(new Set(ids).size, ids.length, 'Manual action IDs repeat.')
  const rows = ids.map(id => source.plan.segments.find(row => row.id === id))
  assert.ok(rows.every(Boolean), 'A frozen manual segment is missing.')
  assert.ok(rows.every(row => row.quantity === 100 && row.date === '2027-01-24'), 'Manual timing requires frozen final-date 100-piece source segments.')
  return rows
}
export function scopeFor(rows) {
  const dates = rows.map(row => row.date).sort()
  return { start_date: dates[0], end_date: dates.at(-1), line_codes: [...new Set(rows.map(row => row.line_code))], model_codes: [...new Set(rows.map(row => row.model_code))] }
}
export function assertManualRequest(command, rows, kind, quantity) {
  assert.equal(command.operations.length, 1, 'The timed manual request must contain one complete action.')
  const operation = { ...command.operations[0] }; delete operation.action_id
  const expected = kind === 'change_quantity' ? { kind, segment_id: rows[0].id, quantity, end_date: rows[0].date, scope: scopeFor(rows) } : { kind, segment_ids: rows.map(row => row.id), scope: scopeFor(rows) }
  assert.deepEqual(operation, expected, 'Submitted manual identity, quantity or complete scope differs.')
}
export function assertManualPlan(actual, source, rows, kind, quantity = 99) {
  const selected = new Set(rows.map(row => row.id)), expected = new Map(source.plan.segments.map(row => [row.id, row]))
  const observed = new Map(actual.segments.map(row => [row.id, row]))
  assert.equal(observed.size, actual.segments.length, 'Result segment IDs repeat.')
  assert.equal(observed.size, expected.size - (kind === 'clear_segments' ? rows.length : 0), 'Unexpected segment count after the manual action.')
  for (const [id, row] of expected) {
    if (kind === 'clear_segments' && selected.has(id)) { assert.ok(!observed.has(id), 'The named cleared segment remains.'); continue }
    const result = observed.get(id); assert.ok(result, 'An unaffected segment disappeared.')
    const wanted = selected.has(id) ? { ...row, quantity } : row
    const inBlock = rows.some(changed => changed.date === row.date && changed.line_code === row.line_code)
    // Only exact capacity witnesses inside the declared line/date block may regenerate.
    const left = { ...result }, right = { ...wanted }
    if (inBlock) { delete left.hours; delete right.hours }
    assert.deepEqual(canonical(left), canonical(right), 'An unaffected segment identity/order/quantity or outside witness changed.')
  }
  for (const key of ['initial_states', 'ordinary_idle', 'current_day_witnesses']) assert.deepEqual(canonical(actual[key] ?? []), canonical(source.plan[key] ?? []), 'Protected plan metadata changed.')
  const outside = plan => plan.switches.filter(sw => !rows.some(row => row.line_code === sw.line_code && sw.allocations.every(part => part.date === row.date))).sort((a, b) => a.id.localeCompare(b.id))
  assert.deepEqual(canonical(outside(actual)), canonical(outside(source.plan)), 'A switch outside the manual block changed.')
}
export function assertManualEvaluation(evaluation, rows, quantity = 99, cleared = false) {
  assert.ok(evaluation.capacity_valid && evaluation.computation_complete && evaluation.executable, 'Manual evaluation is incomplete or not executable.')
  const removed = cleared ? rows.length * 100 : 100 - quantity
  const totals = { expected: 420000 - removed, on_time: 420000 - removed, late: 0, gap: removed, pending: 0, unallocated: 0, outside_window: 0 }
  for (const [key, expected] of Object.entries(totals)) assert.equal(evaluation.output_totals[key], expected, `Independent manual output total ${key} differs.`)
  assert.equal(evaluation.weekly_results.length, 600, 'Complete 600-row weekly coverage is required.')
  const datedPairs = new Set(evaluation.weekly_results.map(row => `${row.week}:${row.model_code}`))
  assert.equal(datedPairs.size, 600, 'Weekly result identities repeat.')
  for (let week = 0; week < 15; week++) for (let model = 0; model < 40; model++) {
    const date = new Date(Date.UTC(2026, 9, 12 + 7 * week)).toISOString().slice(0, 10)
    assert.ok(datedPairs.has(`${date}:M${String(model).padStart(2, '0')}`), 'A dated model/week result is missing.')
  }
  for (const row of evaluation.weekly_results) {
    const affected = row.week === '2027-01-18' && rows.some(changed => changed.model_code === row.model_code)
    const gap = affected ? cleared ? 100 : 100 - quantity : 0
    assert.equal(row.demand, 700, 'A manual action changed demand.')
    assert.equal(row.on_time, 700 - gap, 'An unaffected or final-week on-time result differs.')
    assert.equal(row.gap, gap, 'An unaffected or final-week gap differs.')
    assert.equal(row.late, 0, 'A manual action created late attribution.')
    assert.equal(row.pending, 0, 'A manual action created pending attribution.')
  }
}
export function failEntry(entry, error, reason) {
  entry.status = 'fail'
  entry.failure_phase = entry.phase
  entry.reason = reason ?? String(error?.message ?? error?.name ?? 'Unknown failure').split('\n')[0].slice(0, 300)
  entry.measurement_started = Number.isFinite(entry.clicked_ms)
  if (!entry.measurement_started) entry.seconds = null
}
export function terminal(state) { return ['completed', 'stopped', 'interrupted', 'failed'].includes(state) }
