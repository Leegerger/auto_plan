/** Public observation interface checks; browser and business proofs live in their workflow tests. */
import test from 'node:test'
import assert from 'node:assert/strict'
import {privateObservation} from './private-observation.mjs'

const gate=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}

test('the original primary is retained while cleanup is pending and a cleanup failure stays separate', async()=>{
 const record={},observer=privateObservation(record),primary=new Error('PRIMARY_POST_S0_DOM'),cleanup=new Error('CLEANUP_VERIFY_FAILED')
 const held=gate(),entered=gate()
 const pending=observer.run(
  ()=>observer.sync('verified_message_dom',()=>{throw primary}),
  ()=>observer.step('cleanup_verify_S0',()=>{entered.resolve();return held.promise}),
 )
 const terminal=pending.then(value=>({value}),error=>({error}))
 await entered.promise
 assert.equal(record.observation.primary_error.message,primary.message)
 assert.equal(record.observation.primary_error.phase,'verified_message_dom')
 assert.equal(record.observation.phases.at(-1).phase,'cleanup_verify_S0')
 assert.equal(record.observation.phases.at(-1).state,'begin')
 held.reject(cleanup)
 assert.equal((await terminal).error,primary)
 assert.equal(record.observation.primary_error.message,primary.message)
 assert.equal(record.observation.cleanup_errors[0].message,cleanup.message)
 assert.equal(record.observation.cleanup_errors[0].phase,'cleanup_verify_S0')
})

test('a pending response body and a pending DOM observation retain distinct phase evidence', async()=>{
 const sourceRecord={},source=privateObservation(sourceRecord),body=gate(),bytes=Buffer.from('{"source":"literal"}')
 const pendingBody=source.step('saved_source_body',()=>body.promise)
 assert.equal(pendingBody,body.promise)
 assert.equal(sourceRecord.observation.phases.at(-1).phase,'saved_source_body')
 assert.equal(sourceRecord.observation.phases.at(-1).state,'begin')
 const domRecord={},dom=privateObservation(domRecord),visible=gate()
 const pendingDom=dom.step('verified_message_dom',()=>visible.promise)
 assert.equal(domRecord.observation.phases.at(-1).phase,'verified_message_dom')
 assert.equal(domRecord.observation.phases.at(-1).state,'begin')
 body.resolve(bytes);assert.equal(await pendingBody,bytes)
 assert.equal(sourceRecord.observation.phases.at(-1).state,'end')
 assert.equal(domRecord.observation.phases.at(-1).state,'begin')
 visible.resolve();await pendingDom
 assert.equal(domRecord.observation.phases.at(-1).state,'end')
})

test('observer clock failures preserve original work errors and separate primary and cleanup diagnostics', async()=>{
 const record={},clockFailure=new Error('OBSERVER_CLOCK_FAILED'),primary=new Error('PRIMARY_DOM'),cleanup=new Error('CLEANUP_FAILED')
 const observer=privateObservation(record,{clock:()=>{throw clockFailure}})
 await assert.rejects(observer.run(
  ()=>observer.sync('verified_message_dom',()=>{throw primary}),
  ()=>observer.step('cleanup_verify',()=>Promise.reject(cleanup)),
 ),error=>error===primary)
 assert.equal(record.observation.primary_error.message,primary.message)
 assert.equal(record.observation.cleanup_errors[0].message,cleanup.message)
 assert.ok(record.observation.observer_errors.length>0)
})

test('successful work propagates the original cleanup failure without inventing a primary error', async()=>{
 const record={},observer=privateObservation(record),cleanup=new Error('CLEANUP_FAILED')
 await assert.rejects(observer.run(()=>({literal:'result'}),()=>observer.sync('cleanup',()=>{throw cleanup})),error=>error===cleanup)
 assert.equal(record.observation.primary_error,null)
 assert.equal(record.observation.cleanup_errors[0].message,cleanup.message)
})

test('later primary reports cannot replace the first failure or its phase', ()=>{
 const record={},observer=privateObservation(record),primary=new Error('FIRST_FAILURE'),later=new Error('LATER_FAILURE')
 assert.throws(()=>observer.sync('verified_output',()=>{throw primary}),error=>error===primary)
 observer.primary(primary)
 assert.throws(()=>observer.sync('cleanup_unroute',()=>{throw later}),error=>error===later)
 observer.cleanup(later);observer.primary(later)
 assert.deepEqual(record.observation.primary_error,{phase:'verified_output',error_class:'Error',message:'FIRST_FAILURE'})
 assert.deepEqual(record.observation.cleanup_errors,[{phase:'cleanup_unroute',error_class:'Error',message:'LATER_FAILURE'}])
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
