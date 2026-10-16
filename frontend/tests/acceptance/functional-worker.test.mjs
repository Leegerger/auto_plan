import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync,readFileSync,existsSync } from 'node:fs'
import { resolve,dirname } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { guardFunctionalWorker } from './functional-worker.mjs'
import { setTimeout as delay } from 'node:timers/promises'
const directory=dirname(fileURLToPath(import.meta.url)),script=resolve(directory,'functional-worker.fixture.mjs')
const limits={setup_ms:4000,action_ms:5000,cleanup_ms:1000,absolute_ms:10000}
test('readiness cannot consume the independent action budget',{timeout:15000},async()=>{
  const output=mkdtempSync(resolve(tmpdir(),'auto-plan-functional-budget-'))
  console.log('Evidence '+output)
  const result=await guardFunctionalWorker({script,output,cwd:directory,limits,args:[JSON.stringify({limits,setup:1500,action:4000})]})
  assert.equal(result.code,0);assert.ok(existsSync(resolve(output,'completed.json')))
  const cleanup=JSON.parse(readFileSync(resolve(output,'controller-cleanup.json')));assert.equal(cleanup.timed_out,false);assert.deepEqual(cleanup.remaining_live,[])
})

for(const phase of ['setup','action'])test('cooperative '+phase+' timeout retains uncertainty and reaps owned resources',{timeout:10000},async()=>{
  const output=mkdtempSync(resolve(tmpdir(),'auto-plan-functional-soft-'))
  const guardian={setup_ms:4000,action_ms:4000,cleanup_ms:1000,absolute_ms:9000},worker={...guardian,setup_ms:phase==='setup'?500:4000,action_ms:500}
  const result=await guardFunctionalWorker({script,output,cwd:directory,limits:guardian,args:[JSON.stringify({limits:guardian,workerLimits:worker,setup:50,action:50,block:phase})]})
  assert.equal(result.code,1)
  const failure=JSON.parse(readFileSync(resolve(output,'worker-timeout.json')));assert.equal(failure.phase,phase)
  for(const key of ['source_closure','input_acknowledgments','restoration'])assert.equal(failure[key],'unconfirmed')
  const cleanup=JSON.parse(readFileSync(resolve(output,'controller-cleanup.json')));assert.equal(cleanup.worker_exited,true);assert.deepEqual(cleanup.remaining_live,[])
})

for(const phase of ['setup','action'])test('independent guardian interrupts blocked '+phase+' worker without touching unrelated process',{timeout:10000},async()=>{
  const output=mkdtempSync(resolve(tmpdir(),'auto-plan-functional-hard-')),sentinel=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'})
  const bounded={setup_ms:phase==='setup'?1000:3000,action_ms:1000,cleanup_ms:1000,absolute_ms:5000}
  try{
    const raw=readFileSync('/proc/'+sentinel.pid+'/stat','utf8'),sentinelStart=raw.slice(raw.lastIndexOf(')')+2).trim().split(/\s+/)[19]
    await assert.rejects(guardFunctionalWorker({script,output,cwd:directory,limits:bounded,args:[JSON.stringify({limits:bounded,setup:50,block:phase,hardBlock:true,unrelated:{browser_pid:sentinel.pid,browser_start:sentinelStart}})]}),/guardian deadline/)
    process.kill(sentinel.pid,0)
    const failure=JSON.parse(readFileSync(resolve(output,'controller-interruption.json')));assert.equal(failure.phase,phase)
    for(const key of ['source_closure','input_acknowledgments','restoration'])assert.equal(failure[key],'unconfirmed')
    const cleanup=JSON.parse(readFileSync(resolve(output,'controller-cleanup.json')));assert.equal(cleanup.worker_exited,true);assert.deepEqual(cleanup.remaining_live,[])
    assert.ok(cleanup.recorded_owned.every(row=>row.pid!==sentinel.pid))
  }finally{sentinel.kill();await new Promise(done=>sentinel.once('exit',done))}
})

for(const corrupt of ['duplicate','attempt','owner','late','missing','malformed'])test('invalid owned phase '+corrupt+' cannot extend a run',{timeout:10000},async()=>{
  const output=mkdtempSync(resolve(tmpdir(),'auto-plan-functional-invalid-'))
  await assert.rejects(guardFunctionalWorker({script,output,cwd:directory,limits,args:[JSON.stringify({limits,setup:50,action:50,corrupt})]}))
  const cleanup=JSON.parse(readFileSync(resolve(output,'controller-cleanup.json')));assert.equal(cleanup.worker_exited,true);assert.deepEqual(cleanup.remaining_live,[])
})

test('late guardian polling uses actual owned readiness time',{timeout:10000},async()=>{
  const output=mkdtempSync(resolve(tmpdir(),'auto-plan-functional-poll-'))
  const bound={setup_ms:3000,action_ms:1000,cleanup_ms:1000,absolute_ms:5000}
  await assert.rejects(guardFunctionalWorker({script,output,cwd:directory,limits:bound,poll_ms:800,
    args:[JSON.stringify({limits:bound,workerLimits:{...bound,action_ms:5000},setup:50,action:1800})]}),/action guardian deadline/)
  const cleanup=JSON.parse(readFileSync(resolve(output,'controller-cleanup.json')));assert.equal(cleanup.timed_out,true);assert.deepEqual(cleanup.remaining_live,[])
})

for(const action of [1000,100])test('late readiness cannot renew an exhausted setup budget '+action+'ms',{timeout:10000},async()=>{
  const output=mkdtempSync(resolve(tmpdir(),'auto-plan-functional-late-ready-'))
  const bound={setup_ms:1000,action_ms:3000,cleanup_ms:1000,absolute_ms:6000}
  await assert.rejects(guardFunctionalWorker({script,output,cwd:directory,limits:bound,poll_ms:1500,
    args:[JSON.stringify({limits:bound,workerLimits:{...bound,setup_ms:5000},setup:1200,action})]}),/readiness|setup/i)
})

test('malformed ownership still cleans known real children',{timeout:10000},async()=>{
  const output=mkdtempSync(resolve(tmpdir(),'auto-plan-functional-bad-owned-'))
  const bound={setup_ms:3000,action_ms:3000,cleanup_ms:1000,absolute_ms:7000}
  const alive=row=>{try{const raw=readFileSync('/proc/'+row.pid+'/stat','utf8'),fields=raw.slice(raw.lastIndexOf(')')+2).trim().split(/\s+/);return fields[19]===row.start&&fields[0]!=='Z'}catch{return false}}
  try{
    await assert.rejects(guardFunctionalWorker({script,output,cwd:directory,limits:bound,args:[JSON.stringify({limits:bound,setup:50,action:4000,malformedOwned:true})]}))
    const rows=JSON.parse(readFileSync(resolve(output,'rescue.json')))
    assert.ok(rows.every(row=>!alive(row)),'Malformed ownership bypassed known-child cleanup.')
    assert.deepEqual(JSON.parse(readFileSync(resolve(output,'controller-cleanup.json'))).remaining_live,[])
  }finally{
    if(existsSync(resolve(output,'rescue.json'))){const rows=JSON.parse(readFileSync(resolve(output,'rescue.json')));for(const row of rows)if(alive(row))process.kill(row.pid,'SIGKILL');for(let i=0;i<100&&rows.some(alive);i++)await delay(10)}
  }
})


test('fast successful exit cleans a registered detached child and preserves unrelated sentinel',{timeout:10000},async()=>{
  const output=mkdtempSync(resolve(tmpdir(),'auto-plan-functional-fast-exit-')),sentinel=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'})
  const alive=row=>{try{const raw=readFileSync('/proc/'+row.pid+'/stat','utf8'),fields=raw.slice(raw.lastIndexOf(')')+2).trim().split(/\s+/);return fields[19]===row.start&&fields[0]!=='Z'}catch{return false}}
  try{
    const raw=readFileSync('/proc/'+sentinel.pid+'/stat','utf8'),sentinelStart=raw.slice(raw.lastIndexOf(')')+2).trim().split(/\s+/)[19]
    const result=await guardFunctionalWorker({script,output,cwd:directory,limits,poll_ms:800,args:[JSON.stringify({limits,fastExit:true,unrelated:{browser_pid:sentinel.pid,browser_start:sentinelStart}})]})
    assert.equal(result.code,0)
    const rows=JSON.parse(readFileSync(resolve(output,'rescue.json')))
    assert.ok(rows.every(row=>!alive(row)),'Fast child exit orphaned its registered detached resource.')
    process.kill(sentinel.pid,0)
    const cleanup=JSON.parse(readFileSync(resolve(output,'controller-cleanup.json')))
    assert.deepEqual(cleanup.remaining_live,[]);assert.ok(cleanup.recorded_owned.every(row=>row.pid!==sentinel.pid))
  }finally{
    if(existsSync(resolve(output,'rescue.json'))){const rows=JSON.parse(readFileSync(resolve(output,'rescue.json')));for(const row of rows)if(alive(row))process.kill(row.pid,'SIGKILL');for(let i=0;i<100&&rows.some(alive);i++)await delay(10)}
    sentinel.kill();await new Promise(done=>sentinel.once('exit',done))
  }
})


for(const mode of ['omitReady','overAction'])test('fast success cannot bypass '+mode+' phase validation',{timeout:10000},async()=>{
  const output=mkdtempSync(resolve(tmpdir(),'auto-plan-functional-fast-phase-'))
  const bound={setup_ms:4000,action_ms:500,cleanup_ms:1000,absolute_ms:6000}
  await assert.rejects(guardFunctionalWorker({script,output,cwd:directory,limits:bound,poll_ms:1500,args:[JSON.stringify({limits:bound,workerLimits:{...bound,action_ms:5000},fastExit:true,omitReady:mode==='omitReady',action:mode==='overAction'?750:0})]}),/readiness|action guardian deadline/)
  const cleanup=JSON.parse(readFileSync(resolve(output,'controller-cleanup.json')))
  assert.equal(cleanup.worker_exited,true);assert.deepEqual(cleanup.remaining_live,[])
})
