/** Public native upload and complete input/evaluation evidence. */
import assert from 'node:assert/strict'
import { attestUpload, readUploadObservation } from './upload-observations.mjs'
import { expect } from '@playwright/test'
import { readFileSync, writeFileSync, appendFileSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import { gunzipSync } from 'node:zlib'
import { spawnSync } from 'node:child_process'
import { inputEditorLoop } from './input-editors.mjs'
import { INPUT_CONTENT_DIGEST_ALGORITHM, inputContentDigest } from './input-load.mjs'

const sha = bytes => createHash('sha256').update(bytes).digest('hex')
const epoch = () => Date.now()
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
const twoFrames = page => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(() => done(performance.timeOrigin + performance.now())))))
const body = async response => { const bytes = await response.body(); return { bytes, value: JSON.parse(bytes) } }

export async function importBrowserContract({ pages, fixtures, output, origin, python, repo, report, uploadObservations }) {
  const prefix = origin+'/planning/api', headers = { Origin: origin, 'X-Auto-Plan-Request': '1' }
  const manifest = JSON.parse(readFileSync(resolve(fixtures, 'manifest.json'))), scenario = manifest.scenarios[0]
  const journal = (phase, value={}) => appendFileSync(resolve(output,'events.ndjson'),JSON.stringify({phase,epoch_ms:epoch(),...value})+'\n')
  const rawDeclaration = readFileSync(resolve(fixtures, scenario.initial_import_load.path))
  assert.equal(sha(rawDeclaration), scenario.initial_import_load.sha256)
  const declaration = JSON.parse(rawDeclaration)
  report.bindings={scenario:scenario.id,manifest_sha256:sha(readFileSync(resolve(fixtures,'manifest.json'))),declaration_sha256:sha(rawDeclaration),source_sha256:scenario.source.sha256,reference_sha256:scenario.reference.sha256,action_sha256:scenario.action.sha256,import_sha256:scenario.import.sha256}
  const source = JSON.parse(gunzipSync(readFileSync(resolve(fixtures, scenario.source.path))))
  const payloadBytes = readFileSync(resolve(fixtures, 'frozen-computation.json')), payload = JSON.parse(payloadBytes)
  assert.deepEqual(payload, { snapshot: source })
  const workbook = resolve(fixtures, declaration.workbook.path)
  assert.equal(sha(readFileSync(workbook)), declaration.workbook.sha256)
  mkdirSync(resolve(fixtures, 'bodies'))
  const capture = bytes => { const value = Buffer.from(bytes), digest = sha(value), path = 'bodies/'+digest+'.json'; writeFileSync(resolve(fixtures, path), value); return { path, sha256: digest } }
  const get = async path => {
    const response = await pages[0].request.get(prefix+path, { headers }); assert.equal(response.status(), 200)
    return body(response)
  }
  const versions = {}, attempts = [], history = [], evaluations = [], committed = new Set(), ready = deferred(), evaluationReady = deferred()
  report.versions=versions
  const immutable = async version => {
    const response = await get('/inputs?version='+version)
    versions[String(version)] = { status: 200, version, body: response.value, artifact: capture(response.bytes) }
    journal('immutable_input_observed',{version,artifact:versions[String(version)].artifact})
    return response.value
  }
  const state = async () => {
    const observed=await get('/plans/current'),head=observed.value,inputs=await immutable(head.input_version),demands=await get('/demands/files')
    const value={ head, snapshot: { inputs, plan: head.plan, context: head.context }, demand_files: demands.value,
      read_artifacts:{head:capture(observed.bytes),inputs:versions[String(head.input_version)].artifact,demand_files:capture(demands.bytes)} }
    journal('complete_live_observation',{input_version:head.input_version,...value.read_artifacts})
    return value
  }
  const before = await state()
  assert.equal(before.head.revision, 0); assert.equal(before.head.latest_adoption, 0); assert.equal(before.head.input_version, 1)
  assert.deepEqual(before.snapshot.inputs, declaration.live_inputs)
  report.before = before; report.attempts = attempts; report.history = history; report.evaluations = evaluations
  const helperDeclaration = { schema_version: 1, kind: 'maintained_input_names', digest_algorithm: INPUT_CONTENT_DIGEST_ALGORITHM,
    scenario: scenario.id, source_inputs_sha256: inputContentDigest(declaration.live_inputs), targets: declaration.targets,
    states: declaration.states.map(state => {
      const inputs = structuredClone(declaration.live_inputs), mask = Number(state.id)
      for (const [bit, target] of declaration.targets.entries()) inputs.masters[target.kind].find(row => row.code === target.code).name = target.names[(mask >> bit)&1]
      assert.equal(inputContentDigest(inputs), state.inputs_sha256)
      return { ...state, inputs }
    }) }
  const primary = pages[0]
  const loadPrimary = async reload => {
    const pending = primary.waitForResponse(response => response.url() === prefix+'/plans/current' && response.request().method() === 'GET')
    if (reload) await primary.reload(); else await primary.goto(origin+'/planning/initialize')
    const observed = await body(await pending)
    await expect(primary.getByRole('heading', { name: '首次接入与需求变更', exact: true })).toBeVisible()
    await twoFrames(primary)
    return { value: observed.value, artifact: capture(observed.bytes) }
  }
  const firstReviewed = await loadPrimary(false)
  assert.equal(firstReviewed.value.input_version, 1)
  report.first_reviewed = firstReviewed
  let stop = false, failure, stopEvaluation = false
  const inputWire=[],pendingCaptures=[],wireByRequest=new Map(),listeners=[]
  report.input_wire=inputWire
  for(const [editor,page] of pages.entries())if(editor>0){
    const requested=request=>{
      if(request.url()!==prefix+'/inputs/apply'||request.method()!=='POST')return
      const raw=request.postDataBuffer(),entry={editor,started_epoch_ms:epoch(),response_status:null,request_artifact:capture(raw)}
      inputWire.push(entry);wireByRequest.set(request,entry);journal('actual_input_apply_started',entry)
    }
    const received=response=>{
      const entry=wireByRequest.get(response.request());if(!entry)return
      entry.response_status=response.status()
      const pending=response.body().then(bytes=>{entry.response_artifact=capture(bytes);entry.finished_epoch_ms=epoch();journal('actual_input_apply_reply',entry)}).catch(error=>{entry.capture_failure=String(error.message).split('\n')[0]})
      pendingCaptures.push(pending)
    }
    const failed=request=>{const entry=wireByRequest.get(request);if(entry){entry.failure=request.failure();entry.finished_epoch_ms=epoch();journal('actual_input_apply_failed',entry)}}
    page.on('request',requested);page.on('response',received);page.on('requestfailed',failed)
    listeners.push(()=>{page.off('request',requested);page.off('response',received);page.off('requestfailed',failed)})
  }
  const editorLoops = declaration.targets.map(target => inputEditorLoop({ page: pages[target.editor], editor: target.editor,
    declaration: helperDeclaration, initialInputs: declaration.live_inputs, origin, basePath: '/planning', stopped: () => stop,
    captureBody: capture, firstPreview: async () => {}, firstCommit: async () => {},
    onAttempt: entry => { (report.editor_attempts ??= []).push(entry);journal('input_attempt',{editor:entry.editor,phase:entry.phase,status:entry.status,response_status:entry.response_status,request_artifact:entry.request_artifact,response_artifact:entry.response_artifact}) },
    onCommit: entry => {
      history.push(entry);committed.add(entry.editor);journal('input_commit',{editor:entry.editor,input_version:entry.after.input_version,request_artifact:entry.request_artifact,response_artifact:entry.response_artifact})
      if(entry.response_status!==200 || !entry.response_artifact){failure??=new Error('Input acknowledgment requires a separately qualified reconciliation contract.');stop=true;ready.resolve()}
      if(committed.size===4)ready.resolve()
    }
  }).catch(error => { failure ??= error; ready.resolve(); stop = true }))
  const evaluationLoop = (async () => {
    try {
      while (!stopEvaluation) {
        const entry = { started_epoch_ms: epoch(), status: null, payload, request_artifact: capture(payloadBytes) }
        evaluations.push(entry)
        journal('full_evaluation_started',{request_artifact:entry.request_artifact})
        const response = await pages[1].request.post(prefix+'/plans/evaluate', { headers: { ...headers, 'Content-Type': 'application/json' }, data: payloadBytes, timeout: 60000 })
        entry.received_epoch_ms = epoch(); entry.status = response.status()
        const full = await body(response); entry.response = full.value; entry.response_artifact = capture(full.bytes); entry.body_complete_epoch_ms = epoch()
        assert.equal(entry.status, 200); assert.equal(entry.response.capacity_valid, true); assert.equal(entry.response.computation_complete, true)
        assert.equal(entry.response.output_totals.expected, 420000); assert.equal(entry.response.weekly_results.length, 600)
        entry.validated_epoch_ms = epoch(); evaluationReady.resolve()
        journal('full_evaluation_body_complete',{status:entry.status,response_artifact:entry.response_artifact})
      }
    } catch (error) { failure ??= error; evaluationReady.resolve(); stop = true }
  })()
  let prepared, preparedArtifact, query, after, measured = false, mainFailure
  try {
    await Promise.all([ready.promise, evaluationReady.promise])
    if (failure) throw failure
    assert.equal(committed.size, 4)
    // Primary deliberately retains its honestly reviewed I1. Editors continue.
    report.clock = {}
    for (let number=1; number<=4; number++) {
      if (number>1) report.last_reload = await loadPrimary(true)
      const session = await primary.context().newCDPSession(primary)
      const captureOptions = { maxTotalBufferSize: 128*1024*1024, maxResourceBufferSize: 64*1024*1024, enableDurableMessages: true }
      await session.send('Network.enable', captureOptions)
      const pending = primary.waitForResponse(response => response.url().startsWith(prefix+'/initialization/excel/preview?') && response.request().method() === 'POST', { timeout: 60000 })
      pending.catch(()=>{})
      const attempt = { number, started_epoch_ms: epoch(), status: null, capture: captureOptions }; attempts.push(attempt)
      await primary.getByLabel('上传标准工作簿并预览', { exact: true }).setInputFiles(workbook)
      attempt.clicked_epoch_ms = await primary.evaluate(() => window.__initialImportClickEpoch)
      assert.ok(Number.isFinite(attempt.clicked_epoch_ms)); report.clock.origin_epoch_ms ??= attempt.clicked_epoch_ms
      journal('native_workbook_selected',{number,clicked_epoch_ms:attempt.clicked_epoch_ms,origin_epoch_ms:report.clock.origin_epoch_ms})
      const response = await pending, received = await body(response)
      attempt.status = response.status(); attempt.query = Object.fromEntries([...new URL(response.url()).searchParams].map(([key,value]) => [key,Number(value)]))
      attempt.response_artifact = capture(received.bytes); attempt.response_complete_epoch_ms = epoch()
      journal('workbook_reply',{number,status:attempt.status,query:attempt.query,response_artifact:attempt.response_artifact})
      const requestBytes=response.request().postDataBuffer()
      attempt.playwright_request_bytes_available=Buffer.isBuffer(requestBytes)
      if(requestBytes){attempt.playwright_request_sha256=sha(requestBytes);assert.equal(attempt.playwright_request_sha256,declaration.workbook.sha256)}
      attempt.request_body_capture={provider:'test-only server-observed received bytes',confirmation:'unconfirmed',status:response.status()}
      const id=await response.headerValue('x-auto-plan-test-upload')
      assert.ok(id && uploadObservations,'No actual response-bound server receipt; native transport remains unconfirmed.')
      const receivedObservation=await readUploadObservation(uploadObservations,id)
      const observation=receivedObservation.observation
      attempt.request_body_capture={...attestUpload(observation,{id,url:response.url(),status:response.status(),length:readFileSync(workbook).length,sha256:declaration.workbook.sha256}),confirmation:'confirmed'}
      attempt.request_body_capture.artifact=capture(Buffer.from(receivedObservation.raw))
      await session.detach()
      if (number===1) { assert.equal(attempt.query.input_version, 1); assert.equal(attempt.status, 409) }
      if (attempt.status===409) continue
      assert.equal(attempt.status, 200); assert.equal(received.value.valid, true)
      prepared = received.value; preparedArtifact = attempt.response_artifact; query = attempt.query
      assert.equal(prepared.input_version, query.input_version); assert.equal(prepared.inputs.input_version, query.input_version)
      await expect(primary.getByText('预览通过，尚未写入。', { exact: true })).toBeVisible()
      await expect(primary.getByText(/导入生产段\s*4200\s*段。服务器已生成精确工时证书。规则核算可执行。/)).toBeVisible()
      report.clock.rendered_epoch_ms = await twoFrames(primary)
      report.clock.seconds = (report.clock.rendered_epoch_ms-report.clock.origin_epoch_ms)/1000
      measured = true; break
    }
    assert.ok(measured, 'Four real stale/reload/reselect attempts did not complete; preserve the failure without pausing writers.')
  } catch(error) { mainFailure=error }
  finally {
    // Normal closure happens only after successful complete paint or a failure.
    stop = true; stopEvaluation = true
    await Promise.allSettled([...editorLoops, evaluationLoop])
    await Promise.allSettled(pendingCaptures)
    for(const remove of listeners)remove()
    try{
      after = await state(); report.after = after
      for (let version=1; version<=after.head.input_version; version++) await immutable(version)
    }catch(error){mainFailure??=error;report.final_observation_failure=String(error.message).split('\n')[0]}
  }
  try {
  if(mainFailure)throw mainFailure
  if (failure) throw failure
  history.sort((left,right) => left.after.input_version-right.after.input_version)
  const transitions = history.map(entry => {
    assert.equal(entry.response_status, 200, 'Unknown input acknowledgment needs its separately qualified reconciliation contract; never invent200.')
    return { editor: entry.editor, command: entry.command, response_status: entry.response_status, response: entry.after,
      request_artifact: entry.request_artifact, response_artifact: entry.response_artifact }
  })
  const proof = { prepared, prepared_artifact: preparedArtifact, before, after, query, versions, history: transitions,
    workbook_sha256: declaration.workbook.sha256, evaluations }
  writeFileSync(resolve(fixtures,'public-proof.json'), JSON.stringify(proof))
  writeFileSync(resolve(fixtures,'evaluation.json'), JSON.stringify(prepared.evaluation))
  const compared = spawnSync(python, [resolve(repo,'scripts/acceptance/check_result.py'), '--fixtures', fixtures, '--scenario', scenario.id,
    '--kind','workbook','--evaluation',resolve(fixtures,'evaluation.json'),'--proof',resolve(fixtures,'public-proof.json')], { cwd: repo, encoding:'utf8', timeout:60000, env:{...process.env,PYTHONPATH:'src:.'} })
  report.comparison = { status: compared.status, stdout: compared.stdout, stderr: compared.stderr }
  assert.equal(compared.status,0,'Complete final-checker import proof failed; retain the current source mismatch.')
  for (const editor of [1,2,3,4]) assert.ok(history.filter(entry => entry.editor===editor).length>=2,'An editor did not sustain genuine commits.')
  assert.ok(evaluations[0].started_epoch_ms<=report.clock.origin_epoch_ms && evaluations.at(-1).body_complete_epoch_ms>=report.clock.rendered_epoch_ms)
  report.acceptance='not_run'; report.repeats=[]; report.validation='complete_approved_proof_pass'
  }catch(error){mainFailure??=error}
  // Restoration remains outside the measured/proof interval, ordinary inputs protocol.
  const unresolved=inputWire.some(entry=>{
    const unknown=entry.response_status===null||entry.response_status===408||entry.response_status>=500||entry.response_status===200&&!entry.response_artifact
    if(!unknown)return false
    return !(report.editor_attempts??[]).some(attempt=>attempt.phase==='apply'&&attempt.request_artifact?.sha256===entry.request_artifact.sha256&&['committed','obsolete_without_commit'].includes(attempt.resolution?.result))
  })
  if(!after || unresolved){
    report.restoration={status:'unconfirmed',reason:unresolved?'Original input apply outcome remains unresolved; no restoration write permitted.':'No complete final live state; no restoration write permitted.'}
    mainFailure??=new Error(report.restoration.reason)
  }else try{
  const current = after.snapshot.inputs
  if(inputContentDigest(current)===inputContentDigest(declaration.live_inputs)){
    report.restoration={status:'already_original',input_version:current.input_version}
  }else{
  const models = current.masters.models.map(row => ({ ...row, name: declaration.live_inputs.masters.models.find(old => old.code===row.code).name }))
  const command = { base_version: current.input_version, changes:{models} }
  const preview = await primary.request.post(prefix+'/inputs/preview',{headers,data:command}); assert.equal(preview.status(),200); assert.equal((await preview.json()).valid,true)
  report.restoration={command,response_status:null,status:'unknown',command_artifact:capture(Buffer.from(JSON.stringify(command)))}
  const response = await primary.request.post(prefix+'/inputs/apply',{headers,data:command}); report.restoration.response_status=response.status()
  if(response.status()!==200)report.restoration.status='rejected'
  const restored = await body(response); report.restoration.artifact=capture(restored.bytes)
  assert.equal(response.status(),200); assert.equal(inputContentDigest(restored.value),inputContentDigest(declaration.live_inputs))
  assert.equal(restored.value.input_version,current.input_version+1)
  const immutableRestore=await get('/inputs?version='+restored.value.input_version)
  assert.deepEqual(immutableRestore.value,restored.value)
  const final = await state(); report.restoration.final=final
  assert.equal(final.head.revision,0); assert.equal(final.head.latest_adoption,0)
  assert.equal(inputContentDigest(final.snapshot.inputs),inputContentDigest(declaration.live_inputs))
  assert.deepEqual({...final.head,input_version:after.head.input_version},after.head,'Ordinary name restoration changed another complete live head field.')
  assert.deepEqual(final.snapshot.plan,after.snapshot.plan);assert.deepEqual(final.snapshot.context,after.snapshot.context)
  assert.deepEqual(final.demand_files,{input_version:final.head.input_version,enablement_date:null,files:[]})
  const originalAgain=await get('/inputs?version=1');assert.deepEqual(originalAgain.value,declaration.live_inputs)
  const previousAgain=await get('/inputs?version='+current.input_version);assert.deepEqual(previousAgain.value,current)
  report.restoration.status='confirmed'
  journal('ordinary_name_restoration_confirmed',{input_version:restored.value.input_version})
  }
  }catch(error){
    report.restoration??={status:'unconfirmed'};report.restoration.failure=String(error.message).split('\n')[0]
    // Preserve an unknown original restore command; never apply it again.
    if(report.restoration.status!=='unknown')report.restoration.status='failed'
    mainFailure??=error
  }
  if(mainFailure)throw mainFailure
  report.status='functional_pass'
  return report
}
