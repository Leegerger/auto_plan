/** Tiny actual upload capture regression; never a business or timing gate. */
import test from 'node:test'
import { attestUpload } from './upload-observations.mjs'
import assert from 'node:assert/strict'
import { chromium } from '@playwright/test'
import { spawn, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdtempSync, readFileSync, writeFileSync, openSync, closeSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { get as httpsGet } from 'node:https'
import { setTimeout as delay } from 'node:timers/promises'
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../../..'),sha=bytes=>createHash('sha256').update(bytes).digest('hex')
const healthy=url=>new Promise(done=>{const request=httpsGet(url,{rejectUnauthorized:false,timeout:1000},response=>{response.resume();done(response.statusCode===200)});request.on('error',()=>done(false));request.on('timeout',()=>request.destroy())})
test('native File transport has exact response-bound received bytes, including a rejected workbook', {skip:process.env.AUTO_PLAN_UPLOAD_CAPTURE_BROWSER!=='1',timeout:90000},async()=>{
  const output=mkdtempSync(resolve(tmpdir(),'auto-plan-upload-capture-')),python=process.env.AUTO_PLAN_INITIAL_IMPORT_LOAD_PYTHON
  process.stdout.write('Evidence '+output+'\n');assert.ok(python)
  const env={...process.env,PYTHONPATH:'src:.'},fixtures=resolve(output,'fixtures'),port=18869,origin='https://localhost:'+port,prefix=origin+'/planning/api'
  const prepared=spawnSync(python,['scripts/acceptance/initial_import_fixture.py','--repo',root,'--output',fixtures],{cwd:root,env,encoding:'utf8',timeout:10000});assert.equal(prepared.status,0,prepared.stderr)
  const log=openSync(resolve(output,'server.log'),'w'),server=spawn(python,['-m','scripts.acceptance.adapter_server','--fixtures',fixtures,'--scenario','r20-loose','--port',String(port),...(process.env.AUTO_PLAN_UPLOAD_CAPTURE_OBSERVER==='1'?['--upload-observations',resolve(output,'received.ndjson')]:[])],{cwd:root,env,detached:true,stdio:['ignore',log,log]})
  let exited=false,browser,browserServer,context
  const finished=new Promise(done=>server.once('exit',()=>{exited=true;done()}))
  const report={status:'fail',acceptance:'not_run',repeats:[],server_pid:server.pid,controls:[],observer:process.env.AUTO_PLAN_UPLOAD_CAPTURE_OBSERVER==='1'}
  const flush=stage=>{report.stage=stage;writeFileSync(resolve(output,'report.json'),JSON.stringify(report,null,2))}
  const bounded=(promise,label)=>Promise.race([promise,delay(10000).then(()=>{throw new Error('Bounded capture phase timed out: '+label)})])
  try{
    flush('startup')
    const until=Date.now()+20000;while(!exited&&Date.now()<until&&!await healthy(prefix+'/ready'))await delay(100)
    assert.ok(!exited&&await healthy(prefix+'/ready'))
    browserServer=await chromium.launchServer({headless:true});report.browser_pid=browserServer.process().pid
    writeFileSync(resolve(output,'owned.json'),JSON.stringify({server_pid:server.pid,browser_pid:report.browser_pid,worker_pid:process.pid}));browser=await chromium.connect(browserServer.wsEndpoint());context=await browser.newContext({ignoreHTTPSErrors:true})
    const headers={Origin:origin,'X-Auto-Plan-Request':'1'}
    assert.equal((await context.request.post(prefix+'/auth/login',{headers,data:{username:'ACCEPTANCE0',password:'company-secret'}})).status(),200)
    const page=await context.newPage();page.setDefaultTimeout(10000)
    await page.goto(origin+'/planning/initialize');await page.getByRole('heading',{name:'首次接入与需求变更',exact:true}).waitFor()
    const responseSession=await context.newCDPSession(page);await responseSession.send('Network.enable',{maxTotalBufferSize:134217728,maxResourceBufferSize:67108864,enableDurableMessages:true})
    const bytes=Buffer.from([80,75,0,255,128,13,10,110,111,116,45,97,110,45,120,108,115,120]),path=resolve(output,'rejected.xlsx');writeFileSync(path,bytes)
    report.expected={sha256:sha(bytes),length:bytes.length}
    async function native(label,session){
      const pending=page.waitForResponse(response=>response.url().includes('/initialization/excel/preview?')&&response.request().method()==='POST');pending.catch(()=>{})
      let event
      const listener=value=>{if(value.request.url.includes('/initialization/excel/preview?'))event=value}
      if(session)session.on('Network.requestWillBeSent',listener)
      await page.getByLabel('上传标准工作簿并预览',{exact:true}).setInputFiles(path)
      flush(label+'-selected');const response=await bounded(pending,label+'-response'),body=await bounded(response.body(),label+'-body'),raw=response.request().postDataBuffer()
      const row={label,status:response.status(),query:Object.fromEntries(new URL(response.url()).searchParams),valid:JSON.parse(body).valid,response_sha256:sha(body),playwright_bytes_available:Buffer.isBuffer(raw),playwright_sha256:raw?sha(raw):null,response_observation:await response.headerValue('x-auto-plan-test-upload')}
      if(session){
        row.cdp_has_post_data=event?.request.hasPostData;row.cdp_post_data_entries=event?.request.postDataEntries??null
        try{const captured=await bounded(session.send('Network.getRequestPostData',{requestId:event.requestId}),'cdp-postdata');row.cdp_utf8_sha256=sha(Buffer.from(captured.postData,'utf8'))}catch(error){row.cdp_failure=String(error.message).split('\n')[0]}
        session.off('Network.requestWillBeSent',listener)
      }
      report.controls.push(row);flush(label+'-captured');assert.equal(row.valid,false,'The declared invalid workbook must be rejected.')
      await page.getByLabel('上传标准工作簿并预览',{exact:true}).waitFor()
      return row
    }
    const first=await bounded(native('native-default'),'first-native')
    const text='tiny invalid workbook control',responsePending=page.waitForResponse(response=>response.url().includes('/initialization/excel/preview?')&&response.request().method()==='POST')
    flush('string-control-start');await bounded(page.evaluate(async({url,text})=>{await fetch(url,{method:'POST',signal:AbortSignal.timeout(10000),headers:{'X-Auto-Plan-Request':'1','Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},body:text})},{url:prefix+'/initialization/excel/preview?base_revision=0&input_version=1&latest_adoption=0',text}),'string-fetch')
    const control=await bounded(responsePending,'string-response');await bounded(control.body(),'string-body');const stringBytes=control.request().postDataBuffer()
    report.controls.push({label:'string-control',status:control.status(),playwright_bytes_available:Buffer.isBuffer(stringBytes),sha256:stringBytes?sha(stringBytes):null,expected_sha256:sha(Buffer.from(text))})
    flush('string-control-captured');const session=responseSession;await session.send('Network.enable',{maxPostDataSize:1048576,maxTotalBufferSize:134217728,maxResourceBufferSize:67108864,enableDurableMessages:true})
    const enhanced=await bounded(native('native-large-postdata',session),'enhanced-native');await session.detach()
    if(report.observer){
      const observations=readFileSync(resolve(output,'received.ndjson'),'utf8').trim().split('\n').map(JSON.parse)
      report.observations=observations
      for(const row of [first,enhanced]){
        const observed=observations.find(value=>value.id===row.response_observation);assert.ok(observed,'No response-bound server observation.')
        const expected={id:row.response_observation,url:prefix+'/initialization/excel/preview?'+new URLSearchParams(row.query),status:row.status,length:bytes.length,sha256:sha(bytes)}
        row.server_received_proof=attestUpload(observed,expected)
        assert.equal(row.playwright_bytes_available,false,'Native Playwright byte omission must remain explicit.')
        assert.throws(()=>attestUpload(undefined,expected))
        const invalid=[{id:'other'},{complete:false},{response_complete:false},{length:bytes.length+1},{sha256:'0'.repeat(64)},{method:'GET'},{path:'/different'},{query:{...row.query,input_version:'999'}},{status:409},{instrumentation:'local file'}]
        for(const change of invalid)assert.throws(()=>attestUpload({...observed,...change},expected))
        row.negative_metadata_controls=invalid.length+1
      }
    }else assert.ok([first.playwright_sha256,enhanced.playwright_sha256,enhanced.cdp_utf8_sha256].includes(sha(bytes)),'Native binary File bytes remain unavailable or inexact in Playwright/CDP.')
    report.status='pass'
  }catch(error){report.failure=String(error.message).split('\n')[0];throw error}
  finally{
    if(context)await context.close().catch(()=>{});if(browser)await browser.close().catch(()=>{});if(browserServer)await browserServer.close().catch(()=>{})
    if(!exited){process.kill(-server.pid,'SIGTERM');await Promise.race([finished,delay(3000)])}
    if(!exited){process.kill(-server.pid,'SIGKILL');await finished}
    report.server_exited=exited;closeSync(log);writeFileSync(resolve(output,'report.json'),JSON.stringify(report,null,2))
  }
})
