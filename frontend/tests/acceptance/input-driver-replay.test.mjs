import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtempSync, mkdirSync, appendFileSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { consumeInputResponse, journalInputAttempt } from './input-trace-collector.mjs'
import { collectInputDriverReplies, replayInputDriverTrace } from './input-driver-replay.mjs'
import { replayInputTrace } from './input-trace-replay.mjs'
const sha = bytes => createHash('sha256').update(bytes).digest('hex')
const owner = { pid: 7, start: '1234', attempt: '00000000-0000-4000-8000-000000000001' }

test('one existing callback write durably retains primitives after the final buffers are lost', async () => {
  const directory = mkdtempSync(resolve(tmpdir(), 'auto-plan-input-journal-')), file = resolve(directory, 'events.ndjson')
  try {
    let bodies = 0, captures = 0, writes = 0
    const trace = { events: [], attempts: [] }, entry = { editor: 1, sequence: 2, phase: 'preview', status: 'preview' }
    const bytes = Buffer.from('{"valid":true,"base_version":1}')
    await consumeInputResponse({ headers: () => ({ 'x-auto-plan-test-input': '1'.repeat(32) }), status: () => 200,
      body: async () => { bodies++; return bytes } }, entry, value => { captures++; assert.equal(value, bytes); return { path: 'bodies/' + sha(bytes) + '.json', sha256: sha(bytes) } }, trace)
    journalInputAttempt(entry, (phase, value) => { writes++; appendFileSync(file, JSON.stringify({ phase, epoch_ms: 0, ...value }) + '\n') }, { trace: true })
    trace.events.length = 0; trace.attempts.length = 0; entry.input_trace.node_phases.length = 0
    const retained = JSON.parse(readFileSync(file))
    assert.equal(bodies, 1); assert.equal(captures, 1); assert.equal(writes, 1)
    assert.equal(retained.phase, 'preview'); assert.equal(retained.input_trace.response_id, '1'.repeat(32))
    assert.equal(retained.input_trace.node_phases.length, 6)
    let disabled
    journalInputAttempt(entry, (_phase, value) => { disabled = value })
    assert.deepEqual(Object.keys(disabled), ['editor','phase','status','response_status','request_artifact','response_artifact'])
    assert.equal(disabled.input_trace, undefined)
  } finally { rmSync(directory, { recursive: true }) }
})

test('original errors retain their identity and real phase prefix in the existing callback', async () => {
  const error = new Error('original body failure'), entry = { editor: 2, sequence: 1, phase: 'apply' }, trace = { events: [], attempts: [] }
  await assert.rejects(consumeInputResponse({ headers: () => ({}), body: async () => { throw error } }, entry, () => assert.fail(), trace), value => value === error)
  let retained
  journalInputAttempt(entry, (_phase, value) => { retained = JSON.parse(JSON.stringify(value)) }, { trace: true })
  assert.equal(retained.input_trace.response_id, null)
  assert.deepEqual(retained.input_trace.node_phases.map(row => row.phase), ['body_await_started'])
})

function fixture() {
  const id = '1'.repeat(32), request = Buffer.from('{"base_version":1,"changes":{"models":[{"code":"M","name":"Changed"}]}}'), response = Buffer.from('{"valid":true,"base_version":1}')
  const pointer = bytes => ({ path: 'bodies/' + sha(bytes) + '.json', sha256: sha(bytes) })
  const files = new Map([[pointer(request).path, request], [pointer(response).path, response]])
  const times = [1,3,4,8,9,14], phases = ['body_await_started','body_await_complete','capture_started','capture_complete','json_parse_started','json_parse_complete']
  const entry = { phase: 'preview', editor: 1, response_status: 200, request_artifact: pointer(request), response_artifact: pointer(response),
    input_trace: { response_id: id, node_phases: phases.map((phase, i) => ({ phase, editor: 1, sequence: 1, response_id: id, at_ms: times[i], clock_domain: 'node.performance.now:7' })) } }
  const server = { id, method: 'POST', path: '/planning/api/inputs/preview', status: 200, request_complete: true, response_complete: true,
    request_sha256: sha(request), request_length: request.length }
  const value = { entry, server, files, owner: { ...owner } }
  value.options = () => {
    const journalBytes = Buffer.from(JSON.stringify(value.entry) + '\n'), serverBytes = Buffer.from(JSON.stringify(value.server) + '\n')
    return { journalBytes, journalSha256: sha(journalBytes), serverBytes, serverSha256: sha(serverBytes), owner: value.owner, readArtifact: pointer => files.get(pointer.path) }
  }
  return value
}

test('driver facts correlate complete bytes and owned Node intervals without a browser claim', () => {
  const result = collectInputDriverReplies(fixture().options())
  assert.equal(result.status, 'correlated'); assert.equal(result.replies.length, 1)
  assert.deepEqual(result.replies[0].node_elapsed_ms, { body_await: 2, capture: 4, json_parse: 5 })
  for (const field of ['server_response_bytes','browser_consumption','native_click','consumed_pins']) assert.equal(result.replies[0][field], 'unknown')
  for (const field of ['browser_protocol','source_closure','input_acknowledgments','restoration']) assert.equal(result[field], 'unconfirmed')
  assert.equal(result.acceptance, 'not_run'); assert.deepEqual(result.repeats, [])
  assert.throws(() => replayInputTrace({ input_trace: {} }), /retained artifact binding/)
})

test('raw bytes, IDs, status, paths and owned Node chronology fail closed', () => {
  const mutations = [
    v => { delete v.entry.input_trace }, v => { v.entry.input_trace.response_id = '2'.repeat(32) },
    v => { v.server.path = '/planning/api/inputs/apply' }, v => { v.server.method = 'GET' },
    v => { v.server.status = 409 }, v => { v.entry.response_status = true },
    v => { v.server.request_complete = false }, v => { v.server.response_complete = false },
    v => { v.server.request_length++ }, v => { v.server.request_sha256 = '0'.repeat(64) },
    v => { v.files.set(v.entry.response_artifact.path, Buffer.from('{}')) },
    v => { v.entry.request_artifact.path = '../outside.json' },
    v => { v.entry.input_trace.node_phases[1].at_ms = 0 },
    v => { v.entry.input_trace.node_phases[2].clock_domain = 'node.performance.now:8' },
    v => { for (const row of v.entry.input_trace.node_phases) row.clock_domain = 'node.performance.now:8' },
    v => { v.entry.input_trace.node_phases[3].sequence = 2 },
    v => { v.entry.input_trace.node_phases[4].response_id = '2'.repeat(32) },
    v => { v.entry.input_trace.node_phases[0].extra = {} }, v => { v.entry.input_trace.node_phases.pop() },
  ]
  for (const mutate of mutations) {
    const value = fixture(); mutate(value); const result = collectInputDriverReplies(value.options())
    assert.equal(result.status, 'partial'); assert.equal(result.replies.length, 0); assert.equal(result.unknown.length, 1)
  }
  for (const key of ['journal', 'server']) {
    const options = fixture().options(); options[key + 'Sha256'] = '0'.repeat(64); assert.throws(() => collectInputDriverReplies(options))
    const duplicate = fixture().options(); duplicate[key + 'Bytes'] = Buffer.concat([duplicate[key + 'Bytes'], duplicate[key + 'Bytes']]); duplicate[key + 'Sha256'] = sha(duplicate[key + 'Bytes'])
    const result = collectInputDriverReplies(duplicate); assert.equal(result.status, 'partial'); assert.equal(result.replies.length, 0)
    const partial = fixture().options(); partial[key + 'Bytes'] = partial[key + 'Bytes'].subarray(0, partial[key + 'Bytes'].length - 1); partial[key + 'Sha256'] = sha(partial[key + 'Bytes'])
    assert.equal(collectInputDriverReplies(partial).status, 'partial')
  }
})

test('offline binding and CLI write only a new separate result and reject rebinding', () => {
  const directory = mkdtempSync(resolve(tmpdir(), 'auto-plan-input-driver-'))
  try {
    const value = fixture(), options = value.options(), bindings = { artifacts_root: directory }
    const put = (name, bytes) => { const path = resolve(directory, name); writeFileSync(path, bytes); return { path, sha256: sha(bytes) } }
    bindings.journal = put('events.ndjson', options.journalBytes); bindings.server = put('server.ndjson', options.serverBytes)
    bindings.owner = put('owner.json', Buffer.from(JSON.stringify(owner)))
    const memory = new Map([...value.files].map(([path, bytes]) => [resolve(directory, path), bytes]))
    for (const [name, bytes] of [[bindings.journal.path, options.journalBytes], [bindings.server.path, options.serverBytes], [bindings.owner.path, Buffer.from(JSON.stringify(owner))]]) memory.set(name, bytes)
    assert.equal(replayInputDriverTrace(bindings, path => memory.get(path)).status, 'correlated')
    for (const key of ['journal','server','owner']) {
      const wrong = structuredClone(bindings); wrong[key].sha256 = '0'.repeat(64); assert.throws(() => replayInputDriverTrace(wrong, path => memory.get(path)))
    }
    // The CLI's artifact reader is real; keep all fixture files under its owned directory.
    mkdirSync(resolve(directory, 'bodies'))
    for (const [path, bytes] of value.files) writeFileSync(resolve(directory, path), bytes)
    const config = put('bindings.json', Buffer.from(JSON.stringify(bindings))), output = resolve(directory, 'driver-result.json')
    const run = () => spawnSync(process.execPath, [fileURLToPath(new URL('./input-driver-replay.mjs', import.meta.url)), config.path, output], { encoding: 'utf8', timeout: 10000 })
    const first = run(); assert.equal(first.status, 0, first.stderr)
    const original = readFileSync(output); assert.equal(JSON.parse(original).status, 'correlated')
    const second = run(); assert.equal(second.status, 1); assert.match(second.stderr, /EEXIST/)
    assert.deepEqual(readFileSync(output), original)
  } finally { rmSync(directory, { recursive: true }) }
})
