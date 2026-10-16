import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
const sha = bytes => createHash('sha256').update(bytes).digest('hex')
const one = (rows, predicate, message) => {
  const selected = rows.filter(predicate)
  assert.equal(selected.length, 1, message)
  return selected[0]
}
const finite = value => Number.isFinite(value) && value >= 0
const sequence = value => Number.isInteger(value) && value > 0
function marker(value, clock) {
  assert.ok(finite(value.at_ms), 'A finite browser phase timestamp is required.')
  assert.equal(value.clock_domain, clock, 'Browser phases belong to different document clocks.')
  return value
}
function documentBinding(events, evidence) {
  if (!evidence) return { status: 'unknown', reason: 'No immutable single-document export binding was supplied.' }
  assert.equal(sha(evidence.bytes), evidence.sha256, 'Browser export bytes differ from their trusted hash.')
  const pages = JSON.parse(evidence.bytes)
  assert.ok(Number.isInteger(evidence.page_index) && evidence.page_index >= 0)
  assert.deepEqual(pages[evidence.page_index], events, 'Events differ from the bound single-document export batch.')
  return { status: 'confirmed', artifact_sha256: evidence.sha256, page_index: evidence.page_index }
}
function resourceBinding(events, phases, path, pageOrigin, document) {
  if (!pageOrigin || document.status !== 'confirmed') return { status: 'unknown', reason: 'Resource attribution needs a bound document batch and page origin.' }
  const origin = new URL(pageOrigin)
  assert.ok(origin.origin === pageOrigin && !origin.username && !origin.password, 'Bind the exact page origin without credentials or path.')
  const url = new URL(path, origin).href
  const sameUrl = events.filter(row => row.phase === 'resource_finished' && row.resource_url === url)
  if (sameUrl.some(row => row.clock_domain !== phases.received.clock_domain || !finite(row.start_ms) || !finite(row.response_end_ms) || !finite(row.at_ms) || row.response_end_ms < row.start_ms || row.at_ms < row.response_end_ms)) {
    return { status: 'unknown', reason: 'Resource evidence has invalid times or a different document clock.' }
  }
  const matches = sameUrl.filter(row => row.start_ms >= phases.started.at_ms && row.start_ms <= phases.received.at_ms)
  if (matches.length !== 1) return { status: 'unknown', reason: 'No unique same-page resource fits the actual fetch/consumption window.' }
  const row = matches[0]
  if (row.response_end_ms > phases.completed.at_ms) return { status: 'unknown', reason: 'Resource completion follows JSON consumption.' }
  return { status: 'confirmed', resource_url: url, start_ms: row.start_ms, response_end_ms: row.response_end_ms, callback_ms: row.at_ms, clock_domain: row.clock_domain }
}

function responseBinding(record, observed, evidence) {
  const server = { status: 'unknown', reason: 'Original server trace has no response-byte digest/length.' }
  if (!evidence) return { request_id: record.id, retained_body: { status: 'unknown', reason: 'No immutable retained response bytes supplied.' }, server_body: server }
  const hash = sha(evidence)
  assert.equal(hash, record.response_sha256, 'Retained response bytes differ from the original Node response hash.')
  assert.deepEqual(JSON.parse(evidence), record.response, 'Parsed response differs from immutable retained bytes.')
  if (record.response_length !== undefined) assert.equal(evidence.length, record.response_length)
  if (observed.response_sha256 !== undefined && observed.response_length !== undefined) {
    assert.equal(hash, observed.response_sha256); assert.equal(evidence.length, observed.response_length)
    server.status = 'confirmed'; delete server.reason
  }
  return { request_id: record.id, retained_body: { status: 'confirmed', sha256: hash, length: evidence.length }, server_body: server }
}

export function joinInputTransition({ events, preview, apply, server, documentEvidence, pageOrigin,
  requireSameDocument = false, requireResource = false, responseEvidence, requireResponseBody = false }) {
  assert.equal(preview.response.valid, true, 'Only an accepted valid preview can authorize apply.')
  assert.deepEqual(apply.command, { base_version: preview.response.base_version, changes: preview.response.changes }, 'Native apply differs from its normalized accepted preview.')
  const ready = one(events, row => row.phase === 'preview_dom_ready' && row.request_id === preview.id, 'The current preview needs unique DOM readiness.')
  assert.ok(typeof ready.clock_domain === 'string' && ready.clock_domain.length)
  marker(ready, ready.clock_domain)
  assert.equal(ready.base_version, preview.response.base_version)
  assert.equal(ready.apply_enabled, true)
  const document = documentBinding(events, documentEvidence)
  if (requireSameDocument || requireResource) assert.equal(document.status, 'confirmed', 'Requested document attribution is unknown.')
  const resources = [], requests = [], responses = []
  for (const record of [preview, apply]) {
    assert.ok(record.path.endsWith(record === preview ? '/api/inputs/preview' : '/api/inputs/apply'), 'Request path has another operation.')
    const observed = one(server, row => row.id === record.id, 'An exact unique complete server record is required.')
    assert.ok(observed.request_complete && observed.response_complete, 'Server receipt or response completion is unknown.')
    assert.equal(observed.status, record.status)
    assert.equal(observed.request_sha256, sha(record.request_bytes))
    assert.equal(observed.request_length, record.request_bytes.length)
    assert.equal(observed.path, record.path); assert.equal(observed.method, 'POST')
    const response = responseBinding(record, observed, responseEvidence?.[record.id])
    if (requireResponseBody) assert.equal(response.retained_body.status, 'confirmed', 'Requested retained response attribution is unknown.')
    responses.push(response)
    const phases = {}
    for (const [key, phase] of [['received','fetch_received'],['jsonStarted','json_started'],['completed','json_completed']]) {
      phases[key] = marker(one(events, row => row.phase === phase && row.request_id === record.id, 'Browser consumption attribution is missing or ambiguous.'), ready.clock_domain)
    }
    assert.equal(phases.received.status, record.status, 'Browser received status differs from the actual response.')
    for (const value of [phases.jsonStarted, phases.completed]) if (value.status !== undefined) assert.equal(value.status, record.status, 'Captured JSON status differs from its matched fetch response.')
    const client = phases.received.client_sequence
    assert.ok(sequence(client), 'A positive unique client sequence is required.')
    for (const value of [phases.jsonStarted, phases.completed]) assert.equal(value.client_sequence, client)
    for (const phase of ['fetch_received','json_started','json_completed']) one(events, row => row.phase === phase && row.client_sequence === client, 'A client sequence has duplicate or mixed request phases.')
    phases.started = marker(one(events, row => row.phase === 'fetch_started' && row.client_sequence === client, 'A unique fetch start is required.'), ready.clock_domain)
    assert.equal(phases.started.path, record.path); assert.equal(phases.started.method, 'POST')
    assert.ok(sequence(phases.started.native_sequence))
    phases.native = marker(one(events, row => row.phase === 'native_click' && row.native_sequence === phases.started.native_sequence, 'A unique native action is required.'), ready.clock_domain)
    assert.equal(phases.native.disabled, false)
    assert.equal(phases.native.action, record === preview ? 'preview' : 'apply')
    const ordered = [phases.native, phases.started, phases.received, phases.jsonStarted, phases.completed]
    for (let index = 1; index < ordered.length; index++) assert.ok(ordered[index - 1].at_ms <= ordered[index].at_ms, 'Browser request phases are out of order.')
    if (record === preview) {
      assert.equal(phases.completed.base_version, preview.response.base_version, 'Consumed preview pin differs from its immutable response.')
      assert.ok(phases.completed.at_ms <= ready.at_ms, 'DOM readiness precedes preview JSON completion.')
    } else {
      assert.equal(phases.native.review_request_id, preview.id, 'Native apply used another preview.')
      assert.ok(ready.at_ms <= phases.native.at_ms, 'Native apply precedes current preview DOM readiness.')
      if (record.status === 200) assert.equal(phases.completed.input_version, apply.response.input_version, 'Consumed saved version differs from its immutable response.')
    }
    const resource = resourceBinding(events, phases, record.path, pageOrigin, document)
    if (requireResource) assert.equal(resource.status, 'confirmed', 'Requested resource attribution is unknown.')
    resources.push({ request_id: record.id, ...resource })
    requests.push({ request_id: record.id, native_ms: phases.native.at_ms, fetch_start_ms: phases.started.at_ms,
      headers_ms: phases.received.at_ms, json_start_ms: phases.jsonStarted.at_ms, json_complete_ms: phases.completed.at_ms })
  }
  return { status: 'pass', preview_id: preview.id, apply_id: apply.id, document, resources, responses, requests,
    browser_clock: ready.clock_domain, server_clock: one(server, row => row.id === apply.id).clock_domain,
    interpretation: 'separate clock domains; protocol/consumption correlation only; unknown attribution earns no claim' }
}
