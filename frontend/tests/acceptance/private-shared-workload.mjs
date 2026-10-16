/** Explicit full-source shared epochs; the original small checker stays separate. */
import assert from 'node:assert/strict'
import { isDeepStrictEqual } from 'node:util'
import { createHash } from 'node:crypto'
import { assertFullPrivateEpochResult, assertEpochPlan } from './private-shared-epoch.mjs'
import { canonical, assertManualRequest } from './contracts.mjs'
export const FULL_SHARED_ID = 'r20-loose-private-shared-save-v1'
export const FULL_SHARED_CONTRACT = { kind: 'actual_commit_reply_loss_original_uuid', primary_model: 'M00', background_model: 'M39', revisions: [1,2,3], input_version: 1, latest_adoption: 1, shared_commits: 2, source_total: 420000, saved_total: 419999, background_saved_total: 419998, restore: 'separate_ordinary_uuid_full_original_plan', clock: 'first_native_save_click_through_verified_complete_result_two_frames' }
const same = (a, b, message) => assert.ok(isDeepStrictEqual(a, b), message)

export function sharedEpochExpectation({ snapshot, context, epochs, command, target, parent }) {
  assert.equal(snapshot.plan.segments.length, 4200); assert.equal(snapshot.plan.switches.length, 418)
  assert.equal(snapshot.inputs.business.demands.length, 600)
  assert.ok([1,2,3].includes(parent) && epochs.has(parent), 'An undeclared shared parent was returned.')
  assert.ok([1,2,3].includes(command.base_revision)); assert.equal(command.input_version, 1); assert.equal(command.latest_adoption, 1)
  const gaps = new Set(parent === 2 ? ['M00'] : []), deltas = [], effects = []
  if (target) {
    assert.ok(['S-2027-01-24-M00', 'S-2027-01-24-M39'].includes(target.segment_id))
    assert.equal(target.operation.quantity, 99)
    const row = snapshot.plan.segments.find(row => row.id === target.segment_id)
    assertManualRequest(command, [row], 'change_quantity', 99)
    gaps.add(row.model_code)
    deltas.push({ segment_id: row.id, quantity: 99, hours_lower_exclusive: '49/50', hours_upper_inclusive: '18' })
    effects.push(['CAP', row.line_code, row.date], ...snapshot.inputs.masters.lines.map(line => ['CELL', row.date, line.code, row.model_code]), ...snapshot.inputs.business.demands.filter(d => d.model_code === row.model_code).map(d => ['REQ', row.model_code, d.week]))
    assert.equal(effects.length, 34)
  } else same(command.operations, [], 'Settled undo/display must submit no actions.')
  const weekly = snapshot.inputs.business.demands.map(row => {
    assert.equal(row.quantity, 700)
    const gap = Number(row.week === '2027-01-18' && gaps.has(row.model_code))
    return { model_code: row.model_code, week: row.week, demand: 700, on_time: 700-gap, gap, late: 0, pending: 0 }
  })
  const gap = gaps.size, objective = Array(30).fill('0').concat(['418', String(gap), '0'])
  objective[14] = objective[29] = String(gap)
  return { snapshot, context, parentPlan: epochs.get(parent), pins: { parent_revision: parent, base_revision: command.base_revision, input_version: 1, latest_adoption: 1 }, deltas, effects, weekly, objective,
    totals: { expected: 420000-gap, on_time: 420000-gap, late: 0, gap, pending: 0, unallocated: 0, outside_window: 0 } }
}
export function assertSharedEpoch(result, expected) {
  return assertFullPrivateEpochResult(result, sharedEpochExpectation({ ...expected, parent: result.parent_revision }))
}

const sortedJSON = value => Array.isArray(value) ? value.map(sortedJSON) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sortedJSON(value[key])])) : value
export function assertJobLifetime(witnesses, job, observed, frozen) {
  assert.ok(observed.nonce && Number.isFinite(observed.request_started_epoch_ms) && observed.request_started_epoch_ms <= observed.epoch_ms, 'Actual API read window is missing.')
  const rows = witnesses.filter(row => row.kind === 'read' && row.nonce === observed.nonce)
  assert.equal(rows.length, 1, 'An exact owned API lifecycle observation is required.')
  const row = rows[0]
  same([row.job_id, row.run_id, row.generation], [job.job_id, observed.body.run_id, observed.body.generation], 'Job run/generation differs.')
  assert.equal(observed.body.state, 'running'); assert.equal(row.state, 'running'); assert.equal(row.process_alive, true)
  same([row.source_revision,row.input_version,row.latest_adoption], [frozen.source_revision,frozen.input_version,frozen.latest_adoption], 'Owned source pins differ.')
  same([observed.body.source_revision,observed.body.input_version,observed.body.latest_adoption], [frozen.source_revision,frozen.input_version,frozen.latest_adoption], 'Public source pins differ.')
  assert.equal(row.frozen_sha256, createHash('sha256').update(JSON.stringify(sortedJSON(frozen))).digest('hex'), 'The complete claimed frozen source/request differs.')
  assert.ok(Number.isInteger(row.pid) && /^\d+$/.test(row.start) && row.started_ns <= row.finished_ns)
  const spawned = witnesses.filter(value => value.kind === 'spawned' && value.job_id === row.job_id && value.run_id === row.run_id && value.generation === row.generation)
  assert.equal(spawned.length, 1); same([row.pid, row.start, row.frozen_sha256], [spawned[0].pid, spawned[0].start, spawned[0].frozen_sha256], 'Owned ComputeProcess identity/source differs.')
  assert.ok(spawned[0].at_ns <= row.started_ns)
  return row
}

export function assertFullSharedBrowserReport({ report, declaration, captured, witnesses, action }) {
  const save = report.private_shared_save, job = report.private_primary_job
  same(declaration.shared_save, FULL_SHARED_CONTRACT, 'Full shared declaration changed.')
  assert.equal(save.status, 'pass'); assert.equal(save.acknowledgment_status, null); assert.equal(save.readonly_unknown_observed, true)
  same(save.verification_command, { operation_id: save.submitted.operation_id }, 'Verification used another original UUID.')
  assert.equal(save.resolution.status, 'COMMITTED'); assert.equal(save.resolution.revision, 2)
  assert.equal(save.forwarded_original_save_count, 1)
  assert.equal(save.clock.origin_epoch_ms, save.clock.first_clicked_epoch_ms)
  const before = captured(save.epochs.before.source_artifact), saved = captured(save.epochs.saved.source_artifact), restored = captured(save.epochs.restored.source_artifact)
  assert.equal(before.source_revision, 1); assert.equal(saved.source_revision, 2); assert.equal(restored.source_revision, 3)
  const epochs = new Map([[1,before.snapshot.plan], [2,saved.snapshot.plan], [3,before.snapshot.plan]])
  const options = { snapshot: before.snapshot, context: save.original_context, epochs }
  const primary = declaration.targets[0], prepared = captured(save.prepared_preview_artifact)
  assertSharedEpoch(prepared, { ...options, command: save.prepared_command, target: primary })
  assertEpochPlan(saved.snapshot.plan, prepared.plan)
  same(canonical(saved), canonical({ ...before, source_revision: 2, snapshot: { ...before.snapshot, plan: prepared.plan } }), 'Saved full source differs.')
  same(canonical(restored), canonical({ ...before, source_revision: 3 }), 'Ordinary restoration changed source.')
  assertSharedEpoch(captured(save.display_artifact), { ...options, command: save.display_command, target: null })
  for (const [epoch, plan] of [['saved',saved.snapshot.plan],['restored',before.snapshot.plan]]) assertEpochPlan(captured(save.epochs[epoch].revision_artifact).snapshot.plan, plan)
  assert.notEqual(save.restoration.submitted.operation_id, save.submitted.operation_id); assert.equal(save.restoration.result.revision, 3)
  assert.equal(report.operations.length, 2); assert.ok(report.operations.every(row => row.status === 'COMMITTED'))
  for (let editor=1; editor<5; editor++) {
    const cycles = report.editor_actions.filter(row => row.editor === editor)
    assert.ok(cycles.length >= 2 && cycles.every(row => row.status === 'pass'))
    for (const cycle of cycles) {
      assert.equal(cycle.segment_id, 'S-2027-01-24-M39'); assert.equal(cycle.undo.queue_empty, true)
      assertSharedEpoch(captured(cycle.changed_response_artifact), { ...options, command: cycle.changed_command, target: declaration.targets[editor] })
      assertSharedEpoch(captured(cycle.undo.response_artifact), { ...options, command: cycle.undo.command, target: null })
    }
    assert.ok(cycles.some(row => row.undo_parent_revision === 3))
    const events=cycles.flatMap(row=>row.events)
    assert.ok(Math.min(...events.filter(row=>row.phase==='native_quantity_submit').map(row=>row.epoch_ms)) <= save.clock.origin_epoch_ms)
    assert.ok(Math.max(...events.filter(row=>row.phase==='complete_undo').map(row=>row.epoch_ms)) >= save.clock.rendered_epoch_ms)
  }
  const frozen = { snapshot: before.snapshot, source_revision: 1, input_version: 1, latest_adoption: 1, request: action.request }
  const first = assertJobLifetime(witnesses, job.accepted, save.running_before_save, frozen), last = assertJobLifetime(witnesses, job.accepted, save.running_after_confirmed_paint, frozen)
  same([first.pid,first.start,first.run_id,first.generation], [last.pid,last.start,last.run_id,last.generation], 'One real ComputeProcess must span native save through confirmed paint.')
  assert.ok(save.running_before_save.epoch_ms <= save.clock.origin_epoch_ms)
  assert.ok(save.running_after_confirmed_paint.request_started_epoch_ms >= save.clock.rendered_epoch_ms)
  assertJobLifetime(witnesses, job.accepted, job.running_after_primary_undo, frozen)
  same(captured(job.source_artifact), before, 'Job acquired another full source.')
  same(captured(save.epochs.saved.revision_artifact).operations, save.submitted.operations, 'Immutable saved operations differ.')
  assert.equal(report.local_editing_contract.shared_source_restored, true)
  const candidate=captured(job.candidate_artifact).candidate
  same([candidate.source_revision,candidate.input_version,candidate.latest_adoption], [1,1,1]); assert.equal(candidate.eligibility,'executable')
  for (const key of ['capacity_valid','computation_complete','executable']) assert.equal(candidate.evaluation[key],true)
  same(candidate.snapshot, before.snapshot, 'Candidate lost frozen R1 source.'); same(candidate.request, action.request)
  same(candidate.objective, job.objective)
  const fraction=value=>canonical({hours:value}).hours.split('/').map(BigInt)
  const reference=Array(30).fill('0').concat(['418','0','0']), compare=(a,b)=>{for(let i=0;i<33;i++){const [an,ad]=fraction(a[i]),[bn,bd]=fraction(b[i]),x=an*bd,y=bn*ad;if(x!==y)return x<y?-1:1}return 0}
  assert.equal(candidate.objective.length,33); assert.ok(compare(candidate.objective,reference)<=0, 'Native candidate is worse than original full reference.')
  assert.equal(candidate.evaluation.weekly_results.length,600)
  assert.ok(candidate.evaluation.weekly_results.every(row=>row.demand===700&&row.on_time===700&&row.gap===0&&row.late===0&&row.pending===0))
  assert.ok(witnesses.some(row=>row.kind==='progress' && row.job_id===job.accepted.job_id && row.run_id===first.run_id && row.generation===first.generation && (row.route==='cp_sat'||row.route==='highs') && row.status==='restricted_model'), 'A real native backend result is required; backend temporal overlap remains unknown.')
  assert.ok(['completed','stopped'].includes(job.cleanup.state))
}
