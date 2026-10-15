<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, basePath, online } from '../api'
import type { useSharedPlan, Plan } from '../useSharedPlan'
import type { OutputEvaluation } from '../outputTypes'
import OutputResults from './OutputResults.vue'
import ExactHours from './ExactHours.vue'
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
interface Item {revision:number;adoption:number|null;actor:string;recorded_at:string;kind:string;pending:boolean;recoverable:boolean}
interface Historical {revision:number;actor:string;recorded_at:string;kind:string;pending:boolean;input_version:number;latest_adoption:number;hour_display?:{segments:Record<string,{exact:string;display:string}>};snapshot:{time:{as_of:string};plan:Plan;inputs:Record<string,Record<string,Record<string,unknown>[]>>;execution:Record<string,unknown>;reset:Record<string,unknown>|null;locks:Record<string,unknown>[];preferences:Record<string,unknown>};evaluation:OutputEvaluation}
const items=ref<Item[]>([]),choice=ref(''),selected=ref<Historical|null>(null),next=ref<number|null>(null),busy=ref(false),error=ref('')
let selectionRequest=0
const source=computed(()=>{const [kind,id]=choice.value.split(':');return {kind:kind as 'revision'|'adoption',id:Number(id)}})
const path=computed(()=>`/plans/${source.value.kind==='adoption' ? 'adoptions' : 'revisions'}/${source.value.id}`)
const recordedOutput=computed(()=>{const value=selected.value?.evaluation;return value && ['output_sources','weekly_results','attributions','confirmation_usage','excluded_demands'].every(key=>Array.isArray(value[key as keyof OutputEvaluation])) ? value : null})
const parameters=computed(()=>Object.entries(selected.value?.snapshot.inputs ?? {}).flatMap(([section,categories])=>Object.entries(categories).flatMap(([category,rows])=>Array.isArray(rows) ? rows.map(row=>({section,category,description:Object.entries(row).map(([key,value])=>`${key}: ${value ?? '未记录'}`).join('；')})) : [])))
async function list(more=false){
  if(!online.value)return
  busy.value=true;error.value=''
  try{
    const result=await api<{items:Item[];next_before:number|null}>(`/plans/history${more&&next.value ? `?before=${next.value}` : ''}`)
    items.value=more ? [...items.value,...result.items] : result.items;next.value=result.next_before
    if(!choice.value && items.value[0])choice.value=value(items.value[0])
  }catch(e){error.value=e instanceof Error ? e.message : '历史读取失败'}finally{busy.value=false}
}
function value(item:Item){return item.adoption ? `adoption:${item.adoption}` : `revision:${item.revision}`}
watch(()=>props.shared.head.value?.revision,()=>{void list()},{immediate:true})
watch(choice,async()=>{
  const request=++selectionRequest;selected.value=null;error.value=''
  if(!choice.value)return
  try{const result=await api<Historical>(path.value);if(request===selectionRequest)selected.value=result}
  catch(e){if(request===selectionRequest)error.value=e instanceof Error ? e.message : '选定快照读取失败'}
})
async function restore(){
  if(!selected.value || props.shared.readonly.value)return
  props.shared.appendOperation({kind:'restore_history',source:{...source.value}})
  await props.shared.check()
}
async function download(){
  if(!selected.value || busy.value || !online.value)return
  busy.value=true;error.value=''
  try{
    const response=await fetch(`${basePath}/api${path.value}/export.xlsx`,{credentials:'same-origin'})
    if(!response.ok){const body=await response.json();throw new Error(body.detail ?? '导出失败')}
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a')
    link.href=url;link.download=`auto-plan-${source.value.kind}-${source.value.id}.xlsx`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
  }catch(e){error.value=e instanceof Error ? e.message : '导出失败'}finally{busy.value=false}
}
</script>
<template>
  <section aria-label="历史版本">
    <h2>历史版本</h2>
    <p>历史按当时需求、参数、确认和指标快照展示。共享草稿可在保存后30天内恢复，采用版长期保留。恢复先加入本机动作，按当前资料、事实和锁定检查，再由你共享保存及明确采用。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <label>选择历史版本 <select v-model="choice" aria-label="选择历史版本" :disabled="busy"><option v-for="item in items" :key="item.revision" :value="value(item)">{{item.adoption ? `采用版 ${item.adoption}` : '共享草稿'}} · 修订 {{item.revision}} · {{item.actor}} · {{item.recorded_at}}</option></select></label>
    <el-button :disabled="busy || !online" @click="list()">刷新历史版本</el-button>
    <el-button v-if="next" :disabled="busy || !online" @click="list(true)">加载更早版本</el-button>
    <template v-if="selected">
      <p>选定修订 {{selected.revision}} · 当时输入版本 {{selected.input_version}} · {{selected.recorded_at}} · {{selected.actor}}。计算时点 {{selected.snapshot.time.as_of}}。</p>
      <el-button :disabled="busy || !online" @click="download">导出选定快照 Excel</el-button>
      <el-button v-if="shared.editor.value" :disabled="shared.readonly.value || busy" @click="restore">恢复为新草稿并预览</el-button>
      <el-table :data="selected.snapshot.plan.segments" role="table" aria-label="历史每日投入"><el-table-column prop="date" label="生产日"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="order" label="日内顺序"/><el-table-column prop="quantity" label="投入数量"/><el-table-column label="生产工时"><template #default="{row}"><ExactHours v-if="selected.hour_display?.segments[row.id]" :value="selected.hour_display.segments[row.id]!"/><span v-else>{{row.hours}} 精确小时</span></template></el-table-column></el-table>
      <details><summary>当时需求、参数、日历与确认</summary><el-table :data="parameters" role="table" aria-label="历史资料快照"><el-table-column prop="category" label="资料类别"/><el-table-column prop="description" label="当时记录"/></el-table></details>
      <details><summary>当时执行事实与锁定</summary><el-table :data="(selected.snapshot.execution.facts as Record<string,unknown>[]) ?? []"><el-table-column prop="date" label="生产日"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="quantity" label="累计实际投入"/></el-table><el-table :data="selected.snapshot.locks"><el-table-column prop="date" label="锁定日"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="quantity" label="锁定量"/></el-table><p>重置批次 {{selected.snapshot.reset?.batch_id ?? '无'}}；执行确认截止 {{selected.snapshot.execution.cutoff ?? '未记录'}}。完整执行、重置沿革和当日工时证明见选定快照 Excel。</p></details>
      <p v-if="!selected.evaluation.stability">此历史快照未记录稳定性指标。</p>
      <template v-else><p>当时计划变动代价 {{selected.evaluation.stability.weighted_cost}}；日内顺序变动数 {{selected.evaluation.stability.order_change_count}}。</p><el-table :data="selected.evaluation.stability.weeks" role="table" aria-label="历史稳定性"><el-table-column prop="week" label="实际需求周"/><el-table-column prop="rate_display" label="当时变化率"/></el-table></template>
      <OutputResults v-if="recordedOutput" :result="recordedOutput" />
      <p v-else>此历史快照未记录完整产出与需求归属指标。</p>
    </template>
    <p v-else-if="!busy && !items.length">暂无可恢复的共享修订或采用版。</p>
  </section>
</template>
