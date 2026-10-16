/** Post-teardown request diagnostics. This never repairs a workflow report. */
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, realpathSync, lstatSync, existsSync } from 'node:fs'
import { resolve, isAbsolute, sep, dirname } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'
import { isDeepStrictEqual } from 'node:util'
import { joinInputTransition } from './input-trace-join.mjs'

const sha = bytes => createHash('sha256').update(bytes).digest('hex')
const same = (left, right, message) => assert.ok(isDeepStrictEqual(left, right), message)
const base = '/tmp/auto-plan-spec-23'
const RESULT = { path: base + '/initial-import155-result.json', sha256: 'f72e0542f93ac4993fd2ea055f7c9a697db7750666e80158a0d540f17a4b3867' }
const SEAL = { path: base + '/initial-import155-raw-sha256.json', sha256: 'e958f0b3a539ebb7cf69d950fbc111f36f6e71cbdce9fb86005ffd2f45365010' }
const RAW_ROOT = '/tmp/auto-plan-initial-import-browser-Cg0Nxk'
const phases = ['body_await_started', 'body_await_complete', 'capture_started', 'capture_complete', 'json_parse_started', 'json_parse_complete']
const json = bytes => JSON.parse(bytes)
const lines = bytes => {
  const values = bytes.toString('utf8').split('\n'), tail = values.pop()
  return { rows: values.filter(Boolean).map(JSON.parse), incomplete_tail: Boolean(tail) }
}
function checkedFile(path, expected) {
  assert.equal(realpathSync(path), resolve(path), 'Artifact path contains a symlink.')
  const stat = lstatSync(path)
  assert.ok(stat.isFile() && !stat.isSymbolicLink(), 'Artifact is not a regular file.')
  if (expected.length !== undefined) assert.equal(stat.size, expected.length, 'Artifact length differs from its seal.')
  const bytes = readFileSync(path)
  assert.equal(sha(bytes), expected.sha256, 'Artifact bytes differ from their trusted seal.')
  assert.equal(realpathSync(path), resolve(path), 'Artifact path changed while reading.')
  return bytes
}

/** Filesystem boundary: the caller supplies an already trusted immutable manifest. */
export function readSealedArtifact(root, files, name) {
  assert.ok(typeof name === 'string' && !isAbsolute(name) && !name.split('/').some(part => ['', '.', '..'].includes(part)), 'Artifact name escapes its root.')
  assert.equal(realpathSync(root), resolve(root), 'Raw root contains a symlink.')
  const path = resolve(root, name)
  assert.ok(path.startsWith(resolve(root) + sep), 'Artifact escapes its root.')
  assert.ok(Object.hasOwn(files, name), 'Artifact is absent from the trusted master seal.')
  const expected = files[name]
  assert.ok(/^[a-f0-9]{64}$/.test(expected.sha256) && Number.isSafeInteger(expected.length) && expected.length >= 0, 'Invalid master seal entry.')
  return checkedFile(path, expected)
}

/** Pure request-local proof. The binding loader establishes this bundle's authority. */
export function collectPostTeardownPairs({ attempts, journal, nodeEvents, owner, browserBytes, browserSha256, serverBytes, origin, readArtifact, select }) {
  assert.equal(sha(browserBytes), browserSha256, 'Browser export differs from its external seal.')
  const pages = json(browserBytes), parsedServer = lines(serverBytes), server = parsedServer.rows
  assert.ok(Array.isArray(pages) && pages.length === 5 && pages.every(Array.isArray), 'Expected five original exported page batches.')
  assert.ok(Array.isArray(attempts) && Array.isArray(journal) && Array.isArray(nodeEvents), 'Captured indexes must be arrays.')
  assert.ok(Number.isSafeInteger(owner.worker_pid) && /^\d+$/.test(owner.worker_start) && /^[a-f0-9-]{36}$/.test(owner.attempt), 'Owned worker identity is incomplete.')
  assert.ok(Number.isSafeInteger(owner.server_pid) && /^\d+$/.test(owner.server_start), 'Owned server identity is incomplete.')
  assert.equal(new URL(origin).origin, origin, 'Bind the exact origin.')
  const counts = new Map(), latest = new Map(), pairs = [], unjoined = []
  for (const entry of attempts) {
    const id = entry.input_trace?.response_id
    if (id) counts.set(id, (counts.get(id) ?? 0) + 1)
  }
  if (select) assert.ok(Array.isArray(select) && select.length === 2 && new Set(select).size === 2 && select.every(id => counts.get(id) === 1), 'Selection needs two unique actually captured identifiers.')
  const record = entry => {
    const id = entry.input_trace?.response_id
    assert.ok(typeof id === 'string' && /^[a-f0-9]{32}$/.test(id) && counts.get(id) === 1, 'Actual opaque response identifier is missing or ambiguous.')
    assert.ok(Number.isInteger(entry.editor) && entry.editor >= 1 && entry.editor <= 4 && Number.isInteger(entry.sequence) && entry.sequence > 0, 'Invalid editor/sequence.')
    assert.ok(['preview', 'apply'].includes(entry.phase) && Number.isInteger(entry.response_status), 'Incomplete operation/status.')
    const markers = entry.input_trace.node_phases
    same(markers?.map(row => row.phase), phases, 'Six original Node phases are required.')
    let last = -1
    for (const row of markers) {
      assert.equal(row.clock_domain, 'node.performance.now:' + owner.worker_pid, 'Node phase owner differs.')
      assert.equal(row.response_id, id); assert.equal(row.editor, entry.editor); assert.equal(row.sequence, entry.sequence)
      assert.ok(Number.isFinite(row.at_ms) && row.at_ms >= last && row.at_ms >= 0, 'Node phases are invalid or out of order.')
      last = row.at_ms
    }
    same(nodeEvents.filter(row => row.response_id === id), markers, 'Node export differs from the captured index.')
    const durable = journal.filter(row => row.phase === entry.phase && row.input_trace?.response_id === id)
    assert.equal(durable.length, 1, 'No unique durable original callback backs this index entry.')
    for (const key of ['editor', 'response_status', 'request_artifact', 'response_artifact', 'input_trace']) same(durable[0][key], entry[key], 'Durable callback/index metadata differs: ' + key)
    const bytes = pointer => {
      assert.ok(pointer && pointer.path === 'bodies/' + pointer.sha256 + '.json' && /^[a-f0-9]{64}$/.test(pointer.sha256), 'Body pointer is not content addressed.')
      const value = readArtifact(pointer)
      assert.equal(sha(value), pointer.sha256, 'Retained body differs from captured pointer.')
      return value
    }
    const request = bytes(entry.request_artifact), response = bytes(entry.response_artifact)
    const matches = server.filter(row => row.id === id)
    assert.equal(matches.length, 1, 'No unique original server identifier.')
    assert.equal(matches[0].clock_domain, 'python.perf_counter_ns:pid=' + owner.server_pid, 'Server clock owner differs.')
    return { id, path: '/planning/api/inputs/' + entry.phase, status: entry.response_status, request_bytes: request,
      command: json(request), response: json(response), response_sha256: entry.response_artifact.sha256, response_length: response.length, response_bytes: response }
  }
  for (const entry of attempts) {
    if (!['preview', 'apply'].includes(entry.phase)) { unjoined.push({ editor: entry.editor, sequence: entry.sequence, reason: 'Operation outside accepted-preview/apply scope.' }); continue }
    if (entry.phase === 'preview') {
      if (latest.has(entry.editor)) unjoined.push({ editor: entry.editor, sequence: latest.get(entry.editor).sequence, reason: 'Accepted preview has no following apply.' })
      latest.delete(entry.editor)
      try {
        const value = record(entry)
        if (value.status === 200 && value.response.valid === true) latest.set(entry.editor, entry)
        else unjoined.push({ editor: entry.editor, sequence: entry.sequence, response_id: value.id, reason: 'Unsuccessful preview is outside accepted-pair scope.' })
      } catch (error) { unjoined.push({ editor: entry.editor, sequence: entry.sequence, response_id: entry.input_trace?.response_id ?? null, reason: error.message.split('\n')[0] }) }
      continue
    }
    const accepted = latest.get(entry.editor); latest.delete(entry.editor)
    if (select && entry.input_trace?.response_id !== select[1]) continue
    try {
      assert.ok(accepted, 'No preceding qualified accepted preview.')
      const preview = record(accepted), apply = record(entry)
      if (select) same([preview.id, apply.id], select, 'Selected apply follows another preview.')
      const indexes = pages.flatMap((events, index) => events.some(row => row.request_id === preview.id || row.request_id === apply.id) ? [index] : [])
      assert.equal(indexes.length, 1, 'No unique complete exported document batch contains both identifiers.')
      const page_index = indexes[0]
      assert.equal(page_index, entry.editor, 'Exported context/editor order differs.')
      assert.ok(pages[page_index].every(row => row.clock_domain === 'browser.performance.now:first-import-editor-' + entry.editor), 'Document batch clock differs from its captured context.')
      const joined = joinInputTransition({ events: pages[page_index], preview, apply, server,
        documentEvidence: { bytes: browserBytes, sha256: browserSha256, page_index }, pageOrigin: origin,
        responseEvidence: { [preview.id]: preview.response_bytes, [apply.id]: apply.response_bytes },
        requireSameDocument: true, requireResource: true, requireResponseBody: true })
      pairs.push({ editor: entry.editor, sequence: entry.sequence, ...joined })
    } catch (error) { pairs.push({ editor: entry.editor, sequence: entry.sequence, preview_id: accepted?.input_trace?.response_id ?? null, apply_id: entry.input_trace?.response_id ?? null, status: 'unknown', reason: error.message.split('\n')[0] }) }
  }
  for (const entry of latest.values()) unjoined.push({ editor: entry.editor, sequence: entry.sequence, response_id: entry.input_trace.response_id, reason: 'Accepted preview has no following apply.' })
  return { pairs, unjoined: unjoined.map(row => ({ ...row, browser_attribution: 'unknown' })), attempts_total: attempts.length, incomplete_server_tail: parsedServer.incomplete_tail,
    selection: select ? { ids: select, scope: 'Only this explicitly selected pair; other attempts not qualified by this result.' } : null }
}

export function replayPostTeardownInputTrace(binding) {
  assert.equal(binding.kind, 'post-teardown-input-browser-diagnostic-v1', 'Explicit post-teardown diagnostic kind is required.')
  same(Object.keys(binding).sort(), ['capture_result', 'kind', 'raw_seal', ...(binding.select ? ['select'] : [])].sort(), 'Unexpected diagnostic binding fields.')
  same(binding.capture_result, RESULT, 'Root-trusted155 result anchor differs.')
  same(binding.raw_seal, SEAL, 'Root-trusted155 master seal anchor differs.')
  const capture = json(checkedFile(RESULT.path, RESULT)), seal = json(checkedFile(SEAL.path, SEAL))
  assert.equal(capture.status, 'FAILED_WORKLOAD_STOP_CLEAN'); assert.equal(capture.raw_output, RAW_ROOT); assert.equal(capture.raw_output, seal.root)
  assert.equal(capture.evidence_sha256[SEAL.path], SEAL.sha256)
  const read = name => readSealedArtifact(seal.root, seal.files, name)
  const evidence = name => {
    const path = base + '/' + name, digest = capture.evidence_sha256[path]
    assert.ok(digest, 'Required independent provenance evidence is absent.')
    return json(checkedFile(path, { sha256: digest }))
  }
  const preparation = evidence('initial-import155-preparation.json'), post = evidence('initial-import155-source-post.json'), cleanup = evidence('initial-import155-cleanup.json'), started = evidence('initial-import155-owned-start.json')
  const report = json(read('report.json')), config = json(read('input-trace-config.json')), owner = json(read('owned-processes.json')), phase = json(read('functional-phase.json'))
  assert.equal(report.input_trace.status, 'pending'); same(Object.keys(report.input_trace).sort(), ['config', 'status'])
  assert.equal(report.input_trace.config.sha256, sha(read('input-trace-config.json')))
  assert.equal(report.input_trace.config.path, resolve(seal.root, 'input-trace-config.json'))
  for (const value of [config, preparation, post]) {
    assert.equal(value.source_revision ?? value.source_commit, capture.source_commit, 'Captured source commit differs.')
    assert.equal(value.source_tree, capture.source_tree, 'Captured source tree differs.')
  }
  same(report.source_files, config.source_files, 'Original report/config helper bindings differ.')
  for (const [path, digest] of Object.entries({ ...config.source_files, ...config.actual_product_files })) {
    const expected = path.startsWith('frontend/dist/') ? preparation.dist_sha256[path.slice(14)] : preparation.tracked_source_sha256[path]
    assert.equal(digest, expected, 'Captured helper/product source differs: ' + path)
  }
  same(Object.fromEntries(Object.entries(config.actual_dist_files).map(([key, value]) => [key.slice(14), value])), preparation.dist_sha256, 'Retained distribution differs.')
  for (const key of ['all170_source_helper_lock_files_unchanged', 'all31_frontend_and_retained_dist_unchanged', 'config_actual_product_helpers_dist_exact', 'raw410_hashes_unchanged']) assert.equal(post[key], true, 'Independent post-source proof is incomplete.')
  assert.equal(sha(readFileSync(new URL('./input-trace-join.mjs', import.meta.url))), config.source_files['frontend/tests/acceptance/input-trace-join.mjs'], 'The original strict join implementation changed.')
  same([phase.pid, phase.start, phase.attempt], [owner.worker_pid, owner.worker_start, owner.attempt], 'Owned phase identity differs.')
  same([cleanup.controller_cleanup.worker_pid, cleanup.controller_cleanup.worker_start, cleanup.controller_cleanup.attempt], [owner.worker_pid, owner.worker_start, owner.attempt], 'Independent controller identity differs.')
  assert.equal(cleanup.all_owned_resources_reaped, true); same(cleanup.controller_cleanup.remaining_live, []); same(cleanup.remaining_owned_group_live, [])
  assert.equal(started.source_commit, capture.source_commit)
  assert.ok(cleanup.owned_identity_checks.some(row => row.pid === started.outer_node_pid && row.expected_start === started.outer_node_start && row.same_owned_identity_live === false), 'Independent outer process closure is absent.')
  for (const [pid, start] of [[owner.worker_pid, owner.worker_start], [owner.server_pid, owner.server_start], [owner.browser_pid, owner.browser_start]]) assert.ok(cleanup.all_recorded_owned_identity_checks.some(row => row.pid === pid && row.expected_start === start && row.same_owned_identity_live === false), 'Independent owned identity closure is absent.')
  for (const [pid, start] of [[owner.server_pid, owner.server_start], [owner.browser_pid, owner.browser_start]]) {
    const registration = owner.registrations.filter(row => row.pid === pid && row.start === start)
    assert.equal(registration.length, 1, 'Owned child registration is ambiguous.')
    same([registration[0].parent_pid, registration[0].parent_start], [owner.worker_pid, owner.worker_start], 'Actual owned parent witness differs.')
  }
  assert.ok(cleanup.port18869.length === 2 && cleanup.port18869.every(row => row.port === 18869 && row.errno === 111), 'Independent listener closure is absent.')
  same(cleanup.controller_cleanup.source_files_before, cleanup.controller_cleanup.source_files_after, 'Controller source changed during the run.')
  for (const [path, digest] of Object.entries(cleanup.controller_cleanup.source_files_before)) {
    assert.ok(path.startsWith(preparation.worktree + '/'), 'Controller source has another owner.')
    assert.equal(digest, config.source_files[path.slice(preparation.worktree.length + 1)], 'Controller source differs from captured helper.')
  }
  assert.equal(capture.source_closure, 'unconfirmed'); assert.equal(capture.input_acknowledgments, 'unconfirmed;47known clientacks do not establish final complete count'); assert.equal(capture.restoration, 'unconfirmed')
  const browserBytes = read('input-browser-events.json'), journal = lines(read('events.ndjson'))
  const result = collectPostTeardownPairs({ attempts: json(read('input-trace-attempts.json')), journal: journal.rows,
    nodeEvents: json(read('input-node-events.json')), owner, browserBytes, browserSha256: seal.files['input-browser-events.json'].sha256,
    serverBytes: read('input-transitions.ndjson'), origin: config.origin, select: binding.select,
    readArtifact: pointer => read('fixtures/' + pointer.path) })
  return { status: 'diagnostic_only', binding_origin: 'independent_post_teardown_seal', worker_report_export_attestation: 'absent',
    source_commit: capture.source_commit, source_tree: capture.source_tree, capture_result_sha256: RESULT.sha256, raw_seal_sha256: SEAL.sha256,
    ...result, incomplete_journal_tail: journal.incomplete_tail, source_closure: 'unconfirmed', input_acknowledgments: 'unconfirmed', restoration: 'unconfirmed',
    original_workflow_status: capture.status, original_workflow_result: capture.functional_result,
    original_input_acknowledgments: capture.input_acknowledgments,
    diagnostic_helpers_sha256: { 'input-postteardown-replay.mjs': sha(readFileSync(new URL(import.meta.url))), 'input-trace-join.mjs': config.source_files['frontend/tests/acceptance/input-trace-join.mjs'] },
    limits: { browser_document_url_timeOrigin_UUID: 'not captured', browser_export_tail_completeness: 'unknown', server_response_bytes: 'unknown', native_import_clock: 'not started; no qualified readiness',
      clock_domains: 'Browser, Node and server remain separate; no alignment or cross-domain subtraction.', unsuccessful_previews_and_reload_GETs: 'outside accepted-pair scope' }, acceptance: 'not_run', repeats: [] }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  assert.equal(process.argv.length, 4, 'Provide an explicit binding file and NEW result file.')
  const output = resolve(process.argv[3])
  assert.equal(realpathSync(dirname(output)), dirname(output), 'Derived output parent contains a symlink.')
  assert.ok(output !== RAW_ROOT && !output.startsWith(RAW_ROOT + sep) && !existsSync(output), 'Provide a NEW derived output outside the immutable capture.')
  const result = replayPostTeardownInputTrace(json(readFileSync(process.argv[2])))
  writeFileSync(output, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' })
  console.log(JSON.stringify({ status: result.status, pairs: result.pairs.length, confirmed_pairs: result.pairs.filter(row => row.status === 'pass').length, unknown_pairs: result.pairs.filter(row => row.status === 'unknown').length, acceptance: result.acceptance }))
}
