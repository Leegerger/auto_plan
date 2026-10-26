<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { demandRequestFields, demandRequestKey } from '../demandRequests'
import type { DemandComparisonResult } from '../demandComparisonTypes'
import type { useSharedPlan } from '../useSharedPlan'
import DemandTrialDetails from './DemandTrialDetails.vue'
import ExactHours from './ExactHours.vue'

const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
const form=props.shared.workspace.demandTrial, scope=props.shared.workspace.scope
const view=props.shared.workspace.demandComparison
const busy=ref(false),error=ref('')
let request=0
function payload(){
  return {...demandRequestFields(props.shared),mode:'on_time'}
}
const currentRequest=computed(()=>demandRequestKey(props.shared,payload))
function invalidate(){
  request++;busy.value=false
  if(view.result)view.notice='线体比较已失效，覆盖目标和日期已保留，请重新比较。'
  view.result=null;view.selectedLine='';view.requestKey=''
}
watch(currentRequest,invalidate)
onMounted(()=>{if(view.result && view.requestKey!==currentRequest.value)invalidate()})
onBeforeUnmount(()=>{request++})
const selected=computed(()=>view.result?.rows.find(row=>row.line_code===view.selectedLine))
const details=computed(()=>selected.value && view.result?{...selected.value,basis:view.result.basis}:null)
const labels={complete:'本次目标已安排',partial:'部分按时产出',blocked:'暂不可安排',incomplete:'计算未完成'}
function amount(value:number|null){return value===null?'待完成计算':String(value)}
async function compare(){
  if(props.shared.readonly.value)return
  if(!form.startDate || !form.endDate || !scope.line_codes.length){error.value='请明确投入日期及授权比较线体';return}
  error.value='';view.notice='';view.selectedLine='';view.result=null;busy.value=true
  const sequence=++request,key=currentRequest.value
  try{
    const answer=await api<DemandComparisonResult>('/plans/demand-comparison',{method:'POST',body:JSON.stringify(payload())})
    if(sequence!==request || key!==currentRequest.value)return
    view.result=answer;view.requestKey=key
  }catch(e){if(sequence===request)error.value=e instanceof Error?e.message:'无法比较线体'}finally{if(sequence===request)busy.value=false}
}
async function accept(retain:boolean){
  if(props.shared.readonly.value || !selected.value || !view.result)return
  if(view.requestKey!==currentRequest.value){invalidate();return}
  const operation=retain?selected.value.retain_operation:selected.value.operation
  if(!operation || retain && selected.value.operation)return
  invalidate();view.notice='所选线体的完整安排已加入本机动作，正在预览。'
  props.shared.appendOperation(operation)
  await props.shared.check()
  form.open=false;form.actionId='';form.goalsExpanded=true
}
</script>
<template>
  <div class="comparison-controls"><el-button v-if="shared.editor.value" :disabled="shared.readonly.value || busy" @click="compare">比较授权线体</el-button><span>按时覆盖方案，按相同目标和资料分别试算，保持已选日期及授权范围。</span></div>
  <p v-if="busy" role="status">正在从同一冻结计划比较线体…</p><p v-if="error" role="alert">{{error}}</p><p v-if="view.notice" role="status">{{view.notice}}</p>
  <section v-if="view.result" aria-label="独立线体补排比较" class="line-comparison">
    <h4>{{view.result.request.model_code}} · 需求周 {{view.result.request.demand_week}} · 覆盖目标 {{view.result.request.target_output}} 片合格产出</h4>
    <p>投入日期 {{view.result.request.start_date}} 至 {{view.result.request.end_date}} · 按时覆盖方案</p>
    <p>每行都是从同一原计划出发的独立候选，各行的覆盖量和容量不能相加。选择后先核对该线完整安排，再加入本机动作。</p>
    <p>共同依据：共享修订 {{view.result.basis.revision}} · 输入版本 {{view.result.basis.input_version}} · 最近采用版 {{view.result.basis.latest_adoption}} · {{view.result.basis.as_of}}</p>
    <div class="input-table-scroll comparison-scroll" role="region" aria-label="线体比较滚动区域" tabindex="0"><table class="input-table" aria-label="独立线体比较结果"><thead><tr><th>线体/状态</th><th>已安排按时合格产出</th><th>有效按时覆盖</th><th>建议投入</th><th>本次剩余合格目标</th><th>范围内及变更的整项切机工时，前/后</th><th>额外占用，前/后</th><th>阻碍及采用条件</th><th>选择</th></tr></thead><tbody>
      <tr v-for="row in view.result.rows" :key="row.line_code" :aria-selected="row.line_code===view.selectedLine">
        <th scope="row">{{row.line_code}} {{row.line_name}}<br>{{labels[row.status]}}</th><td>{{amount(row.scheduled_on_time_output)}} 片合格产出</td><td>{{amount(row.valid_on_time_output)}} 片合格产出</td><td>{{amount(row.suggested_input)}} 片投入</td><td>{{amount(row.remaining_output_target)}} 片合格产出</td>
        <td><template v-if="row.switch_totals"><ExactHours :value="row.switch_totals.before.total_hours" /> / <ExactHours :value="row.switch_totals.after.total_hours" /></template><span v-else>待完成计算</span></td>
        <td><template v-if="row.switch_totals"><ExactHours :value="row.switch_totals.before.extra_hours" /> / <ExactHours :value="row.switch_totals.after.extra_hours" /></template><span v-else>待完成计算</span></td>
        <td><ul v-if="row.blockers.length"><li v-for="(reason,index) in row.blockers" :key="index">{{reason.date}} {{reason.reason}}</li></ul><span v-else>无新增阻碍，保存及采用仍须核验</span></td>
        <td><button type="button" :aria-pressed="row.line_code===view.selectedLine" :aria-label="`查看 ${row.line_code} 完整安排`" @click="view.selectedLine=row.line_code">查看完整安排</button></td>
      </tr>
    </tbody></table></div>
    <section v-if="details" :aria-label="`${details.line_code} 线体完整安排`" class="selected-arrangement"><h4>所选线体 {{details.line_code}} 的完整安排</h4><DemandTrialDetails :result="details" :shared="shared" @accept="accept" /></section>
  </section>
</template>
<style scoped>
.comparison-controls{display:flex;align-items:center;gap:12px;margin:10px 0}.comparison-controls span{color:var(--muted)}.line-comparison{border-top:1px solid var(--hairline);margin-top:10px;padding-top:8px}.line-comparison p{margin:6px 0}.comparison-scroll{max-height:360px;overflow:auto}.comparison-scroll table{font-size:12px;min-width:1400px}.comparison-scroll th,.comparison-scroll td{padding:7px}.comparison-scroll thead th{position:sticky;top:0;background:var(--canvas);z-index:2}.comparison-scroll tbody th{position:sticky;left:0;background:var(--canvas);z-index:1}.comparison-scroll thead th:first-child{left:0;z-index:3}.comparison-scroll ul{margin:0;padding-left:16px;min-width:200px}.comparison-scroll tr[aria-selected=true]{background:var(--surface-soft)}.selected-arrangement{border-top:1px solid var(--hairline);margin-top:10px;padding-top:8px}
</style>
