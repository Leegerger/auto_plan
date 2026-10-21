import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtempSync, writeFileSync, mkdirSync, symlinkSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { collectPostTeardownPairs, readSealedArtifact, replayPostTeardownInputTrace } from './input-postteardown-replay.mjs'

const sha = bytes => createHash('sha256').update(bytes).digest('hex')
function example() {
  const p = 'a'.repeat(32), a = 'b'.repeat(32), clock = 'browser.performance.now:first-import-editor-1'
  const command = { base_version: 1, changes: { models: [{ code: 'M02', name: 'changed' }] } }
  const preview = { valid: true, base_version: 1, changes: command.changes }, apply = { error: 'conflict' }
  const bodies = new Map(), pointer = value => {
    const bytes = Buffer.from(JSON.stringify(value)), digest = sha(bytes), path = 'bodies/' + digest + '.json'
    bodies.set(path, bytes); return { path, sha256: digest }
  }
  const request = pointer(command), attempts = []
  for (const [index, phase, id, response, status] of [[1, 'preview', p, preview, 200], [2, 'apply', a, apply, 409]]) {
    const node_phases = ['body_await_started', 'body_await_complete', 'capture_started', 'capture_complete', 'json_parse_started', 'json_parse_complete'].map((phase, i) => ({ phase, editor: 1, sequence: index, response_id: id, at_ms: i + index * 10, clock_domain: 'node.performance.now:42' }))
    attempts.push({ editor: 1, sequence: index, phase, response_status: status, request_artifact: request, response_artifact: pointer(response), input_trace: { response_id: id, node_phases } })
  }
  const events = [], emit = (phase, at_ms, fields) => events.push({ phase, at_ms, clock_domain: clock, ...fields })
  for (const [id, sequence, offset, action, status] of [[p, 1, 0, 'preview', 200], [a, 2, 7, 'apply', 409]]) {
    emit('native_click', offset, { native_sequence: sequence, action, disabled: false, review_request_id: action === 'apply' ? p : null })
    emit('fetch_started', offset + 1, { native_sequence: sequence, client_sequence: sequence, path: '/planning/api/inputs/' + action, method: 'POST' })
    emit('fetch_received', offset + 2, { request_id: id, client_sequence: sequence, status })
    emit('json_started', offset + 3, { request_id: id, client_sequence: sequence })
    emit('json_completed', offset + 4, { request_id: id, client_sequence: sequence, base_version: action === 'preview' ? 1 : null, input_version: null })
    emit('resource_finished', offset + 6, { resource_url: 'https://localhost:18869/planning/api/inputs/' + action, start_ms: offset + 1.1, response_end_ms: offset + 3.5 })
    if (action === 'preview') emit('preview_dom_ready', 5, { request_id: p, base_version: 1, apply_enabled: true })
  }
  const server = attempts.map(row => ({ id: row.input_trace.response_id, clock_domain: 'python.perf_counter_ns:pid=43', method: 'POST', path: '/planning/api/inputs/' + row.phase, status: row.response_status, request_sha256: request.sha256, request_length: bodies.get(request.path).length, request_complete: true, response_complete: true }))
  const bundle = { attempts, journal: structuredClone(attempts), nodeEvents: attempts.flatMap(row => structuredClone(row.input_trace.node_phases)),
    owner: { worker_pid: 42, worker_start: '111', server_pid: 43, server_start: '222', attempt: '7536caab-806d-4830-86d8-ec90c5ff6562' },
    origin: 'https://localhost:18869', readArtifact: row => bodies.get(row.path) }
  return { bundle, events, server, bodies, finish() {
    bundle.browserBytes = Buffer.from(JSON.stringify([[], events, [], [], []])); bundle.browserSha256 = sha(bundle.browserBytes)
    bundle.serverBytes = Buffer.from(server.map(row => JSON.stringify(row)).join('\n') + '\n')
    return bundle
  } }
}

test('explicit captured-window proof retains body/resource facts and unknown server bytes', () => {
  const value = example(), result = collectPostTeardownPairs(value.finish())
  assert.equal(result.pairs.length, 1); assert.equal(result.pairs[0].status, 'pass')
  assert.equal(result.pairs[0].document.page_index, 1)
  assert.ok(result.pairs[0].resources.every(row => row.status === 'confirmed'))
  assert.ok(result.pairs[0].responses.every(row => row.retained_body.status === 'confirmed' && row.server_body.status === 'unknown'))
})

const failures = {
  'duplicate index identifier': value => value.bundle.attempts.push(structuredClone(value.bundle.attempts[0])),
  'duplicate server identifier': value => value.server.push(structuredClone(value.server[0])),
  'wrong server owner': value => { value.server[1].clock_domain = 'python.perf_counter_ns:pid=99' },
  'wrong Node owner': value => { value.bundle.attempts[0].input_trace.node_phases[0].clock_domain = 'node.performance.now:99' },
  'missing durable callback': value => value.bundle.journal.shift(),
  'mixed Node phases': value => { value.bundle.nodeEvents[0].at_ms = 999 },
  'wrong request path': value => { value.server[1].path = '/planning/api/inputs/preview' },
  'status mismatch': value => { value.server[1].status = 200 },
  'incomplete server response': value => { value.server[1].response_complete = false },
  'consumed preview pin': value => { value.events.find(row => row.phase === 'json_completed').base_version = 2 },
  'missing browser JSON completion': value => { value.events.splice(value.events.findIndex(row => row.phase === 'json_completed'), 1) },
  'duplicate browser request phase': value => { value.events.push(structuredClone(value.events.find(row => row.phase === 'fetch_received'))) },
  'another native review': value => { value.events.find(row => row.phase === 'native_click' && row.action === 'apply').review_request_id = 'c'.repeat(32) },
  'reversed browser phases': value => { value.events.find(row => row.phase === 'json_started').at_ms = 99 },
  'mixed document clock': value => { value.events[0].clock_domain = 'another-document' },
  'missing resource': value => { value.events.splice(value.events.findIndex(row => row.phase === 'resource_finished'), 1) },
  'ambiguous resource': value => { value.events.push(structuredClone(value.events.find(row => row.phase === 'resource_finished'))) },
  'late resource': value => { value.events.find(row => row.phase === 'resource_finished').response_end_ms = 100 },
  'wrong origin': value => { value.bundle.origin = 'https://another-origin' },
  'forged retained response': value => { const pointer = value.bundle.attempts[1].response_artifact; value.bodies.set(pointer.path, Buffer.from('{"forged":true}')) },
}
for (const [name, change] of Object.entries(failures)) test('keeps unknown: ' + name, () => {
  const value = example(); change(value)
  const result = collectPostTeardownPairs(value.finish())
  assert.ok(result.pairs.length && result.pairs.every(row => row.status === 'unknown'))
  assert.ok(result.pairs.every(row => row.reason.length < 300))
})

test('wrong exported batch and partial tails cannot earn complete attribution', () => {
  const value = example(), bundle = value.finish()
  bundle.browserBytes = Buffer.from(JSON.stringify([[], [], value.events, [], []])); bundle.browserSha256 = sha(bundle.browserBytes)
  bundle.serverBytes = Buffer.concat([bundle.serverBytes, Buffer.from('{"unfinished":')])
  const result = collectPostTeardownPairs(bundle)
  assert.equal(result.pairs[0].status, 'unknown'); assert.equal(result.incomplete_server_tail, true)
})

test('master seal enforces body bytes, length, root and symlink boundaries', () => {
  const directory = mkdtempSync(join(tmpdir(), 'postteardown-artifact-'))
  try {
    const bytes = Buffer.from('{}'), files = { 'body.json': { length: bytes.length, sha256: sha(bytes) } }
    writeFileSync(join(directory, 'body.json'), bytes)
    assert.deepEqual(readSealedArtifact(directory, files, 'body.json'), bytes)
    for (const name of ['../body.json', '/body.json', 'missing.json', './body.json']) assert.throws(() => readSealedArtifact(directory, files, name))
    assert.throws(() => readSealedArtifact(directory, { 'body.json': { ...files['body.json'], length: 9 } }, 'body.json'))
    writeFileSync(join(directory, 'body.json'), '{1')
    assert.throws(() => readSealedArtifact(directory, files, 'body.json'))
    mkdirSync(join(directory, 'inside')); symlinkSync(join(directory, 'body.json'), join(directory, 'inside', 'link.json'))
    assert.throws(() => readSealedArtifact(directory, { 'inside/link.json': files['body.json'] }, 'inside/link.json'))
  } finally { rmSync(directory, { recursive: true }) }
})

test('caller cannot replace root-trusted155 anchors with a fresh hash', () => {
  const capture_result = { path: '/tmp/other-result.json', sha256: 'a'.repeat(64) }, raw_seal = { path: '/tmp/other-seal.json', sha256: 'b'.repeat(64) }
  assert.throws(() => replayPostTeardownInputTrace({ kind: 'post-teardown-input-browser-diagnostic-v1', capture_result, raw_seal }), /Root-trusted155 result anchor differs/)
  assert.throws(() => replayPostTeardownInputTrace({ kind: 'runtime-workflow-report', capture_result, raw_seal }), /Explicit post-teardown/)
})

test('a fully retained apply still must equal its normalized accepted preview', () => {
  const value = example(), bytes = Buffer.from(JSON.stringify({ base_version: 2, changes: {} })), digest = sha(bytes)
  const pointer = { path: 'bodies/' + digest + '.json', sha256: digest }
  value.bodies.set(pointer.path, bytes)
  value.bundle.attempts[1].request_artifact = pointer; value.bundle.journal[1].request_artifact = structuredClone(pointer)
  value.server[1].request_sha256 = digest; value.server[1].request_length = bytes.length
  const result = collectPostTeardownPairs(value.finish())
  assert.equal(result.pairs[0].status, 'unknown'); assert.match(result.pairs[0].reason, /Native apply differs/)
})

test('missing actual PID-start/attempt identity is rejected before request claims', () => {
  for (const key of ['worker_start', 'server_start', 'attempt']) {
    const value = example(); delete value.bundle.owner[key]
    assert.throws(() => collectPostTeardownPairs(value.finish()), /identity is incomplete/)
  }
})

test('CLI rejects raw or existing output before reading a capture', () => {
  const module = new URL('./input-postteardown-replay.mjs', import.meta.url), directory = mkdtempSync(join(tmpdir(), 'postteardown-output-'))
  try {
    writeFileSync(join(directory, 'exists.json'), '{}')
    for (const output of ['/tmp/auto-plan-initial-import-browser-Cg0Nxk/forbidden160.json', join(directory, 'exists.json')]) {
      const child = spawnSync(process.execPath, [module.pathname, '/not-read-binding.json', output], { encoding: 'utf8', timeout: 5000 })
      assert.notEqual(child.status, 0); assert.match(child.stderr, /NEW derived output/); assert.ok(!child.stderr.includes('ENOENT'))
    }
  } finally { rmSync(directory, { recursive: true }) }
})
