import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { replayInputTrace } from './input-trace-replay.mjs'
import { consumeInputResponse, collectInputJoins, exportInputBuffers, inputTraceCleanupDeadline } from './input-trace-collector.mjs'
const sha = bytes => createHash('sha256').update(bytes).digest('hex')

test('default consumption retains original body/capture/parse calls and error identity', async () => {
  const calls = [], bytes = Buffer.from('{"input_version":2}'), entry = {}
  const response = { body: async () => { calls.push('body'); return bytes }, status: () => 200,
    headers: () => { throw new Error('disabled instrumentation read headers') } }
  await consumeInputResponse(response, entry, value => { calls.push('capture'); assert.equal(value, bytes); return { sha256: sha(value) } })
  assert.deepEqual(calls, ['body','capture']); assert.deepEqual(entry.response, { input_version: 2 }); assert.equal(entry.input_trace, undefined)
  const error = new Error('body failed')
  await assert.rejects(consumeInputResponse({ body: () => Promise.reject(error) }, {}, () => assert.fail()), value => value === error)
  const broken = Buffer.from('not json'), captured = []
  await assert.rejects(consumeInputResponse({ body: async () => broken, status: () => 409 }, {}, value => captured.push(value)), SyntaxError)
  assert.deepEqual(captured, [broken])
})

test('opt-in consumption records scalar phase markers without rereading or mutating parsed proof', async () => {
  let bodies = 0, captures = 0, headers = 0
  const trace = { events: [], attempts: [] }, entry = { editor: 2, sequence: 3, phase: 'preview' }
  await consumeInputResponse({ body: async () => { bodies++; return Buffer.from('{"valid":true,"base_version":1}') }, status: () => 200,
    headers: () => { headers++; return { 'x-auto-plan-test-input': 'actual-id' } } }, entry, () => { captures++; return { sha256: 'saved' } }, trace)
  assert.equal(bodies, 1); assert.equal(captures, 1); assert.equal(headers, 1)
  assert.equal(entry.input_trace.response_id, 'actual-id'); assert.equal(trace.attempts[0], entry)
  assert.deepEqual(trace.events.map(row => row.phase), ['body_await_started','body_await_complete','capture_started','capture_complete','json_parse_started','json_parse_complete'])
  assert.ok(trace.events.every(row => Object.values(row).every(value => value === null || ['string','number'].includes(typeof value))))
  assert.ok(trace.events.every((row, i, rows) => !i || row.at_ms >= rows[i-1].at_ms))
  const unavailable={editor:1,sequence:4,phase:'preview'}
  await consumeInputResponse({headers:()=>{throw new Error('closed metadata')},body:async()=>Buffer.from('{}'),status:()=>200},unavailable,()=>({}),trace)
  assert.equal(unavailable.input_trace.response_id,null);assert.deepEqual(unavailable.response,{})
})

function pair() {
  const command = { base_version: 1, changes: { lines: [{ code: 'L', name: 'Changed', active: true }] } }
  const responses = [{ ...command, valid: true }, { detail: 'conflict' }], files = new Map(), attempts = [], events = [], server = []
  const clock_domain = 'browser.performance.now:fixed-document'
  for (let i = 0; i < 2; i++) {
    const id = ['preview-id','apply-id'][i], phase = i ? 'apply' : 'preview', offset = i * 10
    const request = Buffer.from(JSON.stringify(command)), response = Buffer.from(JSON.stringify(responses[i]))
    files.set(id+'-request', request); files.set(id+'-response', response)
    attempts.push({ editor: 1, sequence: i+1, phase, input_trace: { response_id: id }, command, response: responses[i], response_status: i ? 409 : 200,
      request_artifact: { path: id+'-request', sha256: sha(request) }, response_artifact: { path: id+'-response', sha256: sha(response) } })
    server.push({ id, path: '/planning/api/inputs/'+phase, method: 'POST', status: i ? 409 : 200, request_complete: true, response_complete: true,
      request_sha256: sha(request), request_length: request.length, clock_domain: 'server.monotonic' })
    events.push({ phase: 'native_click', native_sequence: i+1, action: phase, disabled: false, review_request_id: i ? 'preview-id' : null, at_ms: offset, clock_domain },
      { phase: 'fetch_started', client_sequence: i+1, native_sequence: i+1, method: 'POST', path: '/planning/api/inputs/'+phase, at_ms: offset+1, clock_domain },
      ...['fetch_received','json_started','json_completed'].map((phase, j) => ({ phase, client_sequence: i+1, request_id: id, at_ms: offset+2+j, clock_domain,
        ...(j === 0 ? { status: i ? 409 : 200 } : {}), ...(i ? {} : { base_version: 1 }) })))
  }
  events.push({ phase: 'preview_dom_ready', request_id: 'preview-id', base_version: 1, apply_enabled: true, at_ms: 5, clock_domain })
  const browserBytes=Buffer.from(JSON.stringify([events]))
  return { attempts, browserBytes, browserSha256:sha(browserBytes), serverBytes: Buffer.from(server.map(JSON.stringify).join('\n')+'\n'),
    origin: 'https://declared.test', readArtifact: artifact => files.get(artifact.path), files }
}

test('strict collection binds body/document/request IDs and leaves unavailable server/resource claims unknown', () => {
  const value = pair(), result = collectInputJoins(value)
  assert.equal(result.status, 'complete'); assert.equal(result.joins.length, 1)
  assert.equal(result.joins[0].document.status, 'confirmed')
  assert.ok(result.joins[0].responses.every(row => row.retained_body.status === 'confirmed' && row.server_body.status === 'unknown'))
  assert.ok(result.joins[0].resources.every(row => row.status === 'unknown'))
  for (const mutate of [
    value => { delete value.attempts[1].input_trace.response_id },
    value => { value.attempts[1].response_status = 200 },
    value => { value.attempts[1].command = { base_version: 2, changes: {} } },
    value => { value.files.set('apply-id-response', Buffer.from('{}')) },
    value => { value.serverBytes = value.serverBytes.subarray(0, value.serverBytes.length-1) },
    value => { value.serverBytes = Buffer.concat([value.serverBytes, value.serverBytes]) },
    value => { const pages = JSON.parse(value.browserBytes); pages[0].find(row => row.phase === 'json_completed').at_ms = -1; value.browserBytes = Buffer.from(JSON.stringify(pages)); value.browserSha256=sha(value.browserBytes) },
  ]) { const changed = pair(); mutate(changed); assert.equal(collectInputJoins(changed).status, 'partial') }
  const forged=pair();forged.browserSha256='wrong';assert.throws(()=>collectInputJoins(forged))
  const pending = pair(); pending.attempts.pop(); assert.equal(collectInputJoins(pending).status, 'partial')
})

test('best-effort export consumes remaining cleanup without starting work after expiry', async () => {
  const options={deadlineEpochMs:1000,nowEpoch:()=>0}
  assert.deepEqual(await exportInputBuffers([{ pages: () => [{ evaluate: async () => [{ phase: 'actual' }] }] }],options), [[{ phase: 'actual' }]])
  assert.deepEqual(await exportInputBuffers([{ pages: () => [{ evaluate: async () => { throw new Error('closed') } }] }],options), [null])
  let calls=0
  const stuck=[{pages:()=>{calls++;return [{evaluate:()=>new Promise(()=>{})}]}}]
  assert.equal(await exportInputBuffers(stuck,{deadlineEpochMs:10,nowEpoch:()=>10}),null);assert.equal(calls,0)
  const lifetime=setTimeout(()=>{},50) // the export's unref'ed timer must not own worker lifetime
  try{assert.equal(await exportInputBuffers(stuck,{deadlineEpochMs:5,nowEpoch:()=>0,sliceMs:500}),null)}finally{clearTimeout(lifetime)}
  assert.equal(calls,1)
  const limits={setup_ms:180000,action_ms:150000,cleanup_ms:15000,absolute_ms:345000}
  assert.equal(inputTraceCleanupDeadline({started_epoch_ms:0},limits),190000)
  assert.equal(inputTraceCleanupDeadline({started_epoch_ms:0,ready_epoch_ms:150000},limits),310000)
  assert.equal(inputTraceCleanupDeadline({started_epoch_ms:0,ready_epoch_ms:170000,settled_epoch_ms:329999},limits),339999)
  assert.equal(inputTraceCleanupDeadline({started_epoch_ms:0,ready_epoch_ms:180000,settled_epoch_ms:330001},limits),340000)
})

test('offline replay verifies separately retained index/config/bodies and preserves interruption uncertainty', () => {
  const value=pair(), files=new Map(value.files), fixtures='/fixture'
  for(const [path,bytes] of value.files)files.set(fixtures+'/'+path,bytes)
  const retain=(path,value)=>{const bytes=Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value));files.set(path,bytes);return {path,sha256:sha(bytes)}}
  const index=value.attempts.map(({command,response,...entry})=>entry)
  const report={fixtures,input_trace:{config:retain('/config',{origin:value.origin}),browser_export:retain('/browser',value.browserBytes),
    attempts:retain('/index',index),server_ledger:retain('/server',value.serverBytes),source_closure:'unconfirmed',input_acknowledgments:'unconfirmed',restoration:'unconfirmed'}}
  const read=path=>files.get(path)
  const result=replayInputTrace(report,read)
  assert.equal(result.status,'complete');assert.equal(result.source_closure,'unconfirmed');assert.equal(result.input_acknowledgments,'unconfirmed');assert.equal(result.restoration,'unconfirmed')
  assert.equal(result.acceptance,'not_run');assert.deepEqual(result.repeats,[])
  for(const path of ['/config','/browser','/index','/server',fixtures+'/apply-id-response']){
    const original=files.get(path);files.set(path,Buffer.from('{}'));assert.throws(()=>replayInputTrace(report,read));files.set(path,original)
  }
})
