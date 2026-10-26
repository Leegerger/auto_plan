<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import ResidualSwitchEditor from './ResidualSwitchEditor.vue'
import type { LineStateRecord } from '../lineStateTypes'
import type { useSharedPlan } from '../useSharedPlan'
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
const form=props.shared.workspace.lineStates
const options=ref<{lines:{code:string;name:string}[];recipes:{code:string;name:string}[]}>({lines:[],recipes:[]})
const error=ref('')
const records=computed(()=>props.shared.context.value?.line_states?.records ?? [])
const arrangedRecord=computed(()=>records.value.find(row=>row.id===props.shared.workspace.residualSwitch.recordId))
const audit=computed(()=>props.shared.context.value?.line_states?.audit ?? [])
function beijing(value:string){return new Date(Date.parse(value)+8*3600000).toISOString().slice(0,19)}
function timestamp(value:string){return `${value.length===16 ? value+':00' : value}+08:00`}
function effective(){return timestamp(form.effectiveAt)}
function existing(line:string){
  const retained=form.expected[line]
  if(retained && Date.parse(retained.effective_at)===Date.parse(effective()))return retained
  return records.value.find(row=>row.line_code===line && Date.parse(row.effective_at)===Date.parse(effective()))
}
function remember(line:string){const row=existing(line);if(row)form.expected[line]={...row}}
function applyRecipe(){for(const code of form.selectedLines)form.recipes[code]=form.batchRecipe}
function unfinished(code:string){return form.unfinished[code] ??= {jobId:'',startedAt:'',fromRecipe:'',toRecipe:'',remainingHours:'',standardHours:'',completedHours:''}}
function statePayload(code:string){
  if(form.state==='retained_recipe')return {state:'retained_recipe',recipe_code:form.recipes[code]}
  const value=unfinished(code)
  return {state:'unfinished_switch',recipe_code:null,unfinished_switch:{job_id:value.jobId.trim(),started_at:timestamp(value.startedAt),from_recipe:value.fromRecipe,to_recipe:value.toRecipe,remaining_hours:value.remainingHours.trim(),...(value.standardHours.trim() ? {standard_hours:value.standardHours.trim()} : {}),...(value.completedHours.trim() ? {completed_hours:value.completedHours.trim()} : {})}}
}
function changedState(code:string,old:LineStateRecord){
  if((old.state ?? 'retained_recipe')!==form.state || old.source!==form.source.trim())return true
  if(form.state==='retained_recipe')return old.recipe_code!==form.recipes[code]
  const next=statePayload(code).unfinished_switch,previous=old.unfinished_switch
  return !previous || !next || Object.entries(next).some(([key,value])=>value!==previous[key as keyof typeof previous]) || (!!previous.completed_hours && !next.completed_hours) || (!!previous.standard_hours && !next.standard_hours)
}
function inspectable(row:LineStateRecord){return props.shared.matrixView.value?.switch_continuity?.some(value=>value.line_code===row.line_code && value.source_record_id===row.id) ?? false}
function arrange(row:LineStateRecord){
  const job=row.unfinished_switch
  if(!job)return
  const form=props.shared.workspace.residualSwitch
  form.recordId=row.id
  const existing=props.shared.plan.value?.switches.find(value=>value.id===job.job_id)
  form.allocations=existing ? existing.allocations.map(value=>({...value})) : [{date:'',order:0,hours:job.remaining_hours,idle_hours:'0'}]
}
function correct(row:LineStateRecord){
  form.selectedLines=[row.line_code];form.effectiveAt=beijing(row.effective_at);form.source=row.source
  form.state=row.state ?? 'retained_recipe';form.recipes[row.line_code]=row.recipe_code ?? ''
  if(row.unfinished_switch){const value=row.unfinished_switch;form.unfinished[row.line_code]={jobId:value.job_id,startedAt:beijing(value.started_at),fromRecipe:value.from_recipe,toRecipe:value.to_recipe,remainingHours:value.remaining_hours,standardHours:value.standard_hours ?? '',completedHours:value.completed_hours ?? ''}}
  form.expected[row.line_code]={...row};form.reason='';error.value=''
}
async function stage(){
  error.value=''
  if(!form.selectedLines.length){error.value='请选择需要确认的线体';return}
  if(!form.effectiveAt || !form.source.trim()){error.value='请填写状态对应时点与现场来源';return}
  if(form.state==='unfinished_switch' && form.selectedLines.some(code=>{const value=unfinished(code);return !value.jobId.trim() || !value.startedAt || !value.fromRecipe || !value.toRecipe || !value.remainingHours.trim()})){error.value='请填写原作业、起止 Recipe、实际开始时点和现场剩余工时';return}
  if(form.state==='retained_recipe' && form.selectedLines.some(code=>!form.recipes[code])){error.value='请为所选线体填写 Recipe';return}
  const changed=form.selectedLines.some(code=>{const old=existing(code);return old && changedState(code,old)})
  if(changed && !form.reason.trim()){error.value='更正确认状态必须填写原因';return}
  props.shared.appendOperation({kind:'confirm_line_states',reason:form.reason.trim() || null,rows:form.selectedLines.map(code=>{
    const old=existing(code)
    return {line_code:code,...statePayload(code),effective_at:effective(),source:form.source.trim(),...(old ? {expected_record_id:old.id} : {})}
  })})
  await props.shared.check()
}
onMounted(async()=>{
  form.effectiveAt ||= `${props.shared.head.value?.time.window_start ?? ''}T08:00:00`
  try{options.value=await api('/inputs/options');for(const row of options.value.lines)form.recipes[row.code] ??=''}
  catch(e){error.value=e instanceof Error ? e.message : '无法读取线体和 Recipe'}
})
</script>

<template>
  <section aria-label="现场线体状态">
    <p>状态对应时点与录入时间分别保留。确认先进入本机动作，预览后共享保存或正式采用。</p>
    <div class="input-table-scroll"><table class="input-table" aria-label="现场线体状态记录"><thead><tr><th>线体</th><th>已确认状态与来源</th><th v-if="shared.editor.value">更正</th></tr></thead><tbody><tr v-for="row in records" :key="row.id"><td>{{row.line_code}}</td><td><template v-if="row.state==='unfinished_switch' && row.unfinished_switch"><strong>未完成切机 {{row.unfinished_switch.from_recipe}} → {{row.unfinished_switch.to_recipe}}</strong><br>原作业 {{row.unfinished_switch.job_id}}<br>实际开始 {{beijing(row.unfinished_switch.started_at).replace('T',' ')}} 北京时间<br>现场剩余 {{row.unfinished_switch.remaining_hours}} 小时<br><template v-if="row.unfinished_switch.standard_hours!=null">整次标准 {{row.unfinished_switch.standard_hours}} 小时<br></template><template v-if="row.unfinished_switch.completed_hours!=null">现场已完成 {{row.unfinished_switch.completed_hours}} 小时<br></template>目标 Recipe 尚未就绪<br></template><template v-else><strong>Recipe {{row.recipe_code}}</strong><br></template>状态对应 {{beijing(row.effective_at).replace('T',' ')}} 北京时间<br>来源 {{row.source}}<br>确认人 {{row.actor}}<br>录入时间 {{beijing(row.recorded_at).replace('T',' ')}} 北京时间<el-button v-if="row.state==='unfinished_switch' && row.unfinished_switch && inspectable(row)" :aria-label="`${shared.editor.value ? '安排' : '查看'}剩余作业 ${row.unfinished_switch.job_id}`" @click="arrange(row)">{{shared.editor.value ? '安排剩余切机' : '查看剩余作业'}}</el-button></td><td v-if="shared.editor.value"><el-button :disabled="shared.readonly.value" :aria-label="`更正线体 ${row.line_code} 状态`" @click="correct(row)">更正</el-button></td></tr></tbody></table></div>
    <p v-if="!records.length">尚无现场线体状态确认。旧计划起点与计划推算保留原来源。</p>
    <ResidualSwitchEditor v-if="arrangedRecord?.unfinished_switch" :shared="shared" :record="arrangedRecord" />
    <p v-else-if="shared.workspace.residualSwitch.recordId" role="status">安排所依据的现场记录已变化，请重新选择剩余作业并核对。</p>
    <form v-if="shared.editor.value" aria-label="批量线体状态确认" @submit.prevent="stage">
      <fieldset :disabled="shared.readonly.value" class="line-state-form"><legend>确认或更正现场状态</legend>
        <label>状态类别 <select v-model="form.state" aria-label="线体状态类别"><option value="retained_recipe">保留 Recipe</option><option value="unfinished_switch">实际未完成切机</option></select></label>
        <label>状态对应时点，北京时间 <input v-model="form.effectiveAt" type="datetime-local" step="1" aria-label="线体状态对应时点" required></label>
        <label>现场来源 <input v-model="form.source" aria-label="线体状态来源" placeholder="例如现场点检或交班记录" required></label>
        <div v-if="form.state==='retained_recipe'" class="input-toolbar"><label>批量 Recipe <select v-model="form.batchRecipe" aria-label="批量 Recipe"><option value="">请选择</option><option v-for="recipe in options.recipes" :key="recipe.code" :value="recipe.code">{{recipe.code}} {{recipe.name}}</option></select></label><el-button :disabled="!form.batchRecipe || !form.selectedLines.length" @click="applyRecipe">应用到所选线体</el-button></div>
        <div class="input-table-scroll"><table class="input-table" aria-label="待确认线体"><thead><tr><th>选择</th><th>线体</th><th v-if="form.state==='retained_recipe'">Recipe</th></tr></thead><tbody><tr v-for="row in options.lines" :key="row.code"><td><input v-model="form.selectedLines" :value="row.code" type="checkbox" :aria-label="`确认线体 ${row.code}`" @change="remember(row.code)"></td><td>{{row.code}} {{row.name}}</td><td v-if="form.state==='retained_recipe'"><select v-model="form.recipes[row.code]" :aria-label="`状态 Recipe ${row.code}`"><option value="">请选择</option><option v-for="recipe in options.recipes" :key="recipe.code" :value="recipe.code">{{recipe.code}}</option></select></td></tr></tbody></table></div>
        <template v-if="form.state==='unfinished_switch'"><p>剩余工时以现场确认为准。已完成工时为可选观察值，不按整次标准减去剩余量补造。</p><fieldset v-for="code in form.selectedLines" :key="code" class="line-state-form"><legend>{{code}} 实际切机</legend>
          <label>原切机作业 <input v-model="unfinished(code).jobId" :aria-label="`原切机作业 ${code}`" required></label>
          <label>原 Recipe <select v-model="unfinished(code).fromRecipe" :aria-label="`切机原 Recipe ${code}`"><option value="">请选择</option><option v-for="recipe in options.recipes" :key="recipe.code" :value="recipe.code">{{recipe.code}}</option></select></label>
          <label>目标 Recipe <select v-model="unfinished(code).toRecipe" :aria-label="`切机目标 Recipe ${code}`"><option value="">请选择</option><option v-for="recipe in options.recipes" :key="recipe.code" :value="recipe.code">{{recipe.code}}</option></select></label>
          <label>实际开始时点，北京时间 <input v-model="unfinished(code).startedAt" type="datetime-local" step="1" :aria-label="`切机实际开始时点 ${code}`" required></label>
          <label>现场剩余工时 <input v-model="unfinished(code).remainingHours" :aria-label="`现场剩余工时 ${code}`" placeholder="精确小时，可填分数" required></label>
          <label>整次标准工时，可选 <input v-model="unfinished(code).standardHours" :aria-label="`整次标准工时 ${code}`" placeholder="与现场剩余工时分别保留"></label>
          <label>现场已完成工时，可选 <input v-model="unfinished(code).completedHours" :aria-label="`现场已完成工时 ${code}`" placeholder="仅填写实际观察值"></label>
        </fieldset></template>
        <label>更正原因 <textarea v-model="form.reason" aria-label="线体状态更正原因" placeholder="更正已确认值时填写" /></label>
        <p v-if="error" role="alert">{{error}}</p>
        <el-button native-type="submit" type="primary" :disabled="shared.readonly.value">加入状态确认并预览</el-button>
      </fieldset>
    </form>
    <details v-if="audit.length"><summary>状态确认与更正记录</summary><p v-for="row in audit" :key="row.action_id">{{beijing(row.recorded_at).replace('T',' ')}} · {{row.actor}} · {{row.reason || '现场确认'}} · {{row.changes.length}} 项状态变化</p></details>
  </section>
</template>
