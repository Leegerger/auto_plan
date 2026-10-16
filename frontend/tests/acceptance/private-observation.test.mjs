/** Actual private-save orchestration with fake UI/API ports; never a browser or business proof. */
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import vm from 'node:vm'
import {canonical,assertPlan} from './contracts.mjs'
import {privateObservation} from './private-observation.mjs'

const text=readFileSync(new URL('./run.mjs',import.meta.url),'utf8')
const start=text.indexOf('async function privateSharedSaveContract('),end=text.indexOf('\nasync function privateEditCycle(',start)
assert.ok(start>0&&end>start)
const gate=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
function fixture({full=true,makeObservation=privateObservation,unrouteFailure=false}={}){
 const primary=new Error('PRIMARY_POST_S0_DOM'),cleanup=new Error('CLEANUP_VERIFY_FAILED'),entered=gate(),held=gate(),calls=[]
 const row={id:'S',date:'2027-01-24',line_code:'L00',model_code:'M00',order:1,quantity:100,hours:'1'}
 const plan={segments:[row],switches:[],initial_states:[],ordinary_idle:[],current_day_witnesses:[]},changed=structuredClone(plan);changed.segments[0].quantity=99
 const command={base_revision:1,input_version:1,latest_adoption:1,operation_id:'S0',operations:[{kind:'change_quantity',segment_id:'S',quantity:99}]}
 const response={status:()=>200,body:async()=>Buffer.from(JSON.stringify({status:'COMMITTED',revision:2,input_version:1,latest_adoption:1,pending:false})),request:()=>({postDataJSON:()=>({operation_id:'S0'})})}
 let interceptor,actualPrimary
 const page={request:{get:async(_url)=>{calls.push('get:revision');return {ok:()=>true,status:()=>200,json:async()=>({snapshot:{plan}})}},post:async(path)=>{calls.push('json:'+path);return {ok:()=>true,status:()=>200,json:()=>{entered.resolve();return held.promise}}}},route:async(path,fn)=>{calls.push('route');interceptor=fn},unroute:async()=>{calls.push('unroute');if(unrouteFailure&&actualPrimary)throw cleanup},
  getByText:name=>({name}),getByRole:(kind,{name})=>({name,click:async()=>{
   calls.push('click:'+name)
   if(name==='共享保存')await interceptor({fetch:async()=>{calls.push('forward:S0');return response},request:()=>({postDataJSON:()=>command}),abort:async()=>calls.push('drop:S0')})
  }})}
 const evidence={editor_actions:[]},before={value:{source_revision:1,input_version:1,latest_adoption:1,snapshot:{plan}},artifact:{path:'BEFORE'}}
 const expect=locator=>({toBeVisible:async()=>{calls.push('visible:'+locator.name);if(locator.name.startsWith('已核实前次操作成功')){actualPrimary=primary;throw primary}},toBeDisabled:async()=>calls.push('disabled:'+locator.name),toHaveCount:async()=>calls.push('count:'+locator.name)})
 const realmAssert={...assert,deepEqual:(a,b,...rest)=>assert.deepEqual(structuredClone(a),structuredClone(b),...rest)}
 const context={privateObservation:makeObservation,assert:realmAssert,headers:{},Buffer,structuredClone,performance,evidence,source:{plan},localWorkload:{targets:[{editor:0,segment_id:'S'}]},fullSharedSave:full,initialState:{head:{context:{}}},pages:[page],expect,canonical,assertPlan,
  api:x=>x,deferred:gate,writeFileSync:()=>calls.push('injector-artifact'),resolve:(...x)=>x.join('/'),output:'FAKE',captureLocalBody:()=>({path:'FAKE',sha256:'FAKE'}),
  assertDaily:async()=>{},previewChange:async(_page,_row,_quantity,edit)=>{calls.push('preview');edit.clicked_epoch_ms=100;edit.browser_after_result_frames_epoch_ms=101;return {command,response:{body:async()=>Buffer.from('{}')},result:{plan:changed}}},
  assertManualRequest:()=>{},assertPrivateEpochResult:()=>{},primaryPlans:[],privateEpochs:new Map(),requestFor:()=>{calls.push('requestFor:S0');return Promise.resolve({postDataJSON:()=>command})},
  responseFor:()=>{calls.push('responseFor:verify');return Promise.resolve(response)},arm:async()=>{},clicked:async(_page,clock)=>{clock.clicked_epoch_ms=102},registerOperation:()=>{calls.push('register:S0');return {}},
  idle:async()=>{},queue:()=>({name:'queue'}),privateSource:async()=>before,randomUUID:()=> 'T0',persist:()=>calls.push('persist')}
 vm.createContext(context)
 const jsonStart=text.indexOf('async function json('),jsonEnd=text.indexOf('\nasync function readState(',jsonStart)
 context.json=vm.runInContext('('+text.slice(jsonStart,jsonEnd)+')',context)
 const invoke=vm.runInContext('('+text.slice(start,end)+')',context)
 const pending=invoke(before,{},async()=>({body:{state:'running'}}))
 const terminal=pending.then(value=>({value}),error=>({error}))
 return {primary,cleanup,entered,held,calls,evidence,terminal,get actualPrimary(){return actualPrimary}}
}

test('post-S0 primary error is recorded while original cleanup is pending and stays primary', {timeout:2000},async()=>{
 const f=fixture();let settled
 try{
  await f.entered.promise
  assert.ok(f.calls.some(x=>x.startsWith('visible:已核实前次操作成功')), 'The exact DOM sentinel boundary was not reached.')
  assert.equal(f.actualPrimary,f.primary,'The actual DOM port must throw that exact original sentinel object.')
  const observation=f.evidence.private_shared_save.observation
  assert.equal(observation?.primary_error?.message,'PRIMARY_POST_S0_DOM','Actual post-S0 error must be latched before awaiting restore.')
  assert.equal(observation.primary_error.phase,'verified_message_dom')
  assert.equal(observation.phases.at(-1).state,'begin')
 }finally{f.held.reject(f.cleanup);settled=await f.terminal}
 assert.equal(settled.error,f.primary,'Cleanup must not mask the original UI error object.')
 assert.equal(f.calls.filter(x=>x==='forward:S0').length,1)
 assert.equal(f.calls.filter(x=>x==='json:/plans/verify').length,1)
})

test('actual direct source body pending is distinct from a pending DOM wait', {timeout:2000},async()=>{
 const source=readFileSync(new URL('./run.mjs',import.meta.url),'utf8'),a=source.indexOf('async function privateSource('),b=source.indexOf('\nfunction privateEpochPlan(',a)
 const record={},observation=privateObservation(record),body=gate(),bodyEntered=gate(),calls=[],artifact={path:'EXISTING_CAPTURE'}
 const response={status:()=>200,body:()=>{calls.push('body');bodyEntered.resolve();return body.promise}}
 const page={request:{get:()=>{calls.push('get');return Promise.resolve(response)}}}
 const context={pages:[page],headers:{},api:x=>x,assert,JSON,captureLocalBody:()=>{calls.push('capture');return artifact}}
 vm.createContext(context);const invoke=vm.runInContext('('+source.slice(a,b)+')',context)
 const pending=invoke(observation,'saved_source');let result
 try{
  await bodyEntered.promise
  assert.equal(record.observation.phases.at(-1)?.phase,'saved_source_body')
  assert.equal(record.observation.phases.at(-1)?.state,'begin')
 }finally{body.resolve(Buffer.from('{"source":"literal"}'));result=await pending}
 assert.equal(result.artifact,artifact);assert.deepEqual(calls,['get','body','capture']);assert.equal(record.observation.primary_error,null)
 const dom=gate(),domRecord={},domObservation=privateObservation(domRecord);const original=dom.promise
 assert.equal(domObservation.step('verified_message_dom',()=>original),original)
 assert.equal(domRecord.observation.phases.at(-1).phase,'verified_message_dom');assert.equal(domRecord.observation.phases.at(-1).state,'begin');dom.resolve();await original
})


test('observer clock failures do not replace the real primary or cleanup errors', {timeout:2000},async()=>{
 const observerError=new Error('OBSERVER_CLOCK_FAILED')
 const f=fixture({makeObservation:record=>privateObservation(record,{clock:()=>{throw observerError}})})
 await f.entered.promise
 assert.equal(f.actualPrimary,f.primary)
 assert.equal(f.evidence.private_shared_save.observation.primary_error.message,f.primary.message)
 f.held.reject(f.cleanup);const settled=await f.terminal
 assert.equal(settled.error,f.primary)
 const observation=f.evidence.private_shared_save.observation
 assert.equal(observation.cleanup_errors[0].message,f.cleanup.message)
 assert.ok(observation.observer_errors.length>0)
 assert.equal(f.calls.filter(x=>x==='forward:S0').length,1)
 assert.equal(f.calls.filter(x=>x==='json:/plans/verify').length,1)
})

test('cleanup unroute failure remains secondary to the actual primary', {timeout:2000},async()=>{
 const f=fixture({unrouteFailure:true});const result=await f.terminal
 assert.equal(result.error,f.primary)
 assert.equal(f.evidence.private_shared_save.observation.primary_error.message,f.primary.message)
 assert.equal(f.evidence.private_shared_save.observation.cleanup_errors[0].message,f.cleanup.message)
 assert.equal(f.calls.filter(x=>x==='json:/plans/verify').length,0,'The original unroute failure must still stop later cleanup work.')
})

test('nonfull probe keeps its original calls and cleanup failure without observation fields', {timeout:2000},async()=>{
 let created=0;const f=fixture({full:false,makeObservation:()=>{created++;throw new Error('MUST_NOT_CREATE')}})
 await f.entered.promise;assert.equal(f.actualPrimary,f.primary);assert.equal(created,0)
 f.held.reject(f.cleanup);const result=await f.terminal
 assert.equal(result.error,f.cleanup)
 assert.equal(f.evidence.private_shared_save.observation,undefined)
 assert.equal(f.calls.filter(x=>x==='forward:S0').length,1)
 assert.equal(f.calls.filter(x=>x==='json:/plans/verify').length,1)
 assert.equal(f.calls.filter(x=>x==='unroute').length,2)
})

test('primitive observation preserves original promise and returned value identities', async()=>{
 const record={},calls=[],value={literal:'result'},original=Promise.resolve(value)
 const observer=privateObservation(record)
 assert.equal(observer.step('body',()=>{calls.push('body');return original}),original)
 assert.equal(await original,value);assert.deepEqual(calls,['body'])
 assert.deepEqual(record.observation.phases.map(x=>x.state),['begin','end'])
 Object.freeze(record.observation.phases)
 const returned=observer.sync('assertion',()=>{calls.push('assertion');return value})
 assert.equal(returned,value);assert.deepEqual(calls,['body','assertion'])
 assert.ok(record.observation.observer_errors.length>=2)
})

test('an unknown thenable is forwarded once and never called completed by the observer', async()=>{
 const record={},observer=privateObservation(record),value={literal:1};let consumed=0
 const thenable={then(done){consumed++;done(value)}}
 assert.equal(observer.step('unknown_body',()=>thenable),thenable)
 assert.equal(consumed,0);assert.equal(record.observation.phases.at(-1).state,'returned_unobserved')
 assert.equal(await thenable,value);assert.equal(consumed,1)
 assert.ok(!record.observation.phases.some(x=>x.state==='end'))
})
