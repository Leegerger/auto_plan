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

test('join fails closed for missing phases, wrong ID/body/pin, stale DOM or incomplete server receipt', () => {
  const command = { base_version: 1, changes: { lines: [{ code: 'L', name: 'Changed', active: true }] } }
  const bytes = Buffer.from(JSON.stringify(command)), hash = createHash('sha256').update(bytes).digest('hex')
  const preview = { id: 'p', path: '/planning/api/inputs/preview', status: 200, request_bytes: bytes, response: { ...command, valid: true } }
  const apply = { id: 'a', path: '/planning/api/inputs/apply', status: 409, request_bytes: bytes, command }
  const server = [preview, apply].map(row => ({ id: row.id, path: row.path, method: 'POST', status: row.status, request_complete: true, response_complete: true, request_sha256: hash, request_length: bytes.length, clock_domain: 'server-unit' }))
  const events = []
  for (const [index, row] of [preview, apply].entries()) {
    events.push({ phase: 'native_click', action: index ? 'apply' : 'preview', disabled: false, native_sequence: index, review_request_id: index ? 'p' : null, at_ms: index * 10, clock_domain: 'browser-unit' },
      { phase: 'fetch_started', path: row.path, client_sequence: index, native_sequence: index },
      ...['fetch_received','json_started','json_completed'].map(phase => ({ phase, client_sequence: index, request_id: row.id, status: row.status, base_version: index ? null : 1, input_version: null })))
  }
  events.push({ phase: 'preview_dom_ready', request_id: 'p', base_version: 1, at_ms: 5, clock_domain: 'browser-unit' })
  assert.equal(joinInputTransition({ events, preview, apply, server }).status, 'pass')
  for (const mutate of [
    value => { value.events = [] },
    value => { value.apply.id = 'wrong' },
    value => { value.apply.request_bytes = Buffer.from('wrong') },
    value => { value.apply.command.base_version = 2 },
    value => { value.events.find(row => row.phase === 'json_completed' && row.request_id === 'p').base_version = 99 },
    value => { value.events.find(row => row.phase === 'preview_dom_ready').request_id = 'old' },
    value => { value.server[1].request_complete = false },
    value => { value.server.push(structuredClone(value.server[1])) },
  ]) {
    const value = { events: structuredClone(events), preview: { ...preview }, apply: { ...apply, command: structuredClone(command) }, server: structuredClone(server) }
    mutate(value); assert.throws(() => joinInputTransition(value))
  }
})
