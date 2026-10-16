import test from 'node:test'
import assert from 'node:assert/strict'
import { installInputConsumptionTrace } from './input-consumption-trace.mjs'
import { joinInputTransition } from './input-trace-join.mjs'
import { createHash } from 'node:crypto'

test('the original fetch and JSON calls run once and retained markers are immutable primitives', async () => {
  let fetches = 0, parses = 0, time = 0
  const response = new Response(JSON.stringify({ valid: true, base_version: 1, changes: { lines: [{ name: 'Original' }] } }), { headers: { 'x-auto-plan-test-input': 'actual' } })
  const json = response.json
  response.json = function (...args) { parses++; return json.apply(this, args) }
  const target = { performance: { now: () => ++time }, location: { href: 'https://test/inputs' }, fetch: async () => { fetches++; return response } }
  const trace = installInputConsumptionTrace(target, { basePath: '/planning', pageIdentity: 'unit' })
  assert.equal(await target.fetch('/planning/api/inputs/preview', { method: 'POST' }), response)
  const value = await response.json(); value.base_version = 99; value.changes.lines[0].name = 'Later Vue mutation'
  assert.equal(fetches, 1); assert.equal(parses, 1)
  assert.equal(trace.events.find(row => row.phase === 'json_completed').base_version, 1)
  assert.ok(trace.events.every(row => Object.values(row).every(v => v === null || ['number','string','boolean'].includes(typeof v))))
  trace.stop()
})

test('fetch and JSON error identity and original call count are preserved', async () => {
  const error = new TypeError('sentinel'), target = { performance, location: { href: 'https://test/' }, fetch: () => Promise.reject(error) }
  const trace = installInputConsumptionTrace(target, { basePath: '/planning', pageIdentity: 'unit' })
  await assert.rejects(target.fetch('/planning/api/inputs'), value => value === error)
  trace.stop()
  let calls = 0
  const response = { status: 200, headers: new Headers({ 'x-auto-plan-test-input': 'json' }), json() { calls++; return Promise.reject(error) } }
  target.fetch = async () => response
  const second = installInputConsumptionTrace(target, { basePath: '/planning', pageIdentity: 'unit' })
  await target.fetch('/planning/api/inputs'); await assert.rejects(response.json(), value => value === error)
  assert.equal(calls, 1); second.stop()
})

function joinedFixture() {
  const command = { base_version: 1, changes: { lines: [{ code: 'L', name: 'Changed', active: true }] } }
  const bytes = Buffer.from(JSON.stringify(command)), hash = createHash('sha256').update(bytes).digest('hex')
  const preview = { id: 'p', path: '/planning/api/inputs/preview', status: 200, request_bytes: bytes, response: { ...command, valid: true } }
  const apply = { id: 'a', path: '/planning/api/inputs/apply', status: 409, request_bytes: bytes, command }
  const server = [preview, apply].map(row => ({ id: row.id, path: row.path, method: 'POST', status: row.status, request_complete: true, response_complete: true, request_sha256: hash, request_length: bytes.length, clock_domain: 'server-unit' }))
  const events = [], clock_domain = 'browser-unit'
  for (const [index, row] of [preview, apply].entries()) {
    const offset = index * 10, client_sequence = index + 1, native_sequence = index + 1
    events.push({ phase: 'native_click', action: index ? 'apply' : 'preview', disabled: false, native_sequence, review_request_id: index ? 'p' : null, at_ms: offset, clock_domain },
      { phase: 'fetch_started', method: 'POST', path: row.path, client_sequence, native_sequence, at_ms: offset + 1, clock_domain },
      ...['fetch_received','json_started','json_completed'].map((phase, step) => ({ phase, client_sequence, request_id: row.id, status: row.status, at_ms: offset + step + 2, clock_domain, base_version: index ? null : 1, input_version: null })),
      { phase: 'resource_finished', resource_url: 'https://test' + row.path, start_ms: offset + 1, response_end_ms: offset + 3, at_ms: index ? 99 : 7, clock_domain })
  }
  events.push({ phase: 'preview_dom_ready', request_id: 'p', base_version: 1, apply_enabled: true, at_ms: 5, clock_domain })
  return { events, preview, apply, server, pageOrigin: 'https://test' }
}
function bound(value) {
  const bytes = Buffer.from(JSON.stringify([value.events]))
  return { ...value, documentEvidence: { bytes, sha256: createHash('sha256').update(bytes).digest('hex'), page_index: 0 } }
}

test('phases have unique finite same-clock identities and native/fetch/JSON/DOM chronology', () => {
  assert.equal(joinInputTransition(bound(joinedFixture())).status, 'pass')
  for (const mutate of [
    value => { value.events = [] },
    value => { value.apply.id = 'wrong' },
    value => { value.apply.request_bytes = Buffer.from('wrong') },
    value => { value.apply.command.base_version = 2 },
    value => { value.events.find(row => row.phase === 'json_completed').base_version = 99 },
    value => { value.events.find(row => row.phase === 'json_completed').at_ms = 0 },
    value => { delete value.events.find(row => row.phase === 'json_started').at_ms },
    value => { value.events.find(row => row.phase === 'json_started').at_ms = Infinity },
    value => { value.events.find(row => row.phase === 'json_started').clock_domain = 'other-document' },
    value => { value.events.find(row => row.phase === 'json_started').client_sequence = 99 },
    value => { value.events.find(row => row.phase === 'fetch_started').method = 'GET' },
    value => { value.events.find(row => row.phase === 'fetch_started').path = '/other' },
    value => { value.events.push(structuredClone(value.events.find(row => row.phase === 'fetch_started'))) },
    value => { value.events.push(structuredClone(value.events.find(row => row.phase === 'native_click'))) },
    value => { value.events.find(row => row.phase === 'preview_dom_ready').at_ms = 11 },
    value => { value.events.find(row => row.phase === 'preview_dom_ready').request_id = 'old' },
    value => { value.server[1].request_complete = false },
    value => { value.server.push(structuredClone(value.server[1])) },
  ]) {
    const value = joinedFixture(); mutate(value)
    assert.throws(() => joinInputTransition(bound(value)))
  }
  const equal = joinedFixture()
  for (const row of equal.events) if (row.phase !== 'resource_finished') row.at_ms = 0
  assert.equal(joinInputTransition(bound(equal)).status, 'pass', 'Equal observable timestamps remain valid.')
})

test('document and resource attribution stays unknown until its exact immutable batch/origin/window is bound', () => {
  const unbound = joinInputTransition(joinedFixture())
  assert.equal(unbound.document.status, 'unknown'); assert.ok(unbound.resources.every(row => row.status === 'unknown'))
  assert.throws(() => joinInputTransition({ ...joinedFixture(), requireSameDocument: true }))
  const positive = joinInputTransition({ ...bound(joinedFixture()), requireSameDocument: true, requireResource: true })
  assert.ok(positive.resources.every(row => row.status === 'confirmed'))
  assert.equal(positive.resources[1].callback_ms, 99, 'Resource callback may arrive after the request phases.')
  for (const mutate of [
    value => { delete value.pageOrigin },
    value => { value.events = value.events.filter(row => row.phase !== 'resource_finished') },
    value => { value.events.push(structuredClone(value.events.find(row => row.phase === 'resource_finished'))) },
    value => { value.events.find(row => row.phase === 'resource_finished').response_end_ms = 999 },
    value => { const extra = structuredClone(value.events.find(row => row.phase === 'resource_finished')); extra.response_end_ms = 999; extra.at_ms = 1000; value.events.push(extra) },
    value => { value.events.find(row => row.phase === 'resource_finished').start_ms = -1 },
    value => { value.events.find(row => row.phase === 'resource_finished').clock_domain = 'other-document' },
    value => { value.events.find(row => row.phase === 'resource_finished').resource_url = 'https://other/planning/api/inputs/preview' },
  ]) {
    const value = joinedFixture(); mutate(value)
    assert.throws(() => joinInputTransition({ ...bound(value), requireResource: true }))
  }
  const bytes = bound(joinedFixture()); bytes.documentEvidence.sha256 = '0'.repeat(64)
  assert.throws(() => joinInputTransition(bytes))
  const changed = bound(joinedFixture()); changed.events[0].at_ms = 0.5
  assert.throws(() => joinInputTransition(changed), 'A changed batch cannot reuse trusted artifact evidence.')
})


test('retained response claims bind immutable bytes/hash/content while uncaptured server attestation stays unknown', () => {
  const value = joinedFixture(), responseEvidence = {}
  for (const row of [value.preview, value.apply]) {
    row.response ??= { detail: 'conflict' }
    const bytes = Buffer.from(JSON.stringify(row.response)); responseEvidence[row.id] = bytes
    row.response_sha256 = createHash('sha256').update(bytes).digest('hex')
  }
  const result = joinInputTransition({ ...value, responseEvidence, requireResponseBody: true })
  assert.ok(result.responses.every(row => row.retained_body.status === 'confirmed' && row.server_body.status === 'unknown'))
  assert.throws(() => joinInputTransition({ ...value, requireResponseBody: true }))
  value.preview.response.extra = 'forged'
  assert.throws(() => joinInputTransition({ ...value, responseEvidence, requireResponseBody: true }))
  delete value.preview.response.extra
  value.preview.response_sha256 = '0'.repeat(64)
  assert.throws(() => joinInputTransition({ ...value, responseEvidence, requireResponseBody: true }))
  const status = joinedFixture(); status.events.find(row => row.phase === 'json_started').status = 502
  assert.throws(() => joinInputTransition(status))
})
