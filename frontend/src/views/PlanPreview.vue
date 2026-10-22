<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, online } from '../api'
import ExactHours from '../components/ExactHours.vue'
import OutputResults from '../components/OutputResults.vue'
import type { OutputDay, OutputEvaluation } from '../outputTypes'
import { useSharedPlan } from '../useSharedPlan'

interface Master {code:string;name:string;active:boolean}
interface Inputs {input_version:number;masters:{models:Master[];recipes:Master[];lines:Master[]}}
interface CapacityDay extends OutputDay {ending_recipe:string|null}
interface Evaluation extends OutputEvaluation {days?:CapacityDay[];switch_count?:number;switches?:{id:string;duration_state:string;total_hours:{exact:string;display:string};idle_hours:{exact:string;display:string};extra_hours:{exact:string;display:string}}[]}

const shared=useSharedPlan()
const {head,plan,hourDisplay,preview}=shared
const inputs=ref<Inputs|null>(null)
const selectedDate=ref('')
const selectedLine=ref('')
const error=ref('')
const busy=ref(false)
const evaluation=computed(()=>((preview.value??head.value?.evaluation??null) as Evaluation|null))
const segments=computed(()=>plan.value?.segments.filter(row=>row.date===selectedDate.value&&row.line_code===selectedLine.value).sort((left,right)=>Number(left.order)-Number(right.order))??[])
const switches=computed(()=>plan.value?.switches.filter(row=>row.line_code===selectedLine.value&&row.allocations.some(part=>part.date===selectedDate.value))??[])
const capacity=computed(()=>evaluation.value?.days?.find(row=>row.date===selectedDate.value&&row.line_code===selectedLine.value)??null)
const issues=computed(()=>evaluation.value?.issues?.filter(row=>(!row.date||row.date===selectedDate.value)&&(!row.line_code||row.line_code===selectedLine.value))??[])
function beijingDay(value:string){return new Date(Date.parse(value)-8*3600000).toISOString().slice(0,10)}
async function load(){
  busy.value=true;error.value=''
  try{
    inputs.value=await api<Inputs>('/inputs')
    selectedLine.value||=inputs.value.masters.lines.find(row=>row.active)?.code??inputs.value.masters.lines[0]?.code??''
    selectedDate.value||=head.value?beijingDay(head.value.time.as_of):''
  }catch(e){error.value=e instanceof Error?e.message:'读取资料失败'}finally{busy.value=false}
}
watch(head,value=>{if(value&&!selectedDate.value)selectedDate.value=beijingDay(value.time.as_of)},{immediate:true})
onMounted(load)
</script>

<template>
  <section class="exact-hours-view">
    <header class="page-heading">
      <div><p class="eyebrow">计划工作区的只读依据</p><h1>日内详情与工时预览</h1><p class="page-subtitle">查看当前计划或有效预览的精确工时、容量、切机与问题。</p></div>
      <router-link to="/schedule"><el-button type="primary">返回计划工作区</el-button></router-link>
    </header>
    <el-alert v-if="shared.unknown.value" title="共享计划保存结果待核实，当前只读。" type="warning" :closable="false"/>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <p v-if="busy" role="status">正在读取工时依据…</p>
    <div class="input-toolbar">
      <label>生产日期 <input v-model="selectedDate" type="date" aria-label="详情生产日期"/></label>
      <label>线体 <select v-model="selectedLine" aria-label="详情线体"><option v-for="line in inputs?.masters.lines" :key="line.code" :value="line.code">{{ line.code }} {{ line.name }}</option></select></label>
      <el-button @click="load" :disabled="!online||busy">重新读取资料</el-button>
      <span v-if="inputs">资料版本 {{ inputs.input_version }}</span>
    </div>
    <section class="content-band">
      <div class="section-heading"><div><p class="eyebrow">生产段</p><h2>{{ selectedDate }} · {{ selectedLine }}</h2></div><span class="section-count">{{ segments.length }} 段</span></div>
      <div class="input-table-scroll"><table class="input-table" aria-label="日内生产顺序"><thead><tr><th>顺序</th><th>机种</th><th>投入量</th><th>精确工时</th></tr></thead><tbody><tr v-for="row in segments" :key="row.id"><td>{{ row.order }}</td><td>{{ row.model_code }}</td><td>{{ row.quantity }} 片</td><td><ExactHours v-if="hourDisplay?.segments[row.id]" :value="hourDisplay.segments[row.id]!"/><span v-else>{{ row.hours }}</span></td></tr></tbody></table></div>
      <p v-if="!segments.length" class="empty-inline">当日没有生产段。</p>
    </section>
    <template v-if="capacity">
      <section class="content-band">
        <div class="hour-summary"><p>日可用 {{ capacity.available_hours }} 小时</p><p>生产 <ExactHours :value="capacity.production_hours"/></p><p>切机 <ExactHours :value="capacity.switch_hours"/></p><p>总占用 <ExactHours :value="capacity.occupied_hours"/></p><p>日末 Recipe {{ capacity.ending_recipe??'未知' }}</p></div>
        <div class="input-table-scroll"><table class="input-table" aria-label="Recipe 容量依据"><thead><tr><th>Recipe</th><th>汇总投入</th><th>生产分配工时</th><th>全日产能</th><th>投入上限</th><th>参数状态</th></tr></thead><tbody><tr v-for="recipe in capacity.recipes" :key="recipe.recipe_code"><td>{{ recipe.recipe_code }}</td><td>{{ recipe.quantity }}</td><td><ExactHours :value="recipe.hours"/></td><td>{{ recipe.full_day_capacity }}</td><td>{{ recipe.limit }}</td><td>{{ ({known:'已知',zero:'已知为零',missing:'缺失',forbidden:'明确不能生产'} as Record<string,string>)[recipe.capacity_state] }}</td></tr></tbody></table></div>
      </section>
      <section v-if="switches.length" class="content-band"><h2>有向切机</h2><div class="input-table-scroll"><table class="input-table" aria-label="切机依据"><thead><tr><th>切机</th><th>Recipe</th><th>日分配</th></tr></thead><tbody><tr v-for="change in switches" :key="change.id"><td>{{ change.id }}</td><td>{{ change.from_recipe }} → {{ change.to_recipe }}</td><td><span v-for="(part,index) in change.allocations" :key="index">{{ part.date }} · 顺序 {{ part.order }} · <ExactHours v-if="hourDisplay?.switches[change.id]?.[index]?.hours" :value="hourDisplay.switches[change.id]![index]!.hours"/>；</span></td></tr></tbody></table></div></section>
    </template>
    <section v-if="issues.length" class="content-band"><h2>当前问题</h2><el-table :data="issues" aria-label="工时预览问题"><el-table-column prop="date" label="生产日期"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="demand_week" label="需求周"/><el-table-column prop="related_batch_ids" label="相关批次"/><el-table-column prop="reason" label="问题"/></el-table></section>
    <OutputResults v-if="evaluation" :result="evaluation"/>
  </section>
</template>
