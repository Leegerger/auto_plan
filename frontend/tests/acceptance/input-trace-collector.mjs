/** Opt-in diagnostics; the existing body/capture/parse order stays pre-apply. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { performance } from 'node:perf_hooks'
import { joinInputTransition } from './input-trace-join.mjs'
const sha = bytes => createHash('sha256').update(bytes).digest('hex')

export function journalInputAttempt(entry, journal, { trace = false } = {}) {
  const value = { editor: entry.editor, phase: entry.phase, status: entry.status,
    response_status: entry.response_status, request_artifact: entry.request_artifact, response_artifact: entry.response_artifact }
  if (trace && entry.input_trace) value.input_trace = { response_id: entry.input_trace.response_id,
    node_phases: entry.input_trace.node_phases.map(row => ({ ...row })) }
  journal('input_attempt', value)
}

export async function consumeInputResponse(received, entry, captureBody, trace) {
  const mark = phase => {
    if (!trace) return
    const value = { phase, editor: entry.editor, sequence: entry.sequence,
      response_id: entry.input_trace.response_id, at_ms: performance.now(), clock_domain: 'node.performance.now:' + process.pid }
    trace.events.push(value); entry.input_trace.node_phases.push(value)
  }
  if (trace) {
    let id=null
    try { id=received.headers()['x-auto-plan-test-input'] ?? null } catch { /* Unavailable metadata earns no correlation claim. */ }
    entry.input_trace = { response_id: id, node_phases: [] }
    trace.attempts.push(entry)
  }
  mark('body_await_started')
  const bytes = await received.body()
  mark('body_await_complete')
  entry.response_status = received.status()
  mark('capture_started')
  entry.response_artifact = captureBody(bytes)
  mark('capture_complete')
  mark('json_parse_started')
  entry.response = JSON.parse(bytes)
  mark('json_parse_complete')
  return entry
}

/** Pure replay of immutable retained artifacts. Missing attribution never earns a claim. */
export function collectInputJoins({ attempts, browserBytes, browserSha256, serverBytes, origin, readArtifact }) {
  assert.equal(sha(browserBytes), browserSha256, 'Browser bytes differ from their separately retained export hash.')
  const pages = JSON.parse(browserBytes)
  const text = serverBytes.toString('utf8'), lines = text.split('\n')
  lines.pop() // only newline-terminated server records establish completion
  const server = lines.filter(Boolean).map(JSON.parse), joins = [], latest = new Map()
  const record = entry => {
    const id = entry.input_trace?.response_id
    assert.ok(id, 'No actual opaque response identifier was captured.')
    const request = readArtifact(entry.request_artifact), response = readArtifact(entry.response_artifact)
    assert.equal(sha(request), entry.request_artifact.sha256)
    assert.equal(sha(response), entry.response_artifact.sha256)
    assert.deepEqual(JSON.parse(request), entry.command)
    assert.deepEqual(JSON.parse(response), entry.response)
    return { id, path: new URL('/planning/api/inputs/' + entry.phase, origin).pathname,
      status: entry.response_status, request_bytes: request, command: entry.command,
      response: entry.response, response_sha256: entry.response_artifact.sha256, response_length: response.length }
  }
  for (const entry of attempts) {
    if (entry.phase === 'preview') {
      latest.delete(entry.editor)
      if (entry.response_status === 200 && entry.response?.valid) latest.set(entry.editor, entry)
      continue
    }
    if (entry.phase !== 'apply') continue
    const accepted = latest.get(entry.editor)
    latest.delete(entry.editor)
    try {
      assert.ok(accepted, 'No retained accepted preview precedes this apply.')
      const preview = record(accepted), apply = record(entry)
      const indices = pages.flatMap((events, index) => Array.isArray(events) && events.some(row => row.request_id === preview.id) ? [index] : [])
      assert.equal(indices.length, 1, 'No unique immutable document batch contains the accepted preview.')
      const index = indices[0], responses = Object.fromEntries([accepted, entry].map(row => [row.input_trace.response_id, readArtifact(row.response_artifact)]))
      const joined = joinInputTransition({ events: pages[index], preview, apply, server,
        documentEvidence: { bytes: browserBytes, sha256: browserSha256, page_index: index }, pageOrigin: origin,
        responseEvidence: responses, requireSameDocument: true, requireResponseBody: true })
      joins.push({ editor: entry.editor, sequence: entry.sequence, ...joined })
    } catch (error) {
      joins.push({ editor: entry.editor, sequence: entry.sequence, status: 'unknown', reason: String(error.message).split('\n')[0] })
    }
  }
  const incomplete = attempts.filter(row => !row.response_artifact || !Number.isInteger(row.response_status)).length
  return { status: joins.length && joins.every(row => row.status === 'pass') && !incomplete && !latest.size ? 'complete' : 'partial', joins,
    join_scope: 'Accepted-preview/apply pairs only; unsuccessful previews and reload GETs remain raw captured evidence.',
    attempts_total: attempts.length, preview_conflicts_not_joined: attempts.filter(row => row.phase === 'preview' && row.response_status === 409).length,
    incomplete_attempts: incomplete, unmatched_accepted_previews: latest.size,
    browser_export_sha256: sha(browserBytes), server_ledger_sha256: sha(serverBytes),
    limits: { server_response_bytes: 'unknown', raw_reload_get_body: 'not captured',
      api_request_context_evaluation: 'Node-only; no browser fetch/JSON claim' } }
}

/** Derive the existing cooperative cutoff from the already published phase. */
export function inputTraceCleanupDeadline(phase, limits) {
  assert.ok(Number.isFinite(phase.started_epoch_ms))
  const workEnd = phase.settled_epoch_ms ?? (phase.ready_epoch_ms === undefined
    ? phase.started_epoch_ms + limits.setup_ms : phase.ready_epoch_ms + limits.action_ms)
  assert.ok(Number.isFinite(workEnd))
  return Math.min(workEnd + limits.cleanup_ms * 2 / 3,
    phase.started_epoch_ms + limits.absolute_ms - limits.cleanup_ms / 3)
}

/** Best effort consumes the remaining published cleanup budget; timers cannot own lifetime. */
export async function exportInputBuffers(contexts, { deadlineEpochMs, sliceMs = 500,
  nowEpoch = () => performance.timeOrigin + performance.now() }) {
  const remaining = Math.min(sliceMs, deadlineEpochMs - nowEpoch())
  if (!Number.isFinite(remaining) || remaining <= 0) return null
  let timer
  const pending = Promise.all(contexts.map(async context => {
    const page = context.pages()[0]
    if (!page) return null
    try { return await page.evaluate(() => window.__inputConsumptionTrace?.events ?? null) }
    catch { return null }
  }))
  try {
    return await Promise.race([pending, new Promise(resolve => {
      timer = setTimeout(() => resolve(null), remaining)
      timer.unref()
    })])
  } finally { clearTimeout(timer) }
}
