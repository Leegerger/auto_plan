import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gunzipSync, gzipSync } from 'node:zlib'
import { createHash } from 'node:crypto'
import { writeFullPrivateFixture,FULL_PRIVATE_ID } from './private-full-fixture.mjs'
const root=resolve(fileURLToPath(new URL('../../..',import.meta.url))),sha=bytes=>createHash('sha256').update(bytes).digest('hex')
test('full correctness successor keeps exact original source/reference/action/import and gates',()=>{
 const directory=mkdtempSync(resolve(tmpdir(),'auto-plan-full-private-'))
 try{
  const frozen=resolve(root,'docs/acceptance/frozen'),originalBytes=readFileSync(resolve(frozen,'manifest.json')),parent=JSON.parse(originalBytes),original=parent.scenarios.find(row=>row.id==='r20-loose')
  const {scenario,declaration,source}=writeFullPrivateFixture(root,directory),manifest=JSON.parse(readFileSync(resolve(directory,'manifest.json')))
  assert.equal(scenario.id,FULL_PRIVATE_ID);assert.notEqual(scenario.id,original.id)
  for(const name of ['source','reference','action','import']){assert.deepEqual(scenario[name],original[name]);assert.deepEqual(readFileSync(resolve(directory,scenario[name].path)),readFileSync(resolve(frozen,original[name].path)))}
  const business=structuredClone(scenario);for(const key of ['id','predecessor','editing_workload'])delete business[key]
  const expected=structuredClone(original);delete expected.id;assert.deepEqual(business,expected)
  assert.deepEqual({...manifest,scenarios:[]},{...parent,scenarios:[]});assert.deepEqual(readFileSync(resolve(directory,'predecessor-manifest.json')),originalBytes)
  assert.equal(declaration.source_manifest_sha256,sha(originalBytes));assert.equal(sha(readFileSync(resolve(directory,scenario.editing_workload.path))),scenario.editing_workload.sha256)
  assert.equal(source.plan.segments.length,4200);assert.equal(source.plan.switches.length,418)
  assert.deepEqual(declaration.targets.map(row=>row.editor),[0,1,2,3,4]);assert.ok(declaration.targets.every(row=>row.segment_id==='S-2027-01-24-M39'&&row.operation.quantity===99&&row.expected.hours_upper_inclusive==='18'))
  assert.throws(()=>writeFullPrivateFixture(root,directory),/new empty/)
 }finally{rmSync(directory,{recursive:true})}
})

for(const mutation of ['source bytes','manifest gates'])test('rejects changed '+mutation,()=>{
 const parent=mkdtempSync(resolve(tmpdir(),'auto-plan-private-negative-'))
 try{
  const frozen=resolve(root,'docs/acceptance/frozen'),fake=resolve(parent,'repo/docs/acceptance/frozen'),out=resolve(parent,'output')
  mkdirSync(fake,{recursive:true});mkdirSync(out)
  const raw=readFileSync(resolve(frozen,'manifest.json')),manifest=JSON.parse(raw),scenario=manifest.scenarios.find(row=>row.id==='r20-loose')
  writeFileSync(resolve(fake,'manifest.json'),raw)
  for(const key of ['source','reference','action','import'])writeFileSync(resolve(fake,scenario[key].path),readFileSync(resolve(frozen,scenario[key].path)))
  if(mutation==='manifest gates'){manifest.gates_seconds.single_cell=2;writeFileSync(resolve(fake,'manifest.json'),JSON.stringify(manifest))}
  else{const value=JSON.parse(gunzipSync(readFileSync(resolve(fake,scenario.source.path))));value.plan.segments[0].quantity++;writeFileSync(resolve(fake,scenario.source.path),gzipSync(JSON.stringify(value)))}
  assert.throws(()=>writeFullPrivateFixture(resolve(parent,'repo'),out))
 }finally{rmSync(parent,{recursive:true})}
})
