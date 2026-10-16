/** Five actual identities; this collector has no accepted timing result. */
import assert from 'node:assert/strict'
import { chromium } from '@playwright/test'
import { spawn, spawnSync } from 'node:child_process'
import { openSync, readFileSync, writeFileSync, renameSync, readdirSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { get as httpsGet } from 'node:https'
import { createHash } from 'node:crypto'
const sha=bytes=>createHash('sha256').update(bytes).digest('hex')
import { setTimeout as delay } from 'node:timers/promises'
import { installInputConsumptionTrace } from './input-consumption-trace.mjs'
import { exportInputBuffers, inputTraceCleanupDeadline } from './input-trace-collector.mjs'
import { importBrowserContract } from './initial-import-browser-support.mjs'
import { functionalBudget, resourceRegistration, FUNCTIONAL_LIMITS } from './functional-worker.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const healthy = url => new Promise(done => { const request=httpsGet(url,{rejectUnauthorized:false,timeout:1000},response=>{response.resume();done(response.statusCode===200)});request.on('error',()=>done(false));request.on('timeout',()=>request.destroy()) })
const start = pid => {try{const raw=readFileSync('/proc/'+pid+'/stat','utf8');return raw.slice(raw.lastIndexOf(')')+2).trim().split(/\s+/)[19]}catch{return null}}

async function main() {
    const python = process.env.AUTO_PLAN_INITIAL_IMPORT_LOAD_PYTHON
    assert.ok(python)
    const port = Number(process.env.AUTO_PLAN_INITIAL_IMPORT_LOAD_PORT ?? 18869), origin='https://localhost:'+port
    const output = process.argv[2]
    assert.ok(output, 'The owning controller must provide a new output directory.')
    const report={acceptance:'not_run',repeats:[],status:'fail',kind:'functional first-import browser probe; no SLA',functional_limits:FUNCTIONAL_LIMITS}
    const budget=functionalBudget({output,onTimeout:phase=>{report.workflow_timeout={phase,deadline_seconds:FUNCTIONAL_LIMITS[phase==='setup'?'setup_ms':'action_ms']/1000,source_closure:'unconfirmed',input_acknowledgments:'unconfirmed',restoration:'unconfirmed'};report.restoration={status:'unconfirmed',reason:'Functional phase deadline interrupted source closure; resource cleanup does not establish restoration.'}}})
    const bootstrap=resolve(output,'bootstrap'), fixtures=resolve(output,'fixtures')
    const inputTrace=process.env.AUTO_PLAN_INITIAL_IMPORT_INPUT_TRACE==='1' ? {events:[],attempts:[]} : undefined
    const inputLedger=resolve(output,'input-transitions.ndjson')
    const env={...Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('AUTO_PLAN_'))),PYTHONPATH:resolve(root,'src')+':'+root}
    const prepare = template => {
      const result=spawnSync(python,['scripts/acceptance/initial_import_fixture.py','--repo',root,'--output',template?fixtures:bootstrap,...(template?['--template',template]:[])],{cwd:root,env,encoding:'utf8',timeout:30000})
      assert.equal(result.status,0,result.stderr)
    }
    const uploadObservations=resolve(output,'upload-observations.ndjson')
    let log,server,operation,exited=true,finished=Promise.resolve(),stopping=false
    const active=()=>assert.equal(stopping,false,'Functional setup was interrupted.')
    const owned={attempt:process.env.AUTO_PLAN_FUNCTIONAL_ATTEMPT,worker_pid:process.pid,worker_pgid:process.pid,worker_start:start(process.pid),registrations:[]}
    const recordOwned=()=>{writeFileSync(resolve(output,'owned-processes.tmp'),JSON.stringify(owned));renameSync(resolve(output,'owned-processes.tmp'),resolve(output,'owned-processes.json'))}
    let serverError,browser,browserServer,contexts=[],workflow,workflowSettled=false
    Object.assign(report,{bootstrap,fixtures})
    writeFileSync(resolve(output,'report.json'),JSON.stringify(report,null,2))
    try {
      operation=(async()=>{
      prepare();active()
      log=openSync(resolve(output,'server.log'),'w')
      server=spawn(python,['-m','scripts.acceptance.adapter_server','--fixtures',bootstrap,'--scenario','r20-loose','--port',String(port),'--upload-observations',uploadObservations,...(inputTrace?['--input-transitions',inputLedger]:[])],{cwd:root,env,detached:true,stdio:['ignore',log,log]})
      exited=false
      finished=new Promise(done=>{server.once('exit',(code,signal)=>{exited=true;done([code,signal])});server.once('error',error=>{exited=true;serverError=error;done([null,null])})})
      owned.registrations.push(resourceRegistration(server.pid));Object.assign(owned,{server_pid:server.pid,server_pgid:server.pid,server_start:start(server.pid)});recordOwned()
      Object.assign(report,{owned_server_pid:server.pid,owned_server_pgid:server.pid})
      const until=Date.now()+30000
      while(!exited && Date.now()<until && !await healthy(origin+'/planning/api/ready'))await delay(100)
      assert.ok(!exited && await healthy(origin+'/planning/api/ready'))
      if(serverError)throw serverError
      browserServer=await chromium.launchServer({headless:true})
      active()
      owned.browser_pid=browserServer.process().pid;owned.browser_start=start(owned.browser_pid);owned.registrations.push(resourceRegistration(owned.browser_pid));recordOwned()
      browser=await chromium.connect(browserServer.wsEndpoint())
      active()
      const pages=[]
      for(let editor=0;editor<5;editor++){
        const context=await browser.newContext({ignoreHTTPSErrors:true});contexts.push(context)
        active()
        if(inputTrace)await context.addInitScript({content:'('+installInputConsumptionTrace.toString()+')(window,'+JSON.stringify({basePath:'/planning',pageIdentity:'first-import-editor-'+editor})+')'})
        await context.addInitScript(()=>{window.addEventListener('change',event=>{if(event.target instanceof HTMLInputElement && event.target.type==='file')window.__initialImportClickEpoch=performance.timeOrigin+performance.now()},true)})
        const login=await context.request.post(origin+'/planning/api/auth/login',{headers:{Origin:origin,'X-Auto-Plan-Request':'1'},data:{username:'ACCEPTANCE'+editor,password:'company-secret'}})
        active();assert.equal(login.status(),200);pages.push(await context.newPage())
      }
      const template=await pages[0].request.get(origin+'/planning/api/initialization/template.xlsx')
      active()
      assert.equal(template.status(),200)
      const templatePath=resolve(output,'public-template.xlsx'),templateBytes=await template.body();writeFileSync(templatePath,templateBytes)
      report.public_template={path:templatePath,sha256:sha(templateBytes),status:template.status(),url:template.url()}
      report.bootstrap_manifest_sha256=sha(readFileSync(resolve(bootstrap,'manifest.json')))
      report.source_files=Object.fromEntries(['scripts/acceptance/adapter_server.py','scripts/acceptance/upload_observer.py','frontend/tests/acceptance/upload-observations.mjs','scripts/acceptance/initial_import_fixture.py','scripts/acceptance/initial_import_load.py','frontend/tests/acceptance/initial-import-load.browser.test.mjs','frontend/tests/acceptance/initial-import-browser-worker.mjs','frontend/tests/acceptance/initial-import-browser-support.mjs','frontend/tests/acceptance/functional-worker.mjs','frontend/dist/index.html'].map(path=>[path,sha(readFileSync(resolve(root,path)))]))
      prepare(templatePath)
      if(inputTrace){
        const helpers=['scripts/acceptance/input_transition_observer.py','frontend/tests/acceptance/input-consumption-trace.mjs','frontend/tests/acceptance/input-trace-join.mjs','frontend/tests/acceptance/input-trace-collector.mjs','frontend/tests/acceptance/input-trace-replay.mjs','frontend/tests/acceptance/input-editors.mjs','frontend/tests/acceptance/input-load.mjs']
        for(const path of helpers)report.source_files[path]=sha(readFileSync(resolve(root,path)))
        const revision=spawnSync('git',['-C',root,'rev-parse','HEAD'],{encoding:'utf8'})
        const tree=spawnSync('git',['-C',root,'rev-parse','HEAD^{tree}'],{encoding:'utf8'})
        assert.equal(revision.status,0);assert.equal(tree.status,0)
        const tracked=spawnSync('git',['-C',root,'ls-files','-z','--','src','frontend/src'],{encoding:'utf8'})
        assert.equal(tracked.status,0)
        const productFiles=Object.fromEntries(tracked.stdout.split('\0').filter(Boolean).map(path=>[path,sha(readFileSync(resolve(root,path)))]))
        const distFiles={}
        const dist=(directory)=>{for(const row of readdirSync(resolve(root,directory),{withFileTypes:true})){const path=directory+'/'+row.name;if(row.isDirectory())dist(path);else if(row.isFile())distFiles[path]=sha(readFileSync(resolve(root,path)))}}
        dist('frontend/dist')
        const manifest=JSON.parse(readFileSync(resolve(fixtures,'manifest.json'))),scenario=manifest.scenarios[0]
        const declarationBytes=readFileSync(resolve(fixtures,scenario.initial_import_load.path)),declaration=JSON.parse(declarationBytes)
        assert.equal(sha(declarationBytes),scenario.initial_import_load.sha256)
        const workbookBytes=readFileSync(resolve(fixtures,declaration.workbook.path));assert.equal(sha(workbookBytes),declaration.workbook.sha256)
        const config={kind:'TEST-ONLY first-import input trace v1',source_revision:revision.stdout.trim(),source_tree:tree.stdout.trim(),
          predecessor:'original100: /tmp/auto-plan-initial-import-browser-udzMPI',origin,functional_limits:FUNCTIONAL_LIMITS,
          business_bindings:{scenario:scenario.id,source:scenario.source,reference:scenario.reference,action:scenario.action,import:scenario.import,declaration:scenario.initial_import_load,workbook:declaration.workbook},
          fixture_manifest_sha256:sha(readFileSync(resolve(fixtures,'manifest.json'))),source_files:report.source_files,actual_product_files:productFiles,actual_dist_files:distFiles,
          limitations:['No raw per-reload GET body proof.','Server response digest unavailable.','APIRequestContext evaluations are Node-only.']}
        const bytes=Buffer.from(JSON.stringify(config,null,2)),path=resolve(output,'input-trace-config.json');writeFileSync(path,bytes)
        report.input_trace={status:'pending',config:{path,sha256:sha(bytes)}}
      }
      writeFileSync(resolve(output,'report.json'),JSON.stringify(report,null,2))
      report.instrumentation='TEST-ONLY passive ASGI received upload bytes; no formal timing qualification'
      workflow=importBrowserContract({pages,fixtures,output,origin,python,repo:root,report,uploadObservations,inputTrace,onReady:proof=>budget.ready(proof)})
      workflow.finally(()=>{workflowSettled=true}).catch(()=>{})
      await workflow
      })()
      operation.catch(()=>{})
      await Promise.race([operation,budget.failure])
      budget.complete()
    } catch(error) {report.status='fail';report.failure=String(error.message).split('\n')[0];throw error}
    finally {
      budget.close()
      stopping=true
      if(inputTrace){
        try{
          // Consume only the remainder of the published cooperative phase cutoff.
          const phase=JSON.parse(readFileSync(resolve(output,'functional-phase.json')))
          assert.equal(phase.attempt,owned.attempt);assert.equal(phase.pid,process.pid);assert.equal(phase.start,owned.worker_start)
          const buffers=await exportInputBuffers(contexts,{deadlineEpochMs:inputTraceCleanupDeadline(phase,FUNCTIONAL_LIMITS)})
          if(buffers){
            const bytes=Buffer.from(JSON.stringify(buffers)),path=resolve(output,'input-browser-events.json')
            writeFileSync(path,bytes);inputTrace.browserBytes=bytes
            report.input_trace={...report.input_trace,status:'partial',browser_export:{path,sha256:sha(bytes)}}
          }else report.input_trace={...report.input_trace,status:'partial',browser_export:'unconfirmed'}
        }catch{
          // Instrumentation failure cannot skip owned browser/server cleanup.
          report.input_trace={...report.input_trace,status:'partial',browser_export:'unconfirmed'}
        }
      }
      if(report.workflow_timeout&&operation)await Promise.race([operation.catch(()=>{}),delay(1000)])
      for(const context of contexts)await context.close().catch(()=>{})
      if(browser)await browser.close()
      if(browserServer)await browserServer.close()
      if(!exited){try{process.kill(-server.pid,'SIGTERM')}catch(error){if(error.code!=='ESRCH')throw error};await Promise.race([finished,delay(3000)])}
      if(!exited){try{process.kill(-server.pid,'SIGKILL')}catch(error){if(error.code!=='ESRCH')throw error};await finished}
      if(report.workflow_timeout){
        if(workflow)await Promise.race([workflow.catch(()=>{}),delay(2000)])
        report.workflow_timeout.workflow_settled_after_teardown=workflowSettled
        report.interrupted_workflow_restoration=report.restoration
        report.restoration={status:'unconfirmed',reason:'The workflow deadline interrupted source closure; resource cleanup does not establish restoration.'}
      }
      if(inputTrace){
        const retain=(name,bytes)=>{const path=resolve(output,name);writeFileSync(path,bytes);return {path,sha256:sha(bytes)}}
        const index=inputTrace.attempts.map(entry=>({editor:entry.editor,sequence:entry.sequence,phase:entry.phase,input_trace:entry.input_trace,
          response_status:entry.response_status,request_artifact:entry.request_artifact,response_artifact:entry.response_artifact}))
        report.input_trace={...report.input_trace,status:'partial',
          node_events:retain('input-node-events.json',Buffer.from(JSON.stringify(inputTrace.events))),
          attempts:retain('input-trace-attempts.json',Buffer.from(JSON.stringify(index))),
          interpretation:'Primitive capture only; run input-trace-replay.mjs offline. No protocol/document/resource claim before strict replay.'}
        try{const bytes=readFileSync(inputLedger);report.input_trace.server_ledger={path:inputLedger,sha256:sha(bytes)}}catch{/* Missing ledger remains unknown. */}
        if(report.workflow_timeout)report.input_trace={...report.input_trace,source_closure:'unconfirmed',input_acknowledgments:'unconfirmed',restoration:'unconfirmed'}
      }
      report.server_exited=exited
      writeFileSync(resolve(output,'report.json'),JSON.stringify(report,null,2))
    }
}
main().catch(error=>{process.stderr.write(String(error.message).split('\n')[0]+'\n');process.exitCode=1})
