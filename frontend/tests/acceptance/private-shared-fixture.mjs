/** New full shared-save successor; canonical and qualified no-job inputs stay exact. */
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import { writeFullPrivateFixture } from './private-full-fixture.mjs'
import { FULL_SHARED_ID, FULL_SHARED_CONTRACT } from './private-shared-workload.mjs'
const sha=bytes=>createHash('sha256').update(bytes).digest('hex')
export function writeFullSharedFixture(root,directory) {
  const parent=writeFullPrivateFixture(root,directory), parentBytes=readFileSync(resolve(directory,'manifest.json'))
  assert.equal(sha(parentBytes),'67b8b5793460d682da724850ae992c56d1b667898163a661ae173dac7f9e8657')
  assert.equal(sha(readFileSync(resolve(directory,'editing-workload.json'))),'ab571ec54eac0aff4c4d32d0992b0d7fb7afe99cd38b421ad80f52b33380e4e8')
  writeFileSync(resolve(directory,'predecessor-full-private-manifest.json'),parentBytes)
  const manifest=JSON.parse(parentBytes),scenario={...parent.scenario,id:FULL_SHARED_ID,predecessor:{scenario:parent.scenario.id,manifest:{path:'predecessor-full-private-manifest.json',sha256:sha(parentBytes)}}}
  const declaration={...parent.declaration,scenario:FULL_SHARED_ID,source_manifest_sha256:sha(parentBytes),shared_save:structuredClone(FULL_SHARED_CONTRACT),
    targets:parent.declaration.targets.map((target,editor)=>{
      const model=editor===0?'M00':'M39',line=editor===0?'L00':'L01'
      const effects=[['CAP',line,'2027-01-24'],...parent.source.inputs.masters.lines.map(row=>['CELL','2027-01-24',row.code,model]),...parent.source.inputs.business.demands.filter(row=>row.model_code===model).map(row=>['REQ',model,row.week])]
      return {...target,segment_id:'S-2027-01-24-'+model,restoration:editor===0?'ordinary_uuid_full_original_plan':'undo_complete_action_to_actual_shared_epoch',expected:{...target.expected,effects,changed_week:{...target.expected.changed_week,model_code:model}}}
    })}
  const bytes=Buffer.from(JSON.stringify(declaration));writeFileSync(resolve(directory,'editing-workload-shared.json'),bytes)
  scenario.editing_workload={path:'editing-workload-shared.json',encoding:'json',sha256:sha(bytes)}
  writeFileSync(resolve(directory,'manifest.json'),JSON.stringify({...manifest,scenarios:[scenario]}))
  return {scenario,declaration,source:parent.source}
}
