<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, principal } from '../api'
import type { DemandTrialResult } from '../demandTrialTypes'
import type { useSharedPlan } from '../useSharedPlan'
import ExactHours from './ExactHours.vue'
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
const form=props.shared.workspace.demandTrial, scope=props.shared.workspace.scope
const options=ref<{lines:{code:string;name:string}[];models:{code:string;name:string}[]}>({lines:[],models:[]})
const busy=ref(false),error=ref('')
let request=0
const result=computed(()=>form.result)
const goal=computed(()=>props.shared.lineage.value.demand_goals?.[form.goalId])
const labels={complete:'本次覆盖目标已安排',partial:'仅安排部分按时覆盖',blocked:'暂不可安排',incomplete:'计算未完成'}
function payload(){
  const {operation_id:_,pending:__,...command}=props.shared.command()
  return {...command,action_id:form.actionId,goal_id:form.goalId||null,model_code:form.modelCode,demand_week:form.demandWeek,target_output:form.targetOutput===''?null:Number(form.targetOutput),mode:'on_time',line_code:form.lineCode,start_date:form.startDate,end_date:form.endDate,scope:{start_date:scope.start_date,end_date:scope.end_date,line_codes:[...scope.line_codes],model_codes:[...scope.model_codes]}}
}
const currentRequest=computed(()=>JSON.stringify({actor:principal.value?.user_id,request:props.shared.head.value ? payload() : null,head:props.shared.head.value ? {revision:props.shared.head.value.revision,input_version:props.shared.head.value.input_version,latest_adoption:props.shared.head.value.latest_adoption,as_of:props.shared.head.value.time.as_of}:null}))
function invalidate(){request++;busy.value=false;if(form.result)form.notice='试算已失效，输入已保留，请重新试算。';form.result=null;form.requestKey=''}
watch(currentRequest,()=>invalidate())
onMounted(async()=>{
  if(form.result && form.requestKey!==currentRequest.value)invalidate()
  try{options.value=await api('/inputs/options')}catch(e){error.value=e instanceof Error ? e.message : '无法读取可选线体'}
})
onBeforeUnmount(()=>{request++})
function pieces(value:number|null|undefined,unit='片合格产出'){return value==null ? '待完成计算' : `${value} ${unit}`}
async function calculate(){
  if(props.shared.readonly.value)return
  if(!form.lineCode || !form.startDate || !form.endDate){error.value='请明确选择投入日期和一条线体';return}
  error.value='';form.notice='';busy.value=true
  const sequence=++request, key=currentRequest.value, body=payload()
  try{
    const answer=await api<DemandTrialResult>('/plans/demand-trial',{method:'POST',body:JSON.stringify(body)})
    if(sequence!==request || key!==currentRequest.value)return
    form.result=answer;form.requestKey=key
  }catch(e){if(sequence===request)error.value=e instanceof Error ? e.message : '无法完成按需试算'}finally{if(sequence===request)busy.value=false}
}
async function accept(retain=false){
  if(props.shared.readonly.value || !result.value)return
  if(form.requestKey!==currentRequest.value){invalidate();return}
  const operation=retain ? result.value.retain_operation : result.value.operation
  if(!operation || (retain && result.value.operation))return
  form.result=null;form.requestKey='';form.notice=retain ? '目标已加入本机动作，正在预览。' : '完整安排已加入本机动作，正在预览。'
  props.shared.appendOperation(operation)
  await props.shared.check()
  form.open=false;form.actionId='';form.goalsExpanded=true
}
function confirmation(row:{model_code:string;week:string;kind:string}){return {path:'/data/base',query:{category:'confirmations',model:row.model_code,week:row.week,confirmation:row.kind,from:'schedule'}}}
</script>
<template>
  <section aria-label="按需补排试算" class="demand-trial">
    <div class="section-heading"><div><h3>按需补排</h3><p>{{form.modelCode}} · 需求周 {{form.demandWeek}} · 按时覆盖方案</p></div><el-button @click="form.open=false">收起按需补排</el-button></div>
    <p>覆盖目标按合格产出计算，建议投入另列。先查看单线试算，选用后再进入本机动作及共享预览。</p>
    <p v-if="form.goalId">继续原补排目标 {{goal?.requested_output ?? '待核对'}} 片合格产出，当前余额 {{shared.needsPreview.value ? '待重新预览' : pieces(goal?.remaining_output_target)}}。本次可只处理其中一部分，换线或改日后重新计算投入。</p>
    <form v-if="shared.editor.value" @submit.prevent="calculate">
      <fieldset :disabled="shared.readonly.value || busy" class="demand-trial-form"><legend>目标与投入范围</legend>
        <label>覆盖目标，合格产出片数 <input v-model="form.targetOutput" type="number" min="1" step="1" aria-label="补排合格产出目标" placeholder="留空按当前尚无安排量试算"></label>
        <label>投入起日 <input v-model="form.startDate" type="date" aria-label="补排投入起日" required></label>
        <label>投入终日 <input v-model="form.endDate" type="date" aria-label="补排投入终日" required></label>
        <label>本次单线 <select v-model="form.lineCode" aria-label="补排线体" required><option value="">请选择一条线体</option><option v-if="form.lineCode && !options.lines.some(row=>row.code===form.lineCode)" :value="form.lineCode">{{form.lineCode}} · 请重新核对</option><option v-for="row in options.lines" :key="row.code" :value="row.code">{{row.code}} {{row.name}}</option></select></label>
        <label>授权范围起日 <input v-model="scope.start_date" type="date" aria-label="补排调整范围起日" required></label>
        <label>授权范围终日 <input v-model="scope.end_date" type="date" aria-label="补排调整范围终日" required></label>
        <label>授权线体 <select v-model="scope.line_codes" multiple aria-label="补排调整范围线体"><option v-for="row in options.lines" :key="row.code" :value="row.code">{{row.code}}</option></select></label>
        <label>授权机种，不选表示全部 <select v-model="scope.model_codes" multiple aria-label="补排调整范围机种"><option v-for="row in options.models" :key="row.code" :value="row.code">{{row.code}}</option></select></label>
        <el-button native-type="submit" type="primary">试算按时覆盖</el-button>
      </fieldset>
    </form>
    <p v-if="busy" role="status">正在按当前计划与资料试算…</p><p v-if="error" role="alert">{{error}}</p><p v-if="form.notice" role="status">{{form.notice}}</p>
    <section v-if="result" aria-label="单线补排试算结果">
      <h4>{{labels[result.status]}}</h4>
      <p>服务端试算依据：共享修订 {{result.basis.revision}} · 输入版本 {{result.basis.input_version}} · 最近采用版 {{result.basis.latest_adoption}} · {{result.basis.as_of}}</p>
      <div class="demand-trial-amounts"><span>覆盖目标 {{pieces(result.request.target_output)}}</span><span>建议投入 {{pieces(result.suggested_input,'片投入')}}</span><span>按时产出已安排 {{pieces(result.scheduled_on_time_output)}}</span><span>有效按时覆盖 {{pieces(result.valid_on_time_output)}}</span><strong>{{result.request.goal_id?'本次试算剩余目标':'剩余补排目标'}} {{pieces(result.remaining_output_target)}}</strong><strong v-if="result.request.goal_id">原补排目标余额 {{pieces(result.goal_remaining_output_target)}}</strong></div>
      <p>本次只使用 {{result.request.line_code}}，投入日期 {{result.request.start_date}} 至 {{result.request.end_date}}。较早需求周继续优先归属，确认事项按原需求周分别列出。</p>
      <div class="input-table-scroll"><table class="input-table" aria-label="建议逐日投入"><thead><tr><th>生产日</th><th>线体</th><th>机种</th><th>投入片数</th><th>日内顺序</th><th>精确生产工时</th></tr></thead><tbody><tr v-for="row in result.placements" :key="row.id"><td>{{row.date}}</td><td>{{row.line_code}}</td><td>{{row.model_code}}</td><td>{{row.quantity}}</td><td>{{row.order}}</td><td>{{row.hours}} 小时</td></tr></tbody></table></div>
      <div class="input-table-scroll"><table class="input-table" aria-label="各需求周覆盖影响"><thead><tr><th>机种/需求周</th><th>归属产出，前/后</th><th>已安排按时产出，前/后</th><th>有效按时覆盖，前/后</th><th>迟交已安排，前/后</th><th>待物料/出货确认</th></tr></thead><tbody><tr v-for="row in result.week_effects" :key="`${row.model_code}/${row.week}`"><td>{{row.model_code}} / {{row.week}}</td><td>{{row.before.attributed}} / {{row.after.attributed}}</td><td>{{row.physical_on_time_before}} / {{row.physical_on_time_after}}</td><td>{{row.before.on_time}} / {{row.after.on_time}}</td><td>{{row.physical_late_before}} / {{row.physical_late_after}}</td><td>{{row.after.pending_material}} / {{row.after.pending_shipping}}</td></tr></tbody></table></div>
      <details><summary>前后切机与精确占用</summary><section v-for="phase in [{title:'试算前',rows:result.switch_effects.before},{title:'试算后',rows:result.switch_effects.after}]" :key="phase.title"><h5>{{phase.title}}</h5><table class="input-table" :aria-label="`${phase.title}切机影响`"><thead><tr><th>作业/线体</th><th>原/目标 Recipe</th><th>本次作业工时</th><th>普通空闲</th><th>额外占用</th></tr></thead><tbody><tr v-for="row in phase.rows" :key="row.id"><td>{{row.id}} / {{row.line_code}}<br>{{row.kind==='residual'?'已开始作业的剩余部分':'计划切机'}}</td><td>{{row.from_recipe}} → {{row.to_recipe}}</td><td><ExactHours :value="row.total_hours" /></td><td><ExactHours :value="row.idle_hours" /></td><td><ExactHours :value="row.extra_hours" /></td></tr></tbody></table></section></details>
      <div v-if="result.required_confirmations.length" class="input-table-scroll"><table class="input-table" aria-label="补排所需确认"><thead><tr><th>需求</th><th>确认类型</th><th>待确认合格产出</th><th>所需提前投入</th><th>日期边界</th><th>处理</th></tr></thead><tbody><tr v-for="(row,index) in result.required_confirmations" :key="index"><td>{{row.model_code}} / {{row.week}}</td><td>{{row.kind==='material'?'物料':'出货'}}{{row.banned?' · 跨周禁令':''}}</td><td>{{row.pending_output}} 片</td><td>{{row.required_input ?? '—'}} 片投入</td><td>{{row.earliest_input_date}} / {{row.latest_output_date}}</td><td><router-link :to="confirmation(row)">核对确认资料</router-link></td></tr></tbody></table></div>
      <div v-if="result.blockers.length" class="input-table-scroll"><table class="input-table" aria-label="补排阻碍与采用条件"><thead><tr><th>生产日/线体/机种</th><th>原因</th></tr></thead><tbody><tr v-for="(row,index) in result.blockers" :key="index"><td>{{row.date}} / {{row.line_code}} / {{row.model_code}}</td><td>{{row.reason}}</td></tr></tbody></table></div>
      <div class="actions"><el-button v-if="result.operation && shared.editor.value" type="primary" :disabled="shared.readonly.value" @click="accept()">选用完整安排并预览</el-button><el-button v-else-if="result.retain_operation && shared.editor.value" :disabled="shared.readonly.value" @click="accept(true)">仅保留补排目标并预览</el-button></div>
    </section>
  </section>
</template>
<style scoped>
.demand-trial{border-top:1px solid var(--hairline);padding:10px 12px;font-size:12px;min-width:0}.demand-trial h3{font-size:15px;margin:0}.demand-trial p{margin:6px 0}.demand-trial-form{display:flex;flex-wrap:wrap;gap:8px 14px;padding:10px}.demand-trial-form label{display:flex;flex-direction:column;gap:4px}.demand-trial-form input,.demand-trial-form select{min-height:30px;max-width:230px}.demand-trial-amounts{display:flex;flex-wrap:wrap;gap:6px 18px;margin:8px 0}.demand-trial .input-table{font-size:12px;min-width:650px}.demand-trial .input-table th,.demand-trial .input-table td{padding:6px}.demand-trial details{margin:8px 0}
</style>
