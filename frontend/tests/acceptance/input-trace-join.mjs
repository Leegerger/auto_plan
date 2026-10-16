import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
const sha = bytes => createHash('sha256').update(bytes).digest('hex')

export function joinInputTransition({ events, preview, apply, server }) {
  assert.deepEqual(apply.command, { base_version: preview.response.base_version, changes: preview.response.changes }, 'Native apply differs from its normalized accepted preview.')
  for (const record of [preview, apply]) {
    const matches = server.filter(row => row.id === record.id)
    assert.equal(matches.length, 1, 'An exact unique complete server record is required.')
    const observed = matches[0]
    assert.ok(observed.request_complete && observed.response_complete, 'Server receipt or response completion is unknown.')
    assert.equal(observed.status, record.status)
    assert.equal(observed.request_sha256, sha(record.request_bytes))
    assert.equal(observed.request_length, record.request_bytes.length)
    assert.equal(observed.path, record.path)
    assert.equal(observed.method, 'POST')
    for (const phase of ['fetch_received', 'json_started', 'json_completed']) {
      assert.equal(events.filter(row => row.phase === phase && row.request_id === record.id).length, 1, 'Browser consumption attribution is missing or ambiguous.')
    }
    const completed = events.find(row => row.phase === 'json_completed' && row.request_id === record.id)
    if (record === preview) assert.equal(completed.base_version, preview.response.base_version, 'Consumed preview pin differs from its immutable response.')
    else if (record.status === 200) assert.equal(completed.input_version, apply.response.input_version, 'Consumed saved version differs from its immutable response.')
    const received = events.find(row => row.phase === 'fetch_received' && row.request_id === record.id)
    assert.equal(received.status, record.status)
    const started = events.find(row => row.phase === 'fetch_started' && row.client_sequence === received.client_sequence)
    assert.ok(started && started.path === record.path)
    const native = events.find(row => row.phase === 'native_click' && row.native_sequence === started.native_sequence)
    assert.ok(native && !native.disabled && native.action === (record === preview ? 'preview' : 'apply'), 'A real enabled native action is required.')
    if (record === apply) assert.equal(native.review_request_id, preview.id, 'Native apply used another preview.')
  }
  const ready = events.filter(row => row.phase === 'preview_dom_ready' && row.request_id === preview.id)
  assert.equal(ready.length, 1, 'The current preview needs actual DOM readiness.')
  assert.equal(ready[0].base_version, preview.response.base_version)
  const applyFetch = events.find(row => row.phase === 'fetch_received' && row.request_id === apply.id)
  const start = events.find(row => row.phase === 'fetch_started' && row.client_sequence === applyFetch.client_sequence)
  const click = events.find(row => row.phase === 'native_click' && row.native_sequence === start.native_sequence)
  assert.ok(ready[0].at_ms <= click.at_ms)
  return { status: 'pass', preview_id: preview.id, apply_id: apply.id,
    browser_clock: ready[0].clock_domain, server_clock: server.find(row => row.id === apply.id).clock_domain,
    interpretation: 'separate clock domains; protocol/consumption correlation only' }
}
