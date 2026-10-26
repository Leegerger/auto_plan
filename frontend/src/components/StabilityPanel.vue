<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import type { useSharedPlan } from '../useSharedPlan'
import type { LockedCell, PlanningPreferences } from '../stabilityTypes'
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
const context=props.shared.context
const result=computed(()=>props.shared.evaluation.value?.stability)
const editor=computed(()=>props.shared.editor.value), readonly=computed(()=>props.shared.readonly.value)
const selected=ref<LockedCell[]>([]), error=ref('')
const dates=computed(()=>props.shared.head.value?.weeks.flatMap(week=>week.dates) ?? [])
const date=ref(''),line=ref(''),model=ref('')
const lines=ref<{code:string;name:string}[]>([]),models=ref<{code:string;name:string}[]>([])
const preferences=ref<PlanningPreferences>({near_weeks:4,near_tolerance_percent:'10',far_tolerance_percent:'30',near_week_weight:'3',far_week_weight:'1'})
function decimalText(value:string):string {
  if(!value.includes('/'))return value
  const [top,bottom]=value.split('/').map(BigInt)
  let remainder=top!%bottom!,digits=''
  for(let n=0;remainder && n<18;n++){remainder*=10n;digits+=(remainder/bottom!).toString();remainder%=bottom!}
  return remainder ? value : `${top!/bottom!}${digits ? '.'+digits : ''}`
}
watch(context,value=>{
  if(!value?.preferences)return
  const p=value.preferences
  preferences.value={...p,near_tolerance_percent:decimalText(p.near_tolerance_percent),far_tolerance_percent:decimalText(p.far_tolerance_percent),near_week_weight:decimalText(p.near_week_weight),far_week_weight:decimalText(p.far_week_weight)}
}, {immediate:true})
watch(()=>props.shared.head.value, async value=>{
  if(!value)return
  date.value ||= value.weeks[0]?.dates[0] ?? ''
  try {
    const input=await api<{masters:{lines:{code:string;name:string}[];models:{code:string;name:string}[]}}>(`/inputs?version=${value.input_version}`)
    lines.value=input.masters.lines;models.value=input.masters.models
    line.value ||= lines.value[0]?.code ?? '';model.value ||= models.value[0]?.code ?? ''
  } catch(e){error.value=e instanceof Error ? e.message : '资料读取失败'}
},{immediate:true})
function addCell(){
  error.value=''
  if(!date.value||!line.value||!model.value){error.value='请选择生产日、线体和机种';return}
  if(selected.value.some(row=>row.date===date.value&&row.line_code===line.value&&row.model_code===model.value))return
  const quantity=(props.shared.plan.value?.segments ?? []).filter(row=>row.date===date.value&&row.line_code===line.value&&row.model_code===model.value).reduce((total,row)=>total+row.quantity,0)
  selected.value.push({date:date.value,line_code:line.value,model_code:model.value,quantity})
}
async function lock(){
  props.shared.appendOperation({kind:'lock_cells',cells:selected.value.map(row=>({...row}))});selected.value=[];await props.shared.check()
}
async function unlock(cells:LockedCell[]){
  props.shared.appendOperation({kind:'unlock_cells',cells:cells.map(({date,line_code,model_code})=>({date,line_code,model_code}))});await props.shared.check()
}
async function applyPreferences(){
  props.shared.appendOperation({kind:'set_preferences',preferences:{...preferences.value}});await props.shared.check()
}
</script>
<template>
  <section aria-label="数量锁定与稳定性">
    <h2>数量锁定与稳定性</h2>
    <p>数量锁按生产日、线体、机种的汇总量保护，包含零数量；日内顺序仍可调整。手动编辑不会自动锁定。锁定、解锁和偏好都先进入本机动作，预览后共享保存。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div v-if="editor">
      <div class="actions">
        <label>锁定生产日 <select v-model="date" :disabled="readonly" aria-label="锁定生产日"><option v-for="day in dates" :key="day" :value="day">{{day}}</option></select></label>
        <label>锁定线体 <select v-model="line" :disabled="readonly" aria-label="锁定线体"><option v-for="row in lines" :key="row.code" :value="row.code">{{row.code}} {{row.name}}</option></select></label>
        <label>锁定机种 <select v-model="model" :disabled="readonly" aria-label="锁定机种"><option v-for="row in models" :key="row.code" :value="row.code">{{row.code}} {{row.name}}</option></select></label>
        <el-button @click="addCell" :disabled="readonly">加入锁定批次</el-button>
      </div>
      <ul aria-label="待锁定批次"><li v-for="(cell,index) in selected" :key="index">{{cell.date}} / {{cell.line_code}} / {{cell.model_code}} / {{cell.quantity}}片 {{cell.quantity===0 ? '锁零' : ''}} <button :disabled="readonly" @click="selected.splice(index,1)">移除</button></li></ul>
      <el-button @click="lock" :disabled="readonly || !selected.length">批量锁定所选数量</el-button>
    </div>
    <el-table role="table" :data="context?.locks ?? []" aria-label="当前数量锁" empty-text="没有数量锁">
      <el-table-column prop="date" label="生产日"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column label="锁定数量"><template #default="{row}">{{row.quantity}} {{row.quantity===0 ? '锁零' : '片'}}</template></el-table-column>
      <el-table-column v-if="editor" label="显式解锁"><template #default="{row}"><el-button :disabled="readonly" @click="unlock([row])">解锁</el-button></template></el-table-column>
    </el-table>
    <el-button v-if="editor" :disabled="readonly || !context?.locks.length" @click="unlock(context?.locks ?? [])">批量解锁全部当前锁</el-button>
    <fieldset v-if="editor" class="stability-preferences" :disabled="readonly" aria-label="稳定性偏好">
      <legend>稳定性偏好</legend>
      <label>近期截至第几周 <input type="number" min="1" max="15" step="1" v-model.number="preferences.near_weeks" aria-label="近期截至周" /></label>
      <label>近期容忍度 % <input v-model="preferences.near_tolerance_percent" inputmode="decimal" aria-label="近期容忍度" /></label>
      <label>远期容忍度 % <input v-model="preferences.far_tolerance_percent" inputmode="decimal" aria-label="远期容忍度" /></label>
      <label>近期变动权重 <input v-model="preferences.near_week_weight" inputmode="decimal" aria-label="近期变动权重" /></label>
      <label>远期变动权重 <input v-model="preferences.far_week_weight" inputmode="decimal" aria-label="远期变动权重" /></label>
      <el-button @click="applyPreferences">预览偏好变更</el-button>
    </fieldset>
    <template v-if="result">
      <p>最近采用基线 {{result.baseline_version ?? '尚未建立'}} · 计划变动代价 {{result.weighted_cost}} · 日内顺序变动数 {{result.order_change_count}}</p>
      <p>逐周按实际日期比较；同一确认执行量从两边各扣除一次。容忍度只作提示，超出后仍可明确采用。</p>
      <p v-if="!result.comparison_complete">未建立基线的范围不计变化率或变动代价。未知执行量不按零计算；部分覆盖的数值只代表已知范围。</p>
      <el-table role="table" :data="result.weeks" aria-label="逐周稳定性">
        <el-table-column prop="number" label="当前窗口周"/><el-table-column prop="week" label="实际周一起日"/><el-table-column label="分段"><template #default="{row}">{{row.band==='near' ? '近期' : '远期'}}</template></el-table-column>
        <el-table-column label="可比较旧待执行量"><template #default="{row}">{{row.state==='new_plan' ? '无基线' : row.old_remaining}}</template></el-table-column><el-table-column label="可比较新待执行量"><template #default="{row}">{{row.state==='new_plan' ? '未比较' : row.new_remaining}}</template></el-table-column><el-table-column prop="absolute_change" label="绝对变化片数"/><el-table-column prop="rate_display" label="变化率及状态"/><el-table-column label="容忍度"><template #default="{row}">{{decimalText(row.tolerance_percent)}}%</template></el-table-column><el-table-column label="提示"><template #default="{row}">{{row.exceeds_tolerance ? '超出容忍度，可核对后采用' : row.unknown_baseline_cells ? '含未建立基线的单元格' : row.unknown_execution_cells ? '执行事实未确认' : ''}}</template></el-table-column>
      </el-table>
    </template>
    <p v-else>此历史快照未记录稳定性指标。</p>
  </section>
</template>

<style scoped>
.stability-preferences {display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:16px 0;padding:16px}
.stability-preferences label {display:flex;flex-direction:column;gap:6px}
.stability-preferences input {max-width:180px;padding:6px}
.stability-preferences .el-button {align-self:end;justify-self:start}
</style>
