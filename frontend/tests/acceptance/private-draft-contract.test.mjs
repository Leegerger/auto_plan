import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { assertPrivateCycleBusiness, privateExpectation } from './private-draft-contract.mjs'
import { writeFullPrivateFixture } from './private-full-fixture.mjs'
const root=resolve(fileURLToPath(new URL('../../..',import.meta.url)))
function sample(small=false) {
  const directory=mkdtempSync(resolve(tmpdir(),'auto-plan-private-contract-'))
  let loaded
  try { loaded=writeFullPrivateFixture(root,directory) } finally { rmSync(directory,{recursive:true}) }
  const source=structuredClone(loaded.source)
  if(small){
    const models=['M00','M02','M04','M06','M08'],lines=['L00','L01','L02','L03','L04'],recipes=['R00','R01','R02','R03','R04']
    source.inputs.masters.models=source.inputs.masters.models.filter(row=>models.includes(row.code))
    source.inputs.masters.lines=source.inputs.masters.lines.filter(row=>lines.includes(row.code))
    source.inputs.business.demands=source.inputs.business.demands.filter(row=>models.includes(row.model_code))
    source.inputs.masters.recipes=source.inputs.masters.recipes.filter(row=>recipes.includes(row.code))
    source.inputs.masters.capabilities=source.inputs.masters.capabilities.filter(row=>recipes.includes(row.recipe_code)&&lines.includes(row.line_code))
    const parameters=source.inputs.parameters
    parameters.model_yields=parameters.model_yields.filter(row=>models.includes(row.model_code))
    parameters.capacities=parameters.capacities.filter(row=>recipes.includes(row.recipe_code)&&lines.includes(row.line_code))
    parameters.cycle_times=parameters.cycle_times.filter(row=>models.includes(row.model_code))
    parameters.switch_durations=parameters.switch_durations.filter(row=>recipes.includes(row.from_recipe)&&recipes.includes(row.to_recipe))
    source.plan.segments=source.plan.segments.filter(row=>models.includes(row.model_code));source.plan.switches=[]
    source.plan.initial_states=lines.map((line_code,index)=>({line_code,recipe_code:recipes[index]}))
    source.baseline.plan=structuredClone(source.plan)
    for(const scope of [...source.execution.coverage,...source.baseline.coverage]){scope.line_codes=[...lines];scope.model_codes=[...models]}
  }
  const target=structuredClone(loaded.declaration.targets[0])
  if(small){delete target.expected.weekly_count; delete target.expected.objective; delete target.expected.effects; target.segment_id='S-2027-01-24-M00';target.expected.hours_upper_inclusive='24';target.expected.changed_week.model_code='M00';target.expected.totals.expected=target.expected.totals.on_time=52499}
  const count=small?52500:420000,switches=small?0:418
  const reference={totals:{expected:count,on_time:count,late:0,gap:0,pending:0,unallocated:0,outside_window:0},objective:Array(30).fill('0').concat([String(switches),'0','0'])}
  const plan=structuredClone(source.plan);plan.segments.find(row=>row.id===target.segment_id).quantity=99
  const evaluation=changed=>({capacity_valid:true,computation_complete:true,executable:true,
    output_totals:changed?structuredClone(target.expected.totals):structuredClone(reference.totals),switch_count:switches,
    stability:{weighted_cost:changed?1:0,order_change_count:0},
    weekly_results:source.inputs.business.demands.map(row=>{const affected=changed&&row.model_code===target.expected.changed_week.model_code&&row.week==='2027-01-18';return {model_code:row.model_code,week:row.week,demand:700,on_time:affected?699:700,gap:affected?1:0,pending:0,late:0}})})
  const expectation=privateExpectation(source,target,reference)
  return {source,baseline:structuredClone(source),target,expectation,
    changed:{plan,snapshot:{...structuredClone(source),plan},effects:structuredClone(expectation.effects),evaluation:evaluation(true)},
    undo:{plan:structuredClone(source.plan),snapshot:structuredClone(source),effects:[],evaluation:evaluation(false)}}
}
for(const small of [true,false])test(small?'small75-row business contract remains supported':'full600-row business contract preserves the canonical expectation',()=>{
  const value=sample(small);assert.equal(value.expectation.weekly_count,small?75:600);assert.equal(value.changed.effects.length,small?21:34)
  assertPrivateCycleBusiness(value)
})
const mutations={
  'outside production':value=>{value.changed.plan.segments[0].quantity++},
  'selected event slot':value=>{value.changed.plan.segments.find(row=>row.id===value.target.segment_id).order++},
  'switch identity':value=>{value.changed.plan.switches[0].id+='changed'},
  'switch allocation':value=>{value.changed.plan.switches[0].allocations[0].hours='2'},
  'strict lower hour boundary':value=>{value.changed.plan.segments.find(row=>row.id===value.target.segment_id).hours='49/50'},
  'upper hour boundary':value=>{value.changed.plan.segments.find(row=>row.id===value.target.segment_id).hours='1801/100'},
  'objective coordinate':value=>{value.changed.evaluation.stability.weighted_cost=2},
  'weekly identity':value=>{value.changed.evaluation.weekly_results[0].week='2028-01-01'},
  'weekly value':value=>{value.changed.evaluation.weekly_results[0].on_time=699},
  'complete input source':value=>{value.changed.snapshot.inputs.masters.models[0].name+='changed'},
  'missing dependency':value=>{value.changed.effects.pop()},
  'extra dependency':value=>{value.changed.effects.push(['CAP','L00','2027-01-24'])},
  'undo source':value=>{value.undo.snapshot.inputs.masters.models[0].name+='changed'},
  'undo objective':value=>{value.undo.evaluation.switch_count=417}
}
for(const [name,mutate]of Object.entries(mutations))test('rejects '+name,()=>{const value=sample();mutate(value);assert.throws(()=>assertPrivateCycleBusiness(value))})
