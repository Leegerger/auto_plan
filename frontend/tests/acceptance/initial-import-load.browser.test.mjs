/** Own the opt-in functional probe and its independent process deadline. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, openSync, readFileSync, writeFileSync, closeSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tmpdir } from 'node:os'
import { setTimeout as delay } from 'node:timers/promises'

const directory=dirname(fileURLToPath(import.meta.url)),root=resolve(directory,'../../..')
const enabled=process.env.AUTO_PLAN_INITIAL_IMPORT_LOAD_BROWSER==='1'
function identity(pid){
  try{const raw=readFileSync('/proc/'+pid+'/stat','utf8'),fields=raw.slice(raw.lastIndexOf(')')+2).trim().split(/\s+/);return {pid:Number(pid),ppid:Number(fields[1]),stat:fields[0],start:fields[19]}}
  catch(error){if(['ENOENT','ESRCH'].includes(error.code))return null;throw error}
}
function tree(parent,expectedStart){
  if(!expectedStart||identity(parent)?.start!==expectedStart)return []
  const rows=execFileSync('ps',['-e','-o','pid=,ppid=,pgid=,stat='],{encoding:'utf8'}).trim().split('\n').map(row=>row.trim().split(/\s+/))
  const owned=new Set([String(parent)]);let changed=true
  while(changed){changed=false;for(const row of rows)if(owned.has(row[1])&&!owned.has(row[0])){owned.add(row[0]);changed=true}}
  return rows.filter(row=>owned.has(row[0])&&!row[3].startsWith('Z')).map(([pid,ppid,pgid])=>{
    const value=identity(pid);return value&&value.ppid===Number(ppid)?{...value,pgid:Number(pgid)}:null
  }).filter(Boolean)
}
function live(row){const current=identity(row.pid);return current?.start===row.start&&!current.stat.startsWith('Z')}
function signal(row,value){if(live(row))try{process.kill(row.pid,value)}catch(error){if(error.code!=='ESRCH')throw error}}

test('first import uses actual stale upload/retry with four real input editors and complete transient computation',
  {skip:!enabled&&'Set AUTO_PLAN_INITIAL_IMPORT_LOAD_BROWSER=1.',timeout:210000},async()=>{
    const output=mkdtempSync(resolve(tmpdir(),'auto-plan-initial-import-browser-'))
    process.stdout.write('Evidence '+output+'\n')
    const log=openSync(resolve(output,'worker.log'),'w')
    const child=spawn(process.execPath,[resolve(directory,'initial-import-browser-worker.mjs'),output],{cwd:root,detached:true,stdio:['ignore',log,log],env:process.env})
    let exited=false,launchError
    const finished=new Promise(done=>{child.once('exit',(code,signal)=>{exited=true;done([code,signal])});child.once('error',error=>{exited=true;launchError=error;done([null,null])})})
    let workerStart
    const registeredUntil=Date.now()+1000
    while(!exited&&Date.now()<registeredUntil){const value=identity(child.pid);if(value?.ppid===process.pid){workerStart=value.start;break}await delay(10)}
    const known=new Map(),ownedFile=resolve(output,'owned-processes.json'),deadline=Date.now()+180000
    let timedOut=false
    try{
      assert.ok(exited||workerStart,'Could not establish owned worker process identity.')
      while(!exited&&Date.now()<deadline){
        for(const row of tree(child.pid,workerStart))known.set(row.pid,row)
        if(existsSync(ownedFile)){
          const owned=JSON.parse(readFileSync(ownedFile));for(const kind of ['server','browser'])if(owned[kind+'_pid'])for(const row of tree(owned[kind+'_pid'],owned[kind+'_start']))known.set(row.pid,row)
        }
        await delay(500)
      }
      if(!exited){timedOut=true;throw new Error('The sole browser attempt exceeded180s; preserved started operations and independent owned-process deadline.')}
      const [code,signalName]=await finished
      if(launchError)throw launchError
      assert.equal(signalName,null);assert.equal(code,0,'Actual functional workflow failed; evidence '+output)
      const report=JSON.parse(readFileSync(resolve(output,'report.json')))
      assert.equal(report.status,'functional_pass');assert.equal(report.acceptance,'not_run');assert.deepEqual(report.repeats,[])
    }finally{
      // Independent of a blocked worker loop or its synchronous checker.
      for(const row of tree(child.pid,workerStart))known.set(row.pid,row)
      if(existsSync(ownedFile)){
        const owned=JSON.parse(readFileSync(ownedFile));for(const kind of ['server','browser'])if(owned[kind+'_pid'])for(const row of tree(owned[kind+'_pid'],owned[kind+'_start']))known.set(row.pid,row)
      }
      for(const row of known.values())signal(row,'SIGTERM')
      const graceful=Date.now()+3000
      while(Date.now()<graceful&&[...known.values()].some(live))await delay(100)
      const alive=[...known.values()].filter(live)
      for(const row of alive)signal(row,'SIGKILL')
      const forced=Date.now()+2000
      while(Date.now()<forced&&(!exited||[...known.values()].some(live)))await delay(100)
      const remaining=[...known.values()].filter(live)
      if(timedOut)writeFileSync(resolve(output,'controller-interruption.json'),JSON.stringify({status:'fail',source_closure:'unconfirmed',input_acknowledgments:'unconfirmed',restoration:'unconfirmed',reason:'Independent overall deadline interrupted the owned workflow; process cleanup proves only resource cleanup.'}))
      writeFileSync(resolve(output,'controller-cleanup.json'),JSON.stringify({worker_pid:child.pid,timed_out:timedOut,worker_exited:exited,recorded_owned:[...known.values()],force_killed:alive,remaining_live:remaining}))
      closeSync(log)
      assert.equal(exited,true,'Owned worker was not reaped within the independent cleanup interval.')
      assert.deepEqual(remaining,[],'Recorded owned app/browser/checker descendants remain alive.')
    }
  })
