<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowRight, RefreshCw } from '@lucide/vue'
import { api, online } from '../api'
import type { InputSnapshot } from '../inputFields'
import { useSharedPlan } from '../useSharedPlan'
import DemandChangePanel from '../components/DemandChangePanel.vue'

interface DemandFile {file_id:string;attribution_date:string;version:number;effective:boolean;rows:Record<string,unknown>[]}
const shared=useSharedPlan()
const snapshot=ref<InputSnapshot|null>(null)
const files=ref<DemandFile[]>([])
const showChanges=ref(false)
const busy=ref(false),error=ref('')
const weeks=computed(()=>shared.head.value?.weeks??[])
const weekly=computed(()=>shared.head.value?.evaluation.weekly_results??[])
const demandMap=computed(()=>new Map((snapshot.value?.business.demands??[]).map(row=>[`${row.model_code}|${row.week}`,Number(row.quantity??0)])))
const weeklyMap=computed(()=>new Map(weekly.value.map(row=>[`${row.model_code}|${row.week}`,row])))
const deltaMap=computed(()=>{
  const result=new Map<string,number>()
  for(const file of files.value.filter(row=>row.effective)){
    for(const row of file.rows){
      const key=`${String(row.model_code??'')}|${String(row.week??'')}`
      result.set(key,(result.get(key)??0)+Number(row.delta??0))
    }
  }
  return result
})
const modelCodes=computed(()=>{
  const codes=new Set((snapshot.value?.business.demands??[]).map(row=>String(row.model_code??'')))
  for(const model of snapshot.value?.masters.models??[])if(model.active)codes.add(String(model.code))
  return [...codes].filter(Boolean)
})
const totals=computed(()=>weekly.value.reduce((result,row)=>({
  demand:result.demand+row.demand,
  gap:result.gap+row.gap,
  pending:result.pending+row.pending,
}),{demand:0,gap:0,pending:0}))
async function load(){
  busy.value=true;error.value=''
  try{
    snapshot.value=await api<InputSnapshot>('/inputs')
    files.value=shared.head.value?.revision?(await api<{files:DemandFile[]}>('/demands/files')).files:[]
  }catch(e){error.value=e instanceof Error?e.message:'需求读取失败'}finally{busy.value=false}
}
function cell(model:string,week:string){return weeklyMap.value.get(`${model}|${week}`)}
function delta(model:string,week:string){return deltaMap.value.get(`${model}|${week}`)??0}
function signed(value:number){return value>0?`+${value}`:String(value)}
onMounted(load)
</script>

<template>
  <section class="demand-page">
    <header class="page-heading">
      <div><p class="eyebrow">十五周完整需求</p><h1>需求管理</h1><p class="page-subtitle">按机种查看需求、变更量、按时覆盖、缺口与待确认产出。</p></div>
      <div class="heading-actions"><el-button @click="load" :disabled="busy||!online"><RefreshCw :size="16" />重新读取</el-button><el-button type="primary" @click="showChanges=true">导入或调整需求<ArrowRight :size="16" /></el-button></div>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <section class="demand-summary" aria-label="需求摘要">
      <div><span>完整需求</span><strong>{{ totals.demand }}</strong></div>
      <div><span>未补足缺口</span><strong>{{ totals.gap }}</strong></div>
      <div><span>待确认产出</span><strong>{{ totals.pending }}</strong></div>
      <div><span>有效变更日</span><strong>{{ files.filter(row=>row.effective).length }}</strong></div>
    </section>
    <section class="demand-matrix-band" aria-label="机种十五周需求矩阵">
      <div class="input-table-scroll demand-matrix-scroll">
        <table class="input-table demand-matrix" aria-label="机种十五周需求矩阵">
          <thead><tr><th>机种</th><th v-for="week in weeks" :key="week.start_date"><span>第 {{ week.number }} 周</span><strong>{{ week.label }}</strong><small>{{ week.start_date }}</small></th></tr></thead>
          <tbody><tr v-for="model in modelCodes" :key="model"><th>{{ model }}</th><td v-for="week in weeks" :key="`${model}-${week.start_date}`"><div class="demand-cell"><strong>{{ demandMap.get(`${model}|${week.start_date}`)??0 }}</strong><span v-if="delta(model,week.start_date)" class="demand-delta">变更 {{ signed(delta(model,week.start_date)) }}</span><span>覆盖 {{ cell(model,week.start_date)?.on_time??0 }}</span><span :class="{danger:!!cell(model,week.start_date)?.gap}">缺口 {{ cell(model,week.start_date)?.gap??0 }}</span><span>待确认 {{ cell(model,week.start_date)?.pending??0 }}</span></div></td></tr></tbody>
        </table>
      </div>
      <p v-if="!busy&&!modelCodes.length" class="empty-inline">尚无可显示的需求机种。</p>
    </section>
    <el-drawer v-model="showChanges" size="760px" title="导入或调整需求" append-to-body @closed="load">
      <DemandChangePanel />
    </el-drawer>
  </section>
</template>
