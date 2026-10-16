/** Offline server/driver facts only. This cannot establish browser consumption. */
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'
const sha = bytes => createHash('sha256').update(bytes).digest('hex')
const phases = ['body_await_started', 'body_await_complete', 'capture_started', 'capture_complete', 'json_parse_started', 'json_parse_complete']
const digest = value => assert.match(value ?? '', /^[a-f0-9]{64}$/)
const lines = bytes => {
  const parts = bytes.toString('utf8').split('\n'), trailing = parts.pop()
  return { rows: parts.filter(Boolean).map(JSON.parse), trailing: Boolean(trailing) }
}

export function collectInputDriverReplies({ journalBytes, journalSha256, serverBytes, serverSha256, owner, readArtifact }) {
  digest(journalSha256); digest(serverSha256)
  assert.equal(sha(journalBytes), journalSha256); assert.equal(sha(serverBytes), serverSha256)
  const journal = lines(journalBytes), ledger = lines(serverBytes), byId = new Map(), duplicate = new Set()
  for (const row of ledger.rows) {
    if (byId.has(row.id)) duplicate.add(row.id)
    byId.set(row.id, row)
  }
  assert.ok(Number.isSafeInteger(owner?.pid) && owner.pid > 0); assert.match(owner.start ?? '', /^[0-9]+$/)
  assert.match(owner.attempt ?? '', /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/)
  const attempts = journal.rows.filter(row => ['preview', 'apply'].includes(row.phase)), counts = new Map(), replies = [], unknown = []
  for (const entry of attempts) {
    const id = entry.input_trace?.response_id
    counts.set(id, (counts.get(id) ?? 0) + 1)
  }
  const artifact = pointer => {
    digest(pointer?.sha256)
    assert.equal(pointer.path, 'bodies/' + pointer.sha256 + '.json', 'Unexpected retained body path.')
    const bytes = readArtifact(pointer)
    assert.ok(Buffer.isBuffer(bytes)); assert.equal(sha(bytes), pointer.sha256, 'Retained body differs from its capture hash.')
    return bytes
  }
  for (const entry of attempts) {
    try {
      const trace = entry.input_trace, id = trace?.response_id
      assert.match(id ?? '', /^[a-f0-9]{32}$/, 'No durable actual response ID.')
      assert.ok(counts.get(id) === 1 && !duplicate.has(id), 'Duplicate response ID.')
      assert.ok(Number.isSafeInteger(entry.editor) && entry.editor >= 1 && entry.editor <= 4)
      const server = byId.get(id)
      assert.ok(server, 'No complete retained server record.')
      assert.equal(server.method, 'POST'); assert.equal(server.path, '/planning/api/inputs/' + entry.phase)
      assert.equal(server.request_complete, true); assert.equal(server.response_complete, true)
      assert.ok(Number.isSafeInteger(entry.response_status) && entry.response_status >= 100 && entry.response_status <= 599)
      assert.equal(server.status, entry.response_status)
      const request = artifact(entry.request_artifact), response = artifact(entry.response_artifact)
      assert.equal(server.request_sha256, sha(request)); assert.equal(server.request_length, request.length)
      const command = JSON.parse(request); JSON.parse(response)
      assert.ok(Number.isSafeInteger(command.base_version) && command.base_version >= 0)
      assert.ok(command.changes && typeof command.changes === 'object' && !Array.isArray(command.changes))
      const markers = trace.node_phases
      assert.ok(Array.isArray(markers) && markers.length > 0 && markers.length <= phases.length, 'Invalid Node phase prefix.')
      const domain = markers[0].clock_domain, sequence = markers[0].sequence
      assert.match(domain ?? '', /^node\.performance\.now:[1-9][0-9]*$/)
      assert.equal(domain, 'node.performance.now:' + owner.pid, 'Node phase does not belong to the retained worker identity.')
      assert.ok(Number.isSafeInteger(sequence) && sequence > 0)
      for (const [index, marker] of markers.entries()) {
        assert.deepEqual(Object.keys(marker).sort(), ['at_ms','clock_domain','editor','phase','response_id','sequence'])
        assert.equal(marker.phase, phases[index]); assert.equal(marker.response_id, id)
        assert.equal(marker.editor, entry.editor); assert.equal(marker.sequence, sequence); assert.equal(marker.clock_domain, domain)
        assert.ok(Number.isFinite(marker.at_ms) && marker.at_ms >= 0 && (!index || marker.at_ms >= markers[index - 1].at_ms))
      }
      assert.equal(markers.length, phases.length, 'Incomplete Node phase prefix.')
      replies.push({ editor: entry.editor, phase: entry.phase, sequence, response_id: id, status: entry.response_status,
        request_sha256: sha(request), retained_reply_sha256: sha(response), clock_domain: domain,
        node_elapsed_ms: { body_await: markers[1].at_ms - markers[0].at_ms, capture: markers[3].at_ms - markers[2].at_ms,
          json_parse: markers[5].at_ms - markers[4].at_ms },
        server_request: 'confirmed', retained_reply: 'confirmed', server_response_bytes: 'unknown',
        browser_consumption: 'unknown', native_click: 'unknown', consumed_pins: 'unknown' })
    } catch (error) {
      unknown.push({ editor: entry.editor, phase: entry.phase, response_id: entry.input_trace?.response_id ?? null,
        reason: String(error.message).split('\n')[0] })
    }
  }
  return { kind: 'server_driver_reply_diagnostics_v1', status: attempts.length && !unknown.length && !journal.trailing && !ledger.trailing ? 'correlated' : 'partial',
    scope: 'Retained input-attempt callbacks only; in-flight and unjournaled attempts are not inferred.',
    replies, unknown, trailing_journal_record: journal.trailing, trailing_server_record: ledger.trailing,
    journal_sha256: journalSha256, server_sha256: serverSha256, owner, acceptance: 'not_run', repeats: [],
    browser_protocol: 'unconfirmed', source_closure: 'unconfirmed', input_acknowledgments: 'unconfirmed', restoration: 'unconfirmed' }
}

export function replayInputDriverTrace(bindings, read = readFileSync) {
  const checked = pointer => {
    assert.ok(pointer?.path); digest(pointer.sha256)
    const bytes = read(pointer.path); assert.equal(sha(bytes), pointer.sha256); return bytes
  }
  assert.ok(bindings.artifacts_root)
  const owner = JSON.parse(checked(bindings.owner))
  return collectInputDriverReplies({ journalBytes: checked(bindings.journal), journalSha256: bindings.journal.sha256, owner,
    serverBytes: checked(bindings.server), serverSha256: bindings.server.sha256,
    readArtifact: pointer => read(resolve(bindings.artifacts_root, pointer.path)) })
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  assert.ok(process.argv[2] && process.argv[3], 'Provide retained bindings and a NEW result path.')
  const result = replayInputDriverTrace(JSON.parse(readFileSync(resolve(process.argv[2]))))
  writeFileSync(resolve(process.argv[3]), JSON.stringify(result, null, 2) + '\n', { flag: 'wx' })
  process.stdout.write(JSON.stringify({ status: result.status, replies: result.replies.length, unknown: result.unknown.length, browser_protocol: result.browser_protocol }) + '\n')
  if (result.status !== 'correlated') process.exitCode = 1
}
