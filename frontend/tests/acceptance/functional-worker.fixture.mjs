import { spawn } from 'node:child_process'
import { readFileSync,writeFileSync,unlinkSync } from 'node:fs'
import { resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { functionalBudget, resourceRegistration } from './functional-worker.mjs'
const output=process.argv[2],options=JSON.parse(process.argv[3]),start=pid=>{const raw=readFileSync('/proc/'+pid+'/stat','utf8');return raw.slice(raw.lastIndexOf(')')+2).trim().split(/\s+/)[19]}
if(options.fastExit)await delay(250)
const resource=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{detached:true,stdio:'ignore'})
writeFileSync(resolve(output,'owned-processes.json'),JSON.stringify({server_pid:resource.pid,server_start:start(resource.pid),attempt:process.env.AUTO_PLAN_FUNCTIONAL_ATTEMPT,worker_pid:process.pid,worker_start:start(process.pid),registrations:[resourceRegistration(resource.pid)],...options.unrelated}))
writeFileSync(resolve(output,'rescue.json'),JSON.stringify([{pid:process.pid,start:start(process.pid)},{pid:resource.pid,start:start(resource.pid)}]))
if(options.malformedOwned)setTimeout(()=>writeFileSync(resolve(output,'owned-processes.json'),'{'),100)
const proof={commits:[1,2,3,4].map(editor=>({editor,input_version:editor+1})),evaluation:{status:200,artifact:{sha256:'a'.repeat(64)}}}
const budget=functionalBudget({output,limits:options.workerLimits??options.limits,onTimeout:phase=>writeFileSync(resolve(output,'worker-timeout.json'),JSON.stringify({phase,source_closure:'unconfirmed',input_acknowledgments:'unconfirmed',restoration:'unconfirmed'}))})
if(options.fastExit){if(!options.omitReady)budget.ready(proof);await delay(options.action??0);if(!options.omitReady)budget.complete();process.exit(0)}
try{
  const run=async()=>{if(options.block==='setup')await new Promise(()=>{});await delay(options.setup);budget.ready(proof);if(options.corrupt){const file=resolve(output,'functional-phase.json'),value=JSON.parse(readFileSync(file));if(options.corrupt==='duplicate')value.sequence=2;else if(options.corrupt==='attempt')value.attempt='wrong';else if(options.corrupt==='owner')value.start='wrong';else if(options.corrupt==='late')value.ready_epoch_ms-=10000;if(options.corrupt==='missing')unlinkSync(file);else writeFileSync(file,options.corrupt==='malformed'?'{':JSON.stringify(value));await new Promise(()=>{})}if(options.block==='action')await new Promise(()=>{});await delay(options.action);writeFileSync(resolve(output,'completed.json'),JSON.stringify({status:'functional_complete'}))}
  if(options.hardBlock){await delay(options.setup);if(options.block!=='setup')budget.ready(proof);Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0)}
  else {await Promise.race([run(),budget.failure]);budget.complete();if(options.blockCleanup)Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0);await delay(options.cleanup??0)}
}catch{process.exitCode=1}
finally{budget.close();process.kill(resource.pid,'SIGTERM');await new Promise(done=>resource.once('exit',done))}
