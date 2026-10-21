import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gunzipSync } from 'node:zlib'
import { createHash } from 'node:crypto'
import { writeFullSharedFixture } from './private-shared-fixture.mjs'
import { sharedEpochExpectation, FULL_SHARED_CONTRACT, assertJobLifetime } from './private-shared-workload.mjs'
const root=resolve(fileURLToPath(new URL('../../..',import.meta.url))),sha=bytes=>createHash('sha256').update(bytes).digest('hex')
test('full shared successor preserves exact predecessor/assets/gates and declares M00/M39 effects',()=>{
 const dir=mkdtempSync(resolve(tmpdir(),'full-shared-fixture-'))
 try{
  const {scenario,declaration,source}=writeFullSharedFixture(root,dir),parent=JSON.parse(readFileSync(resolve(dir,'predecessor-full-private-manifest.json'))),manifest=JSON.parse(readFileSync(resolve(dir,'manifest.json')))
  assert.equal(scenario.id,'r20-loose-private-shared-save-v1');assert.deepEqual({...manifest,scenarios:[]},{...parent,scenarios:[]})
  assert.deepEqual(declaration.shared_save,FULL_SHARED_CONTRACT)
  for(const key of ['source','reference','action','import'])assert.deepEqual(scenario[key],parent.scenarios[0][key])
  assert.equal(source.plan.segments.length,4200);assert.equal(source.plan.switches.length,418)
  for(const [editor,target] of declaration.targets.entries()){
   const model=editor===0?'M00':'M39',line=editor===0?'L00':'L01'
   assert.equal(target.segment_id,'S-2027-01-24-'+model);assert.equal(target.operation.quantity,99)
   assert.deepEqual(target.expected.effects[0],['CAP',line,'2027-01-24'])
   assert.equal(target.expected.effects.length,34);assert.ok(target.expected.effects.slice(1).every(row=>row.includes(model)))
   assert.equal(target.expected.hours_upper_inclusive,'18')
  }
  const bytes=gunzipSync(readFileSync(resolve(dir,scenario.action.path)));assert.equal(sha(bytes),scenario.action.sha256);assert.equal(JSON.parse(bytes).request.time_limit_seconds,90)
 }finally{rmSync(dir,{recursive:true})}
})

test('epoch expectations use declared parent and submitted action, never arbitrary returned changes',()=>{
 const dir=mkdtempSync(resolve(tmpdir(),'full-shared-expectation-'))
 try{
  const {declaration,source}=writeFullSharedFixture(root,dir),saved=structuredClone(source.plan);saved.segments.find(row=>row.id==='S-2027-01-24-M00').quantity=99
  const epochs=new Map([[1,source.plan],[2,saved],[3,source.plan]]),target=declaration.targets[1],row=source.plan.segments.find(row=>row.id===target.segment_id)
  const command={base_revision:1,input_version:1,latest_adoption:1,operations:[{kind:'change_quantity',segment_id:row.id,quantity:99,end_date:row.date,scope:{start_date:row.date,end_date:row.date,line_codes:[row.line_code],model_codes:[row.model_code]}}]}
  const options={snapshot:source,context:{},epochs,command,target,parent:2},expected=sharedEpochExpectation(options)
  assert.equal(expected.totals.expected,419998);assert.equal(expected.totals.gap,2);assert.equal(expected.weekly.length,600);assert.equal(expected.effects.length,34)
  assert.equal(expected.objective[14],'2');assert.equal(expected.objective[29],'2');assert.equal(expected.objective[30],'418');assert.equal(expected.objective[31],'2')
  assert.equal(sharedEpochExpectation({...options,parent:3}).totals.expected,419999)
  assert.equal(sharedEpochExpectation({...options,target:null,command:{...command,operations:[]}}).totals.expected,419999)
  for(const mutate of [value=>{value.parent=4},value=>{value.command.input_version=2},value=>{value.command.operations[0].quantity=98},value=>{value.target.segment_id='S-2027-01-24-M02'}]){
   const value={...options,command:structuredClone(command),target:structuredClone(target)};mutate(value);assert.throws(()=>sharedEpochExpectation(value))
  }
 }finally{rmSync(dir,{recursive:true})}
})


test('job lifetime requires nonce, actual process identity, source pins and complete frozen digest',()=>{
 const frozen={snapshot:{test_fixture:true},source_revision:1,input_version:1,latest_adoption:1,request:{mode:'local'}}
 const sorted=value=>Array.isArray(value)?value.map(sorted):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,sorted(value[key])])):value
 const digest=sha(JSON.stringify(sorted(frozen))),job={job_id:'J'},observed={nonce:'N',request_started_epoch_ms:100,epoch_ms:102,body:{job_id:'J',run_id:'R',generation:'G',source_revision:1,input_version:1,latest_adoption:1,state:'running'}}
 const owner={job_id:'J',run_id:'R',generation:'G',pid:123,start:'456',pgid:123,frozen_sha256:digest,source_revision:1,input_version:1,latest_adoption:1}
 const rows=[{kind:'spawned',...owner,at_ns:1},{kind:'read',...owner,nonce:'N',state:'running',process_alive:true,started_ns:2,finished_ns:3}]
 assert.equal(assertJobLifetime(rows,job,observed,frozen).pid,123)
 for(const mutate of [r=>{r[1].source_revision=2},r=>{r[1].input_version=2},r=>{r[1].latest_adoption=2},r=>{r[0].frozen_sha256=r[1].frozen_sha256='0'.repeat(64)},r=>{r[1].generation='OTHER'},r=>{r[1].start='OTHER'},r=>{r[1].process_alive=false},r=>{r[1].nonce='OTHER'},r=>{r.push({...r[1]})}]){
  const changed=structuredClone(rows);mutate(changed);assert.throws(()=>assertJobLifetime(changed,job,observed,frozen))
 }
})
