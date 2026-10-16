import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync,writeFileSync,appendFileSync,rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { readUploadObservation } from './upload-observations.mjs'

test('a received response waits for its complete matching server ledger line',async()=>{
  const root=mkdtempSync(join(tmpdir(),'auto-plan-upload-ledger-')),path=join(root,'received.ndjson')
  const raw='{"id":"actual-response-7","complete":true}'
  const written=new Promise(done=>setImmediate(()=>{writeFileSync(path,raw.slice(0,12));setTimeout(()=>{appendFileSync(path,raw.slice(12)+'\n');done()},20)}))
  try{const result=await readUploadObservation(path,'actual-response-7',{timeout_ms:200,interval_ms:5});assert.deepEqual(result,{observation:{id:'actual-response-7',complete:true},raw})}
  finally{await written;rmSync(root,{recursive:true})}
})


test('missing, incomplete, mismatched and repeated ledger records cannot attest receipt',async()=>{
  const root=mkdtempSync(join(tmpdir(),'auto-plan-upload-ledger-')),path=join(root,'received.ndjson')
  try{
    await assert.rejects(readUploadObservation(path,'wanted',{timeout_ms:10,interval_ms:2}),/unconfirmed/)
    writeFileSync(path,'{"id":"wanted"}')
    await assert.rejects(readUploadObservation(path,'wanted',{timeout_ms:10,interval_ms:2}),/unconfirmed/)
    writeFileSync(path,'{"id":"other"}\n')
    await assert.rejects(readUploadObservation(path,'wanted',{timeout_ms:10,interval_ms:2}),/unconfirmed/)
    writeFileSync(path,'{"id":"wanted"}\n{"id":"wanted"}\n')
    await assert.rejects(readUploadObservation(path,'wanted',{timeout_ms:10}),/Repeated/)
  }finally{rmSync(root,{recursive:true})}
})
