<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ComputationSource } from '../jobTypes'
import type { InputSnapshot } from '../inputFields'
import { categories, fieldNames } from '../inputFields'
import type { PlanningContext } from '../stabilityTypes'
const props=defineProps<{source:ComputationSource;inputs:InputSnapshot|null;context:PlanningContext|null}>()
const page=ref(1)
const labels:Record<string,string>={...fieldNames,demands:'需求',execution:'实际执行与确认',facts:'实际累计投入',coverage:'执行覆盖',cutoff:'截止时点',associations:'生产段执行关联',association_cells:'关联投入单元',audit:'执行核对记录',day_capacities:'当日剩余工时确认',reset:'计划重置',locks:'数量锁',preferences:'稳定性偏好',near_weeks:'近期周数',near_tolerance_percent:'近期容忍百分比',far_tolerance_percent:'远期容忍百分比',near_week_weight:'近期权重',far_week_weight:'远期权重',wip:'在制品',net_balance:'投入超前落后净余额',production_week:'生产周',batch_id:'批次',remaining_hours:'剩余小时',current_recipe:'当前 Recipe',confirmed_at:'确认时点',reason:'原因',actor:'核对人',recorded_at:'记录时点',start_date:'起日',end_date:'终日',line_codes:'线体',model_codes:'机种',blanks_are_zero:'空白已确认为零',segment_id:'生产段',changes:'变更'}
function display(value:unknown):string{
  if(value===undefined || value===null)return '无'
  if(typeof value==='boolean')return value ? '是' : '否'
  if(Array.isArray(value))return value.map(display).join('；')
  if(typeof value==='object')return Object.entries(value).map(([key,item])=>`${labels[key] ?? key} ${display(item)}`).join('；')
  return String(value)
}
const changes=computed(()=>{
  const rows:{path:string;before:string;after:string}[]=[]
  function compare(before:unknown,after:unknown,path:string[]){
    if(JSON.stringify(before)===JSON.stringify(after))return
    if(Array.isArray(before) && Array.isArray(after) && [...before,...after].every(row=>row && typeof row==='object' && !Array.isArray(row))){
      const identity=(row:Record<string,unknown>,index:number)=>['id','action_id','segment_id','date','line_code','model_code','batch_id','production_week','start_date','end_date','line_codes','model_codes'].filter(key=>row[key]!==undefined).map(key=>display(row[key])).join(' / ') || String(index+1)
      const a=new Map(before.map((row,index)=>[identity(row,index),row])),b=new Map(after.map((row,index)=>[identity(row,index),row]))
      for(const key of new Set([...a.keys(),...b.keys()]))compare(a.get(key),b.get(key),[...path,key])
    }else if(before && after && typeof before==='object' && typeof after==='object' && !Array.isArray(before) && !Array.isArray(after)){
      const a=before as Record<string,unknown>,b=after as Record<string,unknown>
      for(const key of new Set([...Object.keys(a),...Object.keys(b)]))compare(a[key],b[key],[...path,labels[key] ?? key])
    }else rows.push({path:path.join(' / '),before:display(before),after:display(after)})
  }
  for(const section of ['masters','parameters','calendar','business'] as const){
    for(const kind of new Set([...Object.keys(props.inputs?.[section] ?? {}),...Object.keys(props.source.snapshot.inputs[section])])){
      const category=categories.find(row=>row.kind===kind)
      const keys=category?.keys ?? ['model_code','week']
      const before=props.inputs?.[section][kind] ?? [],after=props.source.snapshot.inputs[section][kind] ?? []
      const identity=(row:Record<string,unknown>)=>keys.map(key=>String(row[key] ?? '')).join(' / ')
      const old=new Map(before.map(row=>[identity(row),row])),current=new Map(after.map(row=>[identity(row),row]))
      for(const key of new Set([...old.keys(),...current.keys()]))compare(old.get(key),current.get(key),[category?.title ?? labels[kind] ?? kind,key])
    }
  }
  for(const key of ['execution','reset','locks','preferences'] as const)compare(props.context?.[key],props.source.snapshot[key],[labels[key] ?? key])
  return rows
})
const visible=computed(()=>changes.value.slice((page.value-1)*50,page.value*50))
function exportSource(){
  const url=URL.createObjectURL(new Blob([JSON.stringify(props.source)],{type:'application/json'})),link=document.createElement('a')
  link.href=url;link.download=`computation-source-${props.source.source_revision}-${props.source.input_version}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
</script>
<template>
  <section aria-label="计算来源核对">
    <h3>核对本次计算来源</h3>
    <p>共享修订 {{source.source_revision}} · 输入版本 {{source.input_version}} · 最近采用版 {{source.latest_adoption}}</p>
    <p>来源时点 {{source.snapshot.time.as_of}} · 十五周窗口起日 {{source.snapshot.time.window_start}}</p>
    <p>完整生产计划共 {{source.snapshot.plan.segments.length}} 个生产段；采用基准 {{source.snapshot.baseline?.version ?? '无'}}。参数、需求、执行事实、数量锁、重置和稳定性偏好均已读取，提交时仍会检查是否变化。</p>
    <p>以下与已载入共享计划的资料比较，共 {{changes.length}} 项变化。核对后再勾选并提交。</p>
    <table v-if="changes.length" aria-label="计算来源业务变化"><thead><tr><th>资料</th><th>已载入值</th><th>本次来源值</th></tr></thead><tbody><tr v-for="(row,index) in visible" :key="index"><td>{{row.path}}</td><td>{{row.before}}</td><td>{{row.after}}</td></tr></tbody></table>
    <p v-else>参数、需求、执行事实、数量锁、重置和稳定性偏好与已载入资料相同。</p>
    <div v-if="changes.length>50"><button type="button" :disabled="page===1" @click="page--">上一页变化</button><span>第 {{page}} 页</span><button type="button" :disabled="page*50>=changes.length" @click="page++">下一页变化</button></div>
    <button type="button" @click="exportSource">导出完整计算来源 JSON</button>
    <slot />
  </section>
</template>
<style scoped>table{width:100%;border-collapse:collapse;margin:12px 0}th,td{text-align:left;border:1px solid #d9e1e8;padding:6px;overflow-wrap:anywhere}section{margin:12px 0;padding:12px;border:1px solid #d9e1e8}</style>
