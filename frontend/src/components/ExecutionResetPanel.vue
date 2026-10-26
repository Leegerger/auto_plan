<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import type { useSharedPlan, BusinessOperation } from '../useSharedPlan'
import type { ExecutionCell, ResetSnapshot } from '../executionTypes'
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
const router=useRouter()
const head=computed(()=>props.shared.head.value)
const context=props.shared.context
const reset=computed(()=>props.shared.effective.value?.reset_preview)
const editable=computed(()=>props.shared.editor.value), readonly=computed(()=>props.shared.readonly.value)
const error=ref(''),lines=ref<{code:string;name:string}[]>([]),models=ref<{code:string;name:string}[]>([]),recipes=ref<{code:string;name:string}[]>([])
const today=computed(()=>head.value ? new Date(head.value.time.as_of).toISOString().slice(0,10) : '')
const week=computed(()=>head.value?.weeks[0]?.start_date ?? '')
const lastDay=computed(()=>head.value?.weeks[14]?.dates[6] ?? '')
function beijing(value:string){return new Date(Date.parse(value)+8*3600000).toISOString().slice(0,16)}
const factDate=ref(''),line=ref(''),model=ref(''),quantity=ref(''),reason=ref(''),cutoff=ref('')
const pendingFacts=ref<ExecutionCell[]>([]),useCoverage=ref(false),coverageStart=ref(''),coverageEnd=ref(''),coverageLines=ref<string[]>([]),coverageModels=ref<string[]>([])
const associationAmounts=ref<Record<string,string>>({})
const batch=ref(''),correction=ref(false),snapshotRows=ref<{model_code:string;wip:string;net_balance:string}[]>([])
const scopeStart=ref(''),scopeEnd=ref(''),scopeLines=ref<string[]>([]),scopeModels=ref<string[]>([]),decisions=ref<Record<number,string>>({})
const residualHours=ref(''),currentRecipe=ref(''),observedAt=ref('')
const external=computed(()=>{
  const marker=context.value?.lineage.external_reset as {production_week:string;already_applied:boolean}|undefined
  return marker?.already_applied && marker.production_week===week.value
})
const participants=computed(()=>(props.shared.effective.value?.reset_participants)?.[correction.value ? 'correction' : 'current'])
const source=computed(()=>participants.value?.source_adoption ?? 0)
const associationRows=computed(()=>{
  const facts=context.value?.execution.facts ?? []
  return (props.shared.rows.value ?? []).filter(row=>facts.some(fact=>fact.date===row.date && fact.line_code===row.line_code && fact.model_code===row.model_code))
})
function auditDetail(change:Record<string,unknown>){
  if(change.family==='fact'){
    const before=change.before as ExecutionCell|null, after=change.after as ExecutionCell
    return `${after.date} ${after.line_code} ${after.model_code} 累计投入由 ${before?.quantity ?? '未登记'} 改为 ${after.quantity} 片`
  }
  if(change.family==='associations'){
    const before=change.before as {segment_id:string;quantity:number}[],after=change.after as {segment_id:string;quantity:number}[]
    const old=new Map(before.map(row=>[row.segment_id,row.quantity])),next=new Map(after.map(row=>[row.segment_id,row.quantity]))
    return [...new Set([...old.keys(),...next.keys()])].filter(id=>old.get(id)!==next.get(id)).map(id=>`${id} 关联投入由 ${old.get(id) ?? 0} 改为 ${next.get(id) ?? 0} 片`).join('；')
  }
  if(change.family==='cutoff')return `执行截止由 ${change.before ?? '未确认'} 改为 ${change.after}`
  return '执行事实覆盖范围已更新'
}
const segments=computed(()=>props.shared.effective.value?.execution_segments ?? [])
const surplus=computed(()=>props.shared.effective.value?.execution_surplus ?? [])
const evaluation=props.shared.evaluation
watch(segments,value=>{for(const row of value)associationAmounts.value[row.segment_id]=String(row.executed)},{immediate:true})
watch(head,async value=>{
  if(!value)return
  factDate.value=today.value;cutoff.value=beijing(value.context.execution.cutoff ?? value.time.as_of);observedAt.value=beijing(value.time.as_of)
  coverageStart.value=week.value;coverageEnd.value=today.value;scopeStart.value=week.value;scopeEnd.value=lastDay.value
  const existing=value.context.reset
  const retained=props.shared.operations.value.slice().reverse().find(operation=>operation.kind==='weekly_reset') as ({snapshot?:ResetSnapshot;correction?:boolean;choices?:{revision:number;retain:boolean}[];scope?:{start_date:string;end_date:string;line_codes:string[];model_codes:string[]}}|undefined)
  const resetSnapshot=retained?.snapshot??(existing?.production_week===week.value?existing:undefined)
  batch.value=resetSnapshot?.batch_id??`week-${week.value}`
  correction.value=retained?.correction??false;decisions.value=Object.fromEntries((retained?.choices??[]).map(choice=>[choice.revision,choice.retain?'keep':'retract']));pendingFacts.value=[]
  snapshotRows.value=(participants.value?.model_codes ?? []).map(code=>{
    const old=resetSnapshot?.models.find(model=>model.model_code===code)
    return {model_code:code,wip:old ? String(old.wip) : '',net_balance:old ? String(old.net_balance) : ''}
  })
  if(retained?.scope){scopeStart.value=retained.scope.start_date;scopeEnd.value=retained.scope.end_date;scopeLines.value=[...retained.scope.line_codes];scopeModels.value=[...retained.scope.model_codes]}
  try{
    const input=await api<{masters:{lines:{code:string;name:string}[];models:{code:string;name:string}[];recipes:{code:string;name:string}[]}}>(`/inputs?version=${value.input_version}`)
    lines.value=input.masters.lines;models.value=input.masters.models;recipes.value=input.masters.recipes
    line.value ||= lines.value[0]?.code ?? '';model.value ||= models.value[0]?.code ?? '';currentRecipe.value ||= recipes.value[0]?.code ?? ''
    coverageLines.value=lines.value.map(row=>row.code);coverageModels.value=models.value.map(row=>row.code);scopeLines.value=[...coverageLines.value]
  }catch(e){error.value=e instanceof Error ? e.message : '执行资料读取失败'}
},{immediate:true})
watch(participants,value=>{
  snapshotRows.value=(value?.model_codes ?? []).map(code=>{
    const entered=snapshotRows.value.find(row=>row.model_code===code)
    const old=context.value?.reset?.production_week===week.value ? context.value.reset.models.find(row=>row.model_code===code) : undefined
    return entered ?? {model_code:code,wip:old ? String(old.wip) : '',net_balance:old ? String(old.net_balance) : ''}
  })
})
watch(reset,value=>{for(const row of value?.adjustments ?? [])if(decisions.value[row.revision]===undefined)decisions.value[row.revision]=row.decision===null ? '' : row.decision ? 'keep' : 'retract'},{immediate:true})
function integer(value:string,signed=false){
  if(!(signed ? /^-?\d+$/ : /^\d+$/).test(value.trim()) || !Number.isSafeInteger(Number(value)))throw new Error(signed ? '最终净余额必须填写完整整数，可正可负' : '数量必须填写完整非负整数，空白不表示零')
  return Number(value)
}
function timestamp(value:string){if(!value)throw new Error('请明确填写执行截止或观察时点');return new Date(value+'+08:00').toISOString()}
function addFact(){
  error.value=''
  try{
    if(!factDate.value||!line.value||!model.value)throw new Error('请选择事实生产日、线体和机种')
    const row={date:factDate.value,line_code:line.value,model_code:model.value,quantity:integer(quantity.value)}
    pendingFacts.value=pendingFacts.value.filter(old=>!(old.date===row.date&&old.line_code===row.line_code&&old.model_code===row.model_code));pendingFacts.value.push(row)
  }catch(e){error.value=(e as Error).message}
}
async function append(operation:BusinessOperation,amend=false){
  error.value=''
  if(amend){
    const old=props.shared.operations.value.slice().reverse().find(row=>row.kind===operation.kind)
    if(old){
      const index=props.shared.operations.value.indexOf(old)
      props.shared.operations.value[index]={...operation,action_id:old.action_id}
      if(!props.shared.selectedActions.value.includes(old.action_id!))props.shared.selectedActions.value.push(old.action_id!)
      props.shared.changed();await returnToWorkspace();return
    }
  }
  props.shared.appendOperation(operation);await returnToWorkspace()
}
async function returnToWorkspace(){
  await props.shared.check()
  if(!props.shared.error.value||props.shared.conflict.value)await router.push('/schedule')
}
async function facts(){
  error.value=''
  try{
    const old=context.value?.execution.facts ?? []
    if(pendingFacts.value.some(row=>old.some(fact=>fact.date===row.date&&fact.line_code===row.line_code&&fact.model_code===row.model_code&&fact.quantity!==row.quantity))&&!reason.value.trim())throw new Error('纠错必须填写原因')
    const operation:BusinessOperation={kind:'record_execution',rows:pendingFacts.value.map(row=>({...row})),cutoff:timestamp(cutoff.value),reason:reason.value || undefined}
    if(useCoverage.value){
      if(!coverageLines.value.length||!coverageModels.value.length)throw new Error('请选择已核实的覆盖线体与机种')
      operation.coverage=[{start_date:coverageStart.value,end_date:coverageEnd.value,line_codes:[...coverageLines.value],model_codes:[...coverageModels.value],blanks_are_zero:true}]
    }
    await append(operation);pendingFacts.value=[]
  }catch(e){error.value=(e as Error).message}
}
async function associate(){
  error.value=''
  try{
    if(!reason.value.trim())throw new Error('分段关联纠错必须填写原因')
    await append({kind:'record_execution',rows:[],reason:reason.value,associations:associationRows.value.map(row=>({segment_id:row.id,quantity:integer(associationAmounts.value[row.id] ?? '')}))})
  }catch(e){error.value=(e as Error).message}
}
async function confirmCapacity(){
  error.value=''
  try{
    if(!residualHours.value.trim())throw new Error('剩余可用工时必须明确填写，不能留空')
    await append({kind:'confirm_day_capacity',observed_at:timestamp(observedAt.value),confirmations:[{line_code:line.value,remaining_hours:residualHours.value,current_recipe:currentRecipe.value}]})
  }catch(e){error.value=(e as Error).message}
}
async function previewReset(){
  error.value=''
  try{
    if(!batch.value.trim()||!scopeLines.value.length)throw new Error('请填写重置批次并选择授权线体')
    const snapshot:ResetSnapshot={batch_id:batch.value,production_week:week.value,cutoff:week.value+'T08:00:00+08:00',models:snapshotRows.value.map(row=>({model_code:row.model_code,wip:integer(row.wip),net_balance:integer(row.net_balance,true)}))}
    const choices=Object.entries(decisions.value).filter(([,value])=>value==='keep'||value==='retract').map(([revision,value])=>({revision:Number(revision),retain:value==='keep'}))
    await append({kind:'weekly_reset',snapshot,source_adoption:source.value,correction:correction.value,choices,
      scope:{start_date:scopeStart.value,end_date:scopeEnd.value,line_codes:[...scopeLines.value],model_codes:[...scopeModels.value]}},true)
  }catch(e){error.value=(e as Error).message}
}
function number(value:number|null|undefined){return value===null||value===undefined ? '未确认' : value}
const names:Record<string,string>={patch_plan:'计划编辑',append_fill:'追加填量',change_quantity:'改量顺延',move_segment:'移动',split_segment:'拆分',copy_segments:'复制',swap_segments:'交换',replace_plan:'完整计划调整'}
</script>
<template>
  <section aria-label="执行事实与每周重置">
    <h2>执行事实与每周重置</h2>
    <p>累计事实按生产日、线体、机种替换，未提供的记录保留。超计划及计划外实际投入单独保留。周一起始在制品只计产出，最终净余额只调整来源周投入目标。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <template v-if="editable">
      <fieldset :disabled="readonly" class="input-grid">
        <legend>累计执行事实</legend>
        <label>事实生产日 <input type="date" v-model="factDate" :max="today" aria-label="事实生产日"/></label>
        <label>事实线体 <select v-model="line" aria-label="事实线体"><option v-for="row in lines" :key="row.code" :value="row.code">{{row.code}} {{row.name}}</option></select></label>
        <label>事实机种 <select v-model="model" aria-label="事实机种"><option v-for="row in models" :key="row.code" :value="row.code">{{row.code}} {{row.name}}</option></select></label>
        <label>累计实际投入 <input v-model="quantity" inputmode="numeric" aria-label="累计实际投入"/></label>
        <el-button @click="addFact">加入累计事实</el-button>
        <label>截止时点，北京时间 <input type="datetime-local" v-model="cutoff" aria-label="执行截止时点"/></label>
        <label class="wide">事实纠错原因 <input v-model="reason" maxlength="1000" aria-label="事实纠错原因"/></label>
      </fieldset>
      <ul aria-label="待登记累计事实"><li v-for="(row,index) in pendingFacts" :key="index">{{row.date}} / {{row.line_code}} / {{row.model_code}} / {{row.quantity}}片 <button :disabled="readonly" @click="pendingFacts.splice(index,1)">移除</button></li></ul>
      <details><summary>确认执行覆盖与空白含义</summary>
        <fieldset :disabled="readonly" class="input-grid">
          <label><input type="checkbox" v-model="useCoverage" aria-label="确认覆盖内空白为零"/> 已核实所选范围，空白明确表示零</label>
          <label>覆盖起日 <input type="date" v-model="coverageStart" aria-label="执行覆盖起日"/></label><label>覆盖终日 <input type="date" v-model="coverageEnd" :max="today" aria-label="执行覆盖终日"/></label>
          <label>已核实线体 <select multiple v-model="coverageLines" aria-label="执行覆盖线体"><option v-for="row in lines" :key="row.code" :value="row.code">{{row.code}}</option></select></label>
          <label>已核实机种 <select multiple v-model="coverageModels" aria-label="执行覆盖机种"><option v-for="row in models" :key="row.code" :value="row.code">{{row.code}}</option></select></label>
        </fieldset>
      </details>
      <el-button :disabled="readonly" @click="facts">预览执行事实</el-button>
    </template>
    <p>执行截止 {{context?.execution.cutoff ?? '尚未确认'}}；未声明的空白保持未知。</p>
    <el-table :data="context?.execution.facts ?? []" role="table" aria-label="当前累计执行事实" empty-text="尚未登记事实"><el-table-column prop="date" label="生产日"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="quantity" label="累计实际投入"/></el-table>
    <el-table :data="surplus" role="table" aria-label="超计划与计划外事实" empty-text="没有超计划或计划外实际投入"><el-table-column prop="date" label="生产日"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="quantity" label="未关联计划段的实际投入"/></el-table>
    <details><summary>原顺序执行分段与关联纠错</summary>
      <el-table :data="associationRows" role="table" aria-label="执行分段关联"><el-table-column prop="id" label="生产段"/><el-table-column prop="date" label="生产日"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="quantity" label="原段投入"/><el-table-column label="关联实际投入"><template #default="{row}"><input v-model="associationAmounts[row.id]" inputmode="numeric" :disabled="readonly" :aria-label="`${row.id} 关联实际投入`"/></template></el-table-column></el-table>
      <el-button v-if="editable" :disabled="readonly || !associationRows.length" @click="associate">预览关联纠错</el-button>
    </details>
    <details><summary>事实纠错与登记审计</summary><el-table :data="context?.execution.audit ?? []" role="table" aria-label="执行事实审计"><el-table-column prop="recorded_at" label="登记时点"/><el-table-column prop="actor" label="登记人"/><el-table-column prop="reason" label="纠错原因"/><el-table-column label="变更前后"><template #default="{row}"><div v-for="(change,index) in row.changes" :key="index">{{auditDetail(change)}}</div></template></el-table-column></el-table></details>
    <fieldset v-if="editable" :disabled="readonly" class="input-grid">
      <legend>当日剩余容量确认</legend>
      <p>只使用你确认的剩余可用工时与当前 Recipe，不按已过钟点推算。事实或参数变化后须重新确认。</p>
      <label>{{line}} 剩余可用工时 <input v-model="residualHours" inputmode="decimal" aria-label="当日剩余可用工时"/></label>
      <label>当前 Recipe <select v-model="currentRecipe" aria-label="确认当前 Recipe"><option v-for="row in recipes" :key="row.code" :value="row.code">{{row.code}}</option></select></label>
      <label>观察时点，北京时间 <input type="datetime-local" v-model="observedAt" aria-label="剩余容量观察时点"/></label>
      <el-button @click="confirmCapacity">预览当日容量确认</el-button>
    </fieldset>
    <h3>本周配对快照</h3>
    <p>截止 {{week}} 周一08:00，北京时间。每个参与机种的在制品与最终净余额同批必填，零也须明确填写。新快照完整替换旧快照，不叠加旧余额或未完成调整量。</p>
    <el-alert v-if="external" title="首次接入周已在外部完成重置，本周不能再次系统重置或更正。可明确手动调整草稿。" type="warning" :closable="false"/>
    <fieldset v-if="editable" :disabled="readonly || external" class="input-grid">
      <label>重置批次 <input v-model="batch" maxlength="64" aria-label="重置批次"/></label>
      <label><input type="checkbox" v-model="correction" aria-label="更正本周重置"/> 更正本周重置</label>
      <label>授权起日 <input type="date" v-model="scopeStart" :min="week" :max="lastDay" aria-label="重置授权起日"/></label><label>授权终日 <input type="date" v-model="scopeEnd" :min="week" :max="lastDay" aria-label="重置授权终日"/></label>
      <label>授权线体 <select multiple v-model="scopeLines" aria-label="重置授权线体"><option v-for="row in lines" :key="row.code" :value="row.code">{{row.code}}</option></select></label>
      <label>授权机种，空选表示全部 <select multiple v-model="scopeModels" aria-label="重置授权机种"><option v-for="row in models" :key="row.code" :value="row.code">{{row.code}}</option></select></label>
    </fieldset>
    <el-table :data="snapshotRows" role="table" aria-label="同批在制品与最终净余额"><el-table-column prop="model_code" label="机种"/><el-table-column label="周一起始在制品"><template #default="{row}"><input v-model="row.wip" inputmode="numeric" :disabled="readonly || external" :aria-label="`${row.model_code} 周一起始在制品`"/></template></el-table-column><el-table-column label="最终净余额，正超前、负落后"><template #default="{row}"><input v-model="row.net_balance" inputmode="numeric" :disabled="readonly || external" :aria-label="`${row.model_code} 最终净余额`"/></template></el-table-column></el-table>
    <el-button v-if="editable" :disabled="readonly || external || !head?.latest_adoption" @click="previewReset">预览本周重置</el-button>
    <template v-if="reset">
      <p>计算来源采用版 {{reset.source_adoption}} · 稳定性比较采用版 {{reset.comparison_adoption}} · 快照批次 {{reset.snapshot.batch_id}} 修订 {{reset.batch_revision}}</p>
      <p v-if="reset.effective_adoption">本周生效重置采用版 {{reset.effective_adoption}}。重复采用只定位生效记录，保留之后的计划调整。</p>
      <el-alert v-if="reset.pending_review" title="重置截止前事实已更正，本周快照待复核。请重新确认配对快照后更正重置。" type="warning" :closable="false"/>
      <p>未完成的重置调整量以片投入计算，来源为下表原归属周及重置快照，不计入补排合格产出目标或追加填量余额。</p>
      <el-table :data="reset.targets" role="table" aria-label="重置周目标与未完成调整"><el-table-column prop="model_code" label="机种"/><el-table-column prop="week" label="原归属周"/><el-table-column prop="source_input" label="来源投入"/><el-table-column prop="incoming_balance" label="本周净余额或自然结转"/><el-table-column prop="target" label="周目标，包含实际"/><el-table-column label="实际投入"><template #default="{row}">{{number(row.actual)}}</template></el-table-column><el-table-column label="剩余目标"><template #default="{row}">{{number(row.remaining_target)}}</template></el-table-column><el-table-column prop="planned_remaining" label="有效待执行"/><el-table-column prop="natural_carry" label="自然结转"/><el-table-column label="未完成应补"><template #default="{row}">{{number(row.unmet_increase)}}</template></el-table-column><el-table-column label="未完成应减"><template #default="{row}">{{number(row.unmet_reduction)}}</template></el-table-column><el-table-column label="实际超目标"><template #default="{row}">{{number(row.actual_over_target)}}</template></el-table-column></el-table>
      <el-table :data="reset.residual_carry" role="table" aria-label="来源外结转与未知基线"><el-table-column prop="model_code" label="机种"/><el-table-column prop="week" label="停止来源抵扣的周"/><el-table-column prop="quantity" label="剩余自然结转"/><el-table-column prop="state" label="原因与后续"/></el-table>
      <el-table :data="reset.expired" role="table" aria-label="过期未执行去向"><el-table-column prop="date" label="原生产日"/><el-table-column prop="model_code" label="机种"/><el-table-column label="过期未执行"><template #default="{row}">{{number(row.quantity)}}</template></el-table-column><el-table-column prop="destination_week" label="原归属周"/><el-table-column prop="destination" label="去向"/></el-table>
      <details v-if="reset.historical_inputs?.length"><summary>窗口之前的投入不重复复制</summary><p v-for="(row,index) in reset.historical_inputs" :key="index">{{row.date}} {{row.line_code}} {{row.model_code}} {{row.quantity}}片：{{row.destination}}</p></details>
      <p v-for="row in reset.crossing_switches ?? []" :key="row.id">{{row.id}}：{{row.state}}</p>
      <fieldset v-if="reset.adjustments.length" :disabled="readonly" aria-label="重置后逐项调整处理">
        <legend>首次重置后的完整调整</legend><p>每项必须明确保留或撤回。保留后的数量仍与重置目标对照，执行事实和当前锁定继续保护。</p>
        <label v-for="row in reset.adjustments" :key="row.revision">共享修订 {{row.revision}} · {{row.operations.map(operation=>names[operation.kind] ?? '完整调整').join('、')}} <select v-model="decisions[row.revision]" :aria-label="`修订 ${row.revision} 的重置更正选择`"><option value="">尚未处理</option><option value="keep">保留完整调整</option><option value="retract">撤回完整调整</option></select></label>
        <el-button @click="previewReset">按逐项选择重新预览重置</el-button>
      </fieldset>
      <p v-if="evaluation?.output_totals">当前需求影响：按时覆盖 {{evaluation.output_totals.on_time}}，未补足缺口 {{evaluation.output_totals.gap}}，待确认 {{evaluation.output_totals.pending}}，切机次数 {{evaluation.switch_count}}。本周目标不随需求变化重算。</p>
    </template>
  </section>
</template>
<style scoped>
.input-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:14px 0;padding:14px}.input-grid label{display:flex;flex-direction:column;gap:5px}.input-grid p,.wide{grid-column:1/-1}input,select{padding:6px;max-width:100%;box-sizing:border-box}fieldset>label{margin-right:14px}details{margin:14px 0}
</style>
