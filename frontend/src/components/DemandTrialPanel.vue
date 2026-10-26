<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, principal } from '../api'
import type { DemandTrialResult } from '../demandTrialTypes'
import type { useSharedPlan } from '../useSharedPlan'
import DemandTrialDetails from './DemandTrialDetails.vue'
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
const form=props.shared.workspace.demandTrial, scope=props.shared.workspace.scope
const options=ref<{lines:{code:string;name:string}[];models:{code:string;name:string}[]}>({lines:[],models:[]})
const busy=ref(false),error=ref('')
let request=0
const result=computed(()=>form.result)
function pieces(value:number|null|undefined,unit='片合格产出'){return value==null ? '待完成计算' : `${value} ${unit}`}
const goal=computed(()=>props.shared.lineage.value.demand_goals?.[form.goalId])
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
    <DemandTrialDetails v-if="result" :result="result" :shared="shared" @accept="accept" />
  </section>
</template>
<style scoped>
.demand-trial{border-top:1px solid var(--hairline);padding:10px 12px;font-size:12px;min-width:0}.demand-trial h3{font-size:15px;margin:0}.demand-trial p{margin:6px 0}.demand-trial-form{display:flex;flex-wrap:wrap;gap:8px 14px;padding:10px}.demand-trial-form label{display:flex;flex-direction:column;gap:4px}.demand-trial-form input,.demand-trial-form select{min-height:30px;max-width:230px}.demand-trial-amounts{display:flex;flex-wrap:wrap;gap:6px 18px;margin:8px 0}.demand-trial .input-table{font-size:12px;min-width:650px}.demand-trial .input-table th,.demand-trial .input-table td{padding:6px}.demand-trial details{margin:8px 0}
</style>
