/** Own functional phases and child processes. Business clocks stay with callers. */
import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { existsSync, openSync, closeSync, readFileSync, writeFileSync, renameSync } from 'node:fs'
import { resolve } from 'node:path'
import { randomUUID, createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { performance } from 'node:perf_hooks'
import { setTimeout as delay } from 'node:timers/promises'

export const FUNCTIONAL_LIMITS=Object.freeze({setup_ms:180000,action_ms:150000,cleanup_ms:15000,absolute_ms:345000})
const epoch=()=>performance.timeOrigin+performance.now()
function identity(pid){try{const raw=readFileSync('/proc/'+pid+'/stat','utf8'),v=raw.slice(raw.lastIndexOf(')')+2).trim().split(/\s+/);return {pid:Number(pid),ppid:Number(v[1]),stat:v[0],start:v[19]}}catch(e){if(['ENOENT','ESRCH'].includes(e.code))return null;throw e}}
function tree(pid,start){
  if(!start||identity(pid)?.start!==start)return []
  const rows=execFileSync('ps',['-e','-o','pid=,ppid=,pgid=,stat='],{encoding:'utf8'}).trim().split('\n').map(r=>r.trim().split(/\s+/)),owned=new Set([String(pid)])
  let changed=true;while(changed){changed=false;for(const r of rows)if(owned.has(r[1])&&!owned.has(r[0])){owned.add(r[0]);changed=true}}
  return rows.filter(r=>owned.has(r[0])&&!r[3].startsWith('Z')).map(([p,parent,group])=>{const v=identity(p);return v&&v.ppid===Number(parent)?{...v,pgid:Number(group)}:null}).filter(Boolean)
}
const live=row=>{const current=identity(row.pid);return current?.start===row.start&&!current.stat.startsWith('Z')}
function signal(row,value){if(live(row))try{process.kill(row.pid,value)}catch(e){if(e.code!=='ESRCH')throw e}}
function readiness(proof){
  assert.deepEqual(proof?.commits?.map(r=>r.editor).sort(),[1,2,3,4],'Four actual editor commits are required.')
  assert.ok(proof.commits.every(r=>Number.isInteger(r.input_version)&&r.input_version>1))
  assert.equal(proof.evaluation?.status,200)
  assert.match(proof.evaluation?.artifact?.sha256??'',/^[a-f0-9]{64}$/)
}
export function resourceRegistration(pid){
  const owner=identity(process.pid),child=identity(pid)
  assert.ok(child&&owner&&child.ppid===owner.pid,'Resource must be an actual direct worker child.')
  assert.equal(identity(child.ppid)?.start,owner.start,'Resource parent identity changed.')
  return {pid:child.pid,start:child.start,parent_pid:owner.pid,parent_start:owner.start}
}
export function functionalBudget({output,attempt=process.env.AUTO_PLAN_FUNCTIONAL_ATTEMPT,limits=FUNCTIONAL_LIMITS,onTimeout=()=>{}}){
  assert.ok(attempt)
  const started=epoch(),owner=identity(process.pid);let timer,readyAt,readyProof,completed=false,closed=false
  const file=resolve(output,'functional-phase.json')
  const publish=value=>{const path=file+'.tmp';writeFileSync(path,JSON.stringify({attempt,pid:process.pid,start:owner.start,started_epoch_ms:started,...value}));renameSync(path,file)}
  publish({sequence:0,phase:'setup'})
  let rejectFailure
  const failure=new Promise((_,reject)=>{rejectFailure=reject});failure.catch(()=>{})
  const expire=phase=>{if(closed)return;const error=new Error('Functional '+phase+' deadline exceeded.');onTimeout(phase);rejectFailure(error)}
  timer=setTimeout(()=>expire('setup'),limits.setup_ms)
  return {failure,ready(proof){
    assert.ok(readyAt===undefined&&!closed,'Repeated readiness transition.');readiness(proof)
    const at=epoch();assert.ok(at-started<=limits.setup_ms,'Setup budget exhausted before readiness.')
    readyAt=at;readyProof=structuredClone(proof);clearTimeout(timer)
    publish({sequence:1,phase:'ready',ready_epoch_ms:readyAt,proof:readyProof})
    timer=setTimeout(()=>expire('action'),limits.action_ms)
  },complete(){
    assert.ok(readyAt!==undefined&&!completed&&!closed,'Workflow settlement requires active readiness.')
    const at=epoch();assert.ok(at<=readyAt+limits.action_ms,'Workflow settled after action deadline.')
    completed=true;clearTimeout(timer)
    publish({sequence:2,phase:'cleanup',ready_epoch_ms:readyAt,proof:readyProof,settled_epoch_ms:at})
  },close(){closed=true;clearTimeout(timer)}}
}

export async function guardFunctionalWorker({script,output,cwd,env=process.env,limits=FUNCTIONAL_LIMITS,args=[],poll_ms=50,sourceFiles=[]}){
  const bound=[...new Set([script,fileURLToPath(import.meta.url),...sourceFiles])],hashes=()=>Object.fromEntries(bound.map(path=>[path,createHash('sha256').update(readFileSync(path)).digest('hex')])),sourceBefore=hashes()
  const attempt=randomUUID(),log=openSync(resolve(output,'worker.log'),'w'),started=epoch()
  const child=spawn(process.execPath,[script,output,...args],{cwd,env:{...env,AUTO_PLAN_FUNCTIONAL_ATTEMPT:attempt},detached:true,stdio:['ignore',log,log]})
  let exited=false,launchError,workerStart,readyEpoch,readyProof,settledEpoch,phase='setup',lastRaw,timedOut=false,interruption,cleanupDeadline,invalidOwned=false
  const finished=new Promise(done=>{child.once('exit',(code,signal)=>{exited=true;done({code,signal,ended_epoch_ms:epoch()})});child.once('error',e=>{exited=true;launchError=e;done({code:null,signal:null})})})
  const registered=epoch()+1000
  while(!exited&&epoch()<registered){const row=identity(child.pid);if(row?.ppid===process.pid){workerStart=row.start;break}await delay(5)}
  const known=new Map(),ownedFile=resolve(output,'owned-processes.json'),phaseFile=resolve(output,'functional-phase.json')
  const remember=()=>{for(const row of tree(child.pid,workerStart))known.set(row.pid,row);if(existsSync(ownedFile)){let owned;try{owned=JSON.parse(readFileSync(ownedFile));assert.ok(owned&&typeof owned==='object');for(const kind of ['server','browser'])if(owned[kind+'_pid'])assert.ok(Number.isInteger(owned[kind+'_pid'])&&owned[kind+'_pid']>0)}catch{invalidOwned=true;return}for(const kind of ['server','browser'])if(owned[kind+'_pid']){const row=identity(owned[kind+'_pid']),witness=owned.attempt===attempt&&owned.worker_pid===child.pid&&owned.worker_start===workerStart&&Array.isArray(owned.registrations)&&owned.registrations.some(r=>r&&r.pid===row?.pid&&r.start===row?.start&&r.parent_pid===child.pid&&r.parent_start===workerStart);if(row&&row.start===owned[kind+'_start']&&(row.ppid===child.pid||known.get(row.pid)?.start===row.start||witness))for(const member of tree(row.pid,row.start))known.set(member.pid,member)}}}
  const observePhase=()=>{
    if(!existsSync(phaseFile)){assert.equal(lastRaw,undefined,'Owned phase artifact disappeared.');return}
    const raw=readFileSync(phaseFile,'utf8');if(raw===lastRaw)return
    const value=JSON.parse(raw);assert.equal(value.attempt,attempt);assert.equal(value.pid,child.pid);assert.equal(value.start,workerStart)
    assert.ok([0,1,2].includes(value.sequence),'Unknown phase sequence.')
    if(value.sequence===0){assert.equal(value.phase,'setup');assert.equal(lastRaw,undefined,'Repeated setup phase.');assert.equal(readyEpoch,undefined,'Phase regression.')}else{
      readiness(value.proof)
      assert.ok(Number.isFinite(value.ready_epoch_ms)&&value.ready_epoch_ms>=started&&value.ready_epoch_ms<=epoch()&&value.ready_epoch_ms<=started+limits.setup_ms,'Invalid readiness time or exhausted setup.')
      if(readyEpoch!==undefined){assert.equal(value.ready_epoch_ms,readyEpoch,'Readiness time changed.');assert.equal(JSON.stringify(value.proof),readyProof,'Readiness proof changed.')}
      if(value.sequence===1){assert.equal(value.phase,'ready');assert.equal(readyEpoch,undefined,'Repeated readiness phase.')}else{
        assert.equal(value.phase,'cleanup');assert.equal(settledEpoch,undefined,'Repeated cleanup phase.')
        assert.ok(Number.isFinite(value.settled_epoch_ms)&&value.settled_epoch_ms>=value.ready_epoch_ms&&value.settled_epoch_ms<=epoch()&&value.settled_epoch_ms<=value.ready_epoch_ms+limits.action_ms&&value.settled_epoch_ms<=started+limits.absolute_ms-limits.cleanup_ms,'Invalid workflow settlement time or exhausted action.')
        settledEpoch=value.settled_epoch_ms
      }
      readyEpoch=value.ready_epoch_ms;readyProof=JSON.stringify(value.proof);phase=value.sequence===2?'cleanup':'action'
    }
    lastRaw=raw
  }
  try{
    assert.ok(exited||workerStart,'Could not establish worker identity.')
    while(!exited){
      remember()
      assert.equal(invalidOwned,false,'Malformed owned process artifact.')
      observePhase()
      // Readiness carries its actual monotonic epoch. Late polling cannot
      // restart the action timer or extend the absolute cleanup reservation.
      const deadline=settledEpoch===undefined?Math.min(readyEpoch===undefined?started+limits.setup_ms:readyEpoch+limits.action_ms,started+limits.absolute_ms-limits.cleanup_ms):Math.min(settledEpoch+limits.cleanup_ms*2/3,started+limits.absolute_ms-limits.cleanup_ms/3)
      if(epoch()>=deadline){timedOut=true;cleanupDeadline=Math.min(settledEpoch===undefined?epoch()+limits.cleanup_ms:settledEpoch+limits.cleanup_ms,started+limits.absolute_ms);if(phase!=='cleanup')await Promise.race([finished,delay(Math.min(3000,limits.cleanup_ms*0.3))]);throw new Error('Functional '+phase+' guardian deadline exceeded.')}
      await delay(poll_ms)
    }
    const result=await finished;if(launchError)throw launchError;remember();assert.equal(invalidOwned,false,'Malformed owned process artifact.');observePhase();if(result.code===0){assert.ok(readyEpoch!==undefined,'Successful worker omitted owned readiness.');assert.ok(settledEpoch!==undefined,'Successful worker omitted workflow settlement.');assert.ok(result.ended_epoch_ms<=Math.min(settledEpoch+limits.cleanup_ms,started+limits.absolute_ms),'Functional cleanup guardian deadline exceeded before successful exit.')}return result
  }catch(error){interruption=error.message;throw error}finally{
    cleanupDeadline??=Math.min(settledEpoch===undefined?epoch()+limits.cleanup_ms:settledEpoch+limits.cleanup_ms,started+limits.absolute_ms)
    remember();for(const row of known.values())signal(row,'SIGTERM')
    const grace=Math.min(cleanupDeadline,epoch()+limits.cleanup_ms/5)
    while(epoch()<grace&&[...known.values()].some(live))await delay(10)
    const forced=[...known.values()].filter(live);for(const row of forced)signal(row,'SIGKILL')
    const end=cleanupDeadline
    while(epoch()<end&&(!exited||[...known.values()].some(live)))await delay(10)
    const remaining=[...known.values()].filter(live)
    if(interruption)writeFileSync(resolve(output,'controller-interruption.json'),JSON.stringify({status:'fail',phase,reason:interruption,source_closure:'unconfirmed',input_acknowledgments:'unconfirmed',restoration:'unconfirmed'}))
    const sourceAfter=hashes()
    writeFileSync(resolve(output,'controller-cleanup.json'),JSON.stringify({worker_pid:child.pid,worker_start:workerStart,attempt,timed_out:timedOut,worker_exited:exited,recorded_owned:[...known.values()],force_killed:forced,remaining_live:remaining,phase,ready_epoch_ms:readyEpoch,settled_epoch_ms:settledEpoch,cleanup_deadline_epoch_ms:cleanupDeadline,cooperative_cleanup_ms:limits.cleanup_ms*2/3,guardian_cleanup_reserve_ms:limits.cleanup_ms/3,source_files_before:sourceBefore,source_files_after:sourceAfter}))
    assert.deepEqual(sourceAfter,sourceBefore,'Invoked functional source changed during the run.')
    closeSync(log);assert.ok(exited,'Owned worker was not reaped.');assert.deepEqual(remaining,[],'Owned descendants remain alive.')
  }
}
