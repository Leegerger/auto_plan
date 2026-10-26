<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, toRefs, watch } from 'vue'
import { api } from '../api'
import type { BusinessOperation } from '../useSharedPlan'
import { useSharedPlan } from '../useSharedPlan'
import ExactHours from './ExactHours.vue'
import QuantityChange from './QuantityChange.vue'
import LineWeekState from './LineWeekState.vue'
import MatrixDayState from './MatrixDayState.vue'
import type { WorkspaceCapacity, WorkspaceLineWeek } from '../workspaceTypes'
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
const {head,rows,plan,hourDisplay,readonly,appendOperation,check,unallocated}=props.shared
const options=ref<{models:{code:string;name:string;recipe_code:string}[];lines:{code:string;name:string}[]}>({models:[],lines:[]})
const workspace=props.shared.workspace
const {week,day,line,lineFilter,modelFilter,selected,exactHours,panelOpen}=toRefs(workspace)
const {form,scope,mapping}=workspace
const optionError=ref(''), capacityBusy=ref(false)
const matrix=ref<HTMLElement|null>(null), panelHeading=ref<HTMLElement|null>(null), panelButton=ref<{ $el:HTMLElement }|null>(null)
let panelTrigger:HTMLElement|null=null
const fillLines=ref<{code:string;name:string}[]>([]), fillDays=ref<{date:string;line_code:string;quantity:number}[]>([])
const currentWeek=computed(()=>head.value?.weeks?.find(value=>value.number===week.value))
const displayLines=computed(()=>[...options.value.lines,...[...new Set([...rows.value.map(row=>row.line_code),...(plan.value?.switches.map(row=>row.line_code) ?? []),...(props.shared.effective.value?.workspace_view?.line_weeks.map(row=>row.line_code) ?? [])])].filter(code=>!options.value.lines.some(row=>row.code===code)).map(code=>({code,name:'历史引用'}))])
const matrixView=props.shared.matrixView
const matrixDays=computed(()=>new Map((matrixView.value?.days ?? []).map(value=>[`${value.date}/${value.line_code}`,value])))
const lineWeeks=computed(()=>new Map((matrixView.value?.line_weeks ?? []).map(value=>[`${value.week}/${value.line_code}`,value])))
const monthCapacities=computed(()=>{
  const values=new Map<string,WorkspaceCapacity[]>()
  for(const row of matrixView.value?.capacities ?? []){const key=`${row.month}/${row.line_code}`;const group=values.get(key) ?? [];group.push(row);values.set(key,group)}
  return values
})
const filteredRecipe=computed(()=>options.value.models.find(value=>value.code===modelFilter.value)?.recipe_code)
const completeDay=computed(()=>matrixDays.value.get(`${day.value}/${line.value}`))
const selectedLineWeek=computed(()=>{
  const viewed=head.value?.weeks.find(value=>value.dates.includes(day.value))
  return viewed ? lineWeeks.value.get(`${viewed.start_date}/${line.value}`) : undefined
})
function switchPaths(state:WorkspaceLineWeek|undefined){return (state?.first_production?.planned_switch_ids ?? []).flatMap(id=>{const value=plan.value?.switches.find(row=>row.id===id);return value ? [`${value.from_recipe} → ${value.to_recipe}`] : []})}
function matrixDay(date:string,code:string){return matrixDays.value.get(`${date}/${code}`)}
function matrixCapacities(date:string,code:string){return monthCapacities.value.get(`${date.slice(0,7)}/${code}`) ?? []}
function hiddenCount(date:string,code:string){return cell(date,code).length-visibleCellRows(date,code).length}
const filteredLines=computed(()=>lineFilter.value?displayLines.value.filter(value=>value.code===lineFilter.value):displayLines.value)
const weekItems=computed(()=>head.value?.weeks.map(value=>{
  const outputs=props.shared.evaluation.value?.weekly_results.filter(row=>row.week===value.start_date) ?? []
  return {...value,incomplete:outputs.some(row=>!row.computation_complete),gap:outputs.reduce((sum,row)=>sum+row.gap,0),pending:outputs.reduce((sum,row)=>sum+row.pending,0),hasPlan:rows.value.some(row=>value.dates.includes(row.date))}
})??[])
const cells=computed(()=>{
  const grouped=new Map<string,typeof rows.value>()
  for(const row of rows.value){
    const key=`${row.date}/${row.line_code}`
    const values=grouped.get(key)
    if(values) values.push(row)
    else grouped.set(key,[row])
  }
  for(const values of grouped.values()) values.sort((a,b)=>a.order-b.order)
  return grouped
})
const visibleCellRows=(date:string,code:string)=>cell(date,code).filter(row=>!modelFilter.value||row.model_code===modelFilter.value)
const daily=computed(()=>visibleCellRows(day.value,line.value))
const chosen=computed(()=>rows.value.filter(row=>selected.value.includes(row.id)))
watch(chosen,value=>{const key=selected.value.join(',');if(key!==workspace.exactHoursSelection){workspace.exactHoursSelection=key;exactHours.value=value.length===1?value[0]!.hours:''}},{immediate:true})
const sourceLines=computed(()=>[...new Set(chosen.value.map(row=>row.line_code))])
const evaluation=props.shared.evaluation
const capacity=computed(()=>evaluation.value?.days?.find(value=>value.date===day.value && value.line_code===line.value))
const dailyLocks=computed(()=>props.shared.context.value?.locks.filter(value=>value.date===day.value&&value.line_code===line.value) ?? [])
const dailySwitches=computed(()=>plan.value?.switches.filter(value=>value.line_code===line.value && value.allocations.some(part=>part.date===day.value)) ?? [])
const dailyIssues=computed(()=>evaluation.value?.issues?.filter(value=>(!value.date||value.date===day.value)&&(!value.line_code||value.line_code===line.value)&&(!modelFilter.value||!value.model_code||value.model_code===modelFilter.value))??[])
defineExpose({selected,scope,day,line})
const kinds:Record<string,string>={append_fill:'追加填量',swap_segments:'交换两段',move_segment:'移动一段',split_segment:'拆分一段',clear_segments:'清空所选段',copy_segments:'复制新增',overwrite_copy_segments:'覆盖复制'}
watch(head,value=>{ if (!value || scope.start_date) return; const first=value.weeks[0]!; scope.start_date=first.dates[0]!;scope.end_date=first.dates[6]!;form.start_date=scope.start_date;form.end_date=scope.end_date;form.target_date=scope.start_date;day.value=scope.start_date },{immediate:true})
watch(sourceLines,values=>values.forEach(value=>{mapping[value] ??=value}))
onMounted(async()=>{
  try {
    options.value=await api('/inputs/options')
    if(!workspace.scopeInitialized){scope.line_codes=options.value.lines.map(value=>value.code);workspace.scopeInitialized=true}
    line.value ||= options.value.lines[0]?.code ?? '';form.target_line ||= line.value
    await nextTick()
    if(matrix.value){matrix.value.scrollLeft=workspace.scroll.left;matrix.value.scrollTop=workspace.scroll.top}
    window.scrollTo({top:workspace.scroll.page})
  } catch(e){optionError.value=e instanceof Error ? e.message : '无法读取主资料'}
})
onBeforeUnmount(()=>{workspace.scroll.page=window.scrollY;rememberScroll()})
function rememberScroll(){if(matrix.value){workspace.scroll.left=matrix.value.scrollLeft;workspace.scroll.top=matrix.value.scrollTop}}
async function openPanel(){panelOpen.value=true;await nextTick();panelHeading.value?.focus({preventScroll:true})}
async function closePanel(){
  panelOpen.value=false
  await nextTick()
  const cell=Array.from(matrix.value?.querySelectorAll<HTMLButtonElement>('.plan-cell') ?? []).find(value=>value.dataset.date===day.value&&value.dataset.line===line.value)
  const target=panelTrigger?.isConnected ? panelTrigger : cell ?? panelButton.value?.$el
  target?.focus({preventScroll:true})
}

function cell(date:string,code:string){return cells.value.get(`${date}/${code}`) ?? []}
function open(date:string,code:string,event:MouseEvent){day.value=date;line.value=code;panelTrigger=event.currentTarget as HTMLElement;void openPanel()}
function choose(id:string){selected.value=selected.value.includes(id) ? selected.value.filter(value=>value!==id) : [...selected.value,id]}
async function capacities(){
  if (readonly.value || !form.model_code) return
  capacityBusy.value=true;optionError.value=''
  try {
    const {operation_id:_,pending:__,...command}=props.shared.command()
    const result=await api<{lines:{code:string;name:string}[];days:{date:string;line_code:string;quantity:number}[]}>('/plans/fill-options',{method:'POST',body:JSON.stringify({...command,model_code:form.model_code,start_date:form.start_date,end_date:form.end_date,line_code:form.line_code || null,scope})})
    fillLines.value=result.lines;fillDays.value=result.days
    if (form.line_code && !result.lines.some(value=>value.code===form.line_code)) form.line_code=''
  }catch(e){optionError.value=e instanceof Error ? e.message : '无法试算容量'}finally{capacityBusy.value=false}
}
watch([()=>props.shared.operations.value,()=>props.shared.sourceNotice.value,()=>form.start_date,()=>form.end_date,()=>form.model_code,()=>form.line_code,scope],()=>{fillDays.value=[]},{deep:true,flush:'sync'})
function operationScope(){return {start_date:scope.start_date,end_date:scope.end_date,line_codes:[...scope.line_codes],model_codes:[...scope.model_codes]}}
async function stage(){
  optionError.value=''
  let operation:BusinessOperation={kind:form.kind,scope:operationScope()}
  if(form.kind==='append_fill') operation={...operation,model_code:form.model_code,quantity:form.quantity,line_code:form.line_code,start_date:form.start_date,end_date:form.end_date,continue_id:form.continue_id || null}
  else if(form.kind==='move_segment' || form.kind==='split_segment') {
    if(selected.value.length!==1){optionError.value='请选择一段生产安排';return}
    operation={...operation,segment_id:selected.value[0],target_date:form.target_date,target_line:form.target_line,target_order:form.target_order,...(form.kind==='split_segment'?{quantity:form.quantity}:{})}
  }else if(form.kind==='copy_segments' || form.kind==='overwrite_copy_segments') operation={...operation,segment_ids:[...selected.value],target_start_date:form.target_date,line_mapping:Object.fromEntries(sourceLines.value.map(value=>[value,mapping[value]]))}
  else operation={...operation,segment_ids:[...selected.value]}
  appendOperation(operation);await check()
}
async function order(id:string,direction:number){const ids=daily.value.map(value=>value.id);const index=ids.indexOf(id),other=index+direction;if(other<0||other>=ids.length)return;[ids[index],ids[other]]=[ids[other]!,ids[index]!];appendOperation({kind:'reorder_segments',date:day.value,line_code:line.value,segment_ids:ids,scope:operationScope()});await check()}
function continueInput(value:{id:string;model_code:string;quantity:number}){form.kind='append_fill';form.model_code=value.model_code;form.quantity=value.quantity;form.continue_id=value.id;form.line_code='';fillDays.value=[];void openPanel();void capacities()}
async function discard(id:string){appendOperation({kind:'discard_unallocated',result_ids:[id],scope:operationScope()});await check()}
async function stageExactHours(){
  optionError.value=''
  if(chosen.value.length!==1)return
  const row=chosen.value[0]!
  if(row.date<scope.start_date || row.date>scope.end_date || !scope.line_codes.includes(row.line_code) || (scope.model_codes.length && !scope.model_codes.includes(row.model_code))){optionError.value='所选生产段位于授权调整范围之外，请明确扩大范围后预览';return}
  if(!exactHours.value.trim()){optionError.value='精确工时不能留空';return}
  appendOperation({kind:'patch_plan',segments:[{...chosen.value[0]!,hours:exactHours.value}]})
  await check()
}
</script>
<template>
  <section aria-label="周计划编辑">
    <h2>十五周计划</h2>
    <nav class="week-strip" aria-label="十五周导航">
      <button v-for="item in weekItems" :key="item.number" type="button" class="week-jump" :class="{active:item.number===week, 'has-plan':item.hasPlan, 'has-gap':item.gap>0}" :aria-label="`第 ${item.number} 周 ${item.label}`" @click="week=item.number">
        <span>第 {{item.number}} 周</span><strong>{{item.label}}</strong><small>{{shared.needsPreview.value?'待重新预览':item.incomplete?'核算未完成':item.gap?'缺口 '+item.gap:item.hasPlan?'已有安排':'待排程'}}</small>
      </button>
    </nav>
    <div class="workspace-context" role="region" aria-label="工作区查看与调整范围">
      <span>当前投入周 第 {{week}} 周 · {{currentWeek?.start_date}} 至 {{currentWeek?.dates[6]}}</span>
      <span>所选日期 {{day || '尚未选择'}} · 线体 {{line || '尚未选择'}}<span v-if="day && !currentWeek?.dates.includes(day)"> · 位于其他查看周</span></span>
      <span>实际授权调整范围 {{scope.start_date}} 至 {{scope.end_date}} · {{scope.line_codes.length}} 条线体</span>
      <el-button v-if="!panelOpen" ref="panelButton" aria-label="打开对象面板" @click="openPanel">打开对象面板</el-button>
    </div>
    <div class="schedule-frame" :class="{'panel-closed':!panelOpen}">
      <div class="schedule-board">
        <div class="input-toolbar"><label>周次 <select v-model.number="week" aria-label="计划周次"><option v-for="value in head?.weeks" :key="value.number" :value="value.number">第 {{value.number}} 周 · {{value.label}} · {{value.start_date}}</option></select></label><label>筛选线体 <select v-model="lineFilter" aria-label="筛选线体"><option value="">全部线体</option><option v-for="value in displayLines" :key="value.code" :value="value.code">{{value.code}} {{value.name}}</option></select></label><label>筛选机种 <select v-model="modelFilter" aria-label="筛选机种"><option value="">全部机种</option><option v-for="value in options.models" :key="value.code" :value="value.code">{{value.code}} {{value.name}}</option></select></label></div>
        <div ref="matrix" class="input-table-scroll matrix-scroll" role="region" aria-label="周矩阵滚动区域" tabindex="0" @scroll="rememberScroll"><table class="input-table weekly-grid" aria-label="十五周日期线体计划"><thead><tr><th>线体</th><th v-for="date in currentWeek?.dates" :key="date">{{date}}</th></tr></thead><tbody><tr v-for="value in filteredLines" :key="value.code"><th scope="row" :aria-label="`${value.code} ${value.name}`"><strong>{{value.code}} {{value.name}}</strong><LineWeekState :state="lineWeeks.get(`${currentWeek?.start_date}/${value.code}`)" :switch-paths="switchPaths(lineWeeks.get(`${currentWeek?.start_date}/${value.code}`))" :stale="shared.needsPreview.value" /></th><td v-for="date in currentWeek?.dates" :key="date" :class="{'selected-cell':date===day && value.code===line}"><button type="button" class="plan-cell" :data-date="date" :data-line="value.code" :aria-label="`${date} ${value.code} 日内详情`" @click="open(date,value.code,$event)"><span v-for="row in visibleCellRows(date,value.code)" :key="row.id">{{row.model_code}} · {{row.quantity}}片<br></span><span v-if="hiddenCount(date,value.code)" class="hidden-production">另有 {{hiddenCount(date,value.code)}} 段已排生产被筛选隐藏</span><span v-else-if="!visibleCellRows(date,value.code).length">无已排生产</span><MatrixDayState :day="matrixDay(date,value.code)" :capacities="matrixCapacities(date,value.code)" :recipe-code="filteredRecipe" :stale="shared.needsPreview.value" /></button></td></tr></tbody></table></div>
      </div>
      <aside v-show="panelOpen" class="schedule-inspector" aria-label="排程对象详情" @keydown.esc.prevent="closePanel">
    <div class="inspector-heading"><h3 ref="panelHeading" tabindex="-1">{{day}} · {{line}} 日内详情</h3><el-button aria-label="关闭对象面板" @click="closePanel">关闭</el-button></div>
    <section v-if="selectedLineWeek" class="inspector-line-state" aria-label="所选线体周初状态"><h4>周初线体状态</h4><LineWeekState :state="selectedLineWeek" :switch-paths="switchPaths(selectedLineWeek)" /></section>
    <section v-if="selectedLineWeek?.current_confirmation" aria-label="当前现场确认" class="inspector-line-state"><h4>当前现场确认</h4><p>Recipe {{selectedLineWeek.current_confirmation.recipe_code}} · {{selectedLineWeek.current_confirmation.observed_at}}</p><p>{{selectedLineWeek.current_confirmation.valid ? '现场依据有效' : '现场依据已变化，需重新核对'}}</p></section>
    <section v-if="completeDay" role="region" aria-label="完整日容量依据" class="inspector-line-state"><h4>{{completeDay.basis==='confirmed_remaining_day' ? '现场确认剩余工时' : '日历与工时'}}</h4><p>日历可用 <ExactHours :value="completeDay.calendar_hours" /></p><p>当前可用 <ExactHours :value="completeDay.available_hours" /></p><p>已占用 <ExactHours :value="completeDay.occupied_hours" /></p><p>剩余 <ExactHours :value="completeDay.remaining_hours" /></p><p v-if="completeDay.holiday">Holiday{{completeDay.opening_exception ? ' · 开工例外' : ''}}</p><p v-if="completeDay.maintenance_hours">保养 {{completeDay.maintenance_hours}} 小时</p><p v-if="completeDay.experiment_hours">实验 {{completeDay.experiment_hours}} 小时</p></section>
    <p v-else>{{shared.needsPreview.value ? '日容量待重新预览' : '尚未取得完整日容量，不能视为空闲'}}</p>
    <p v-if="hiddenCount(day,line)" class="hidden-production">另有 {{hiddenCount(day,line)}} 段已排生产被筛选隐藏 <el-button @click="modelFilter=''">显示该日全部机种</el-button></p>
    <table class="input-table" aria-label="日内生产顺序"><thead><tr><th>选择</th><th>顺序 / 机种</th><th>投入量</th><th>精确工时</th><th>排序</th></tr></thead><tbody><tr v-for="(row,index) in daily" :key="row.id"><td><input type="checkbox" :checked="selected.includes(row.id)" :disabled="readonly" :aria-label="`选择生产段 ${row.id}`" @change="choose(row.id)"></td><td>{{row.order}} · {{row.model_code}}</td><td>{{row.quantity}}片</td><td><ExactHours v-if="hourDisplay?.segments[row.id]" :value="hourDisplay!.segments[row.id]!" /></td><td><el-button size="small" :disabled="readonly || index===0" :aria-label="`${row.id}上移`" @click="order(row.id,-1)">上移</el-button><el-button size="small" :disabled="readonly || index===daily.length-1" :aria-label="`${row.id}下移`" @click="order(row.id,1)">下移</el-button></td></tr></tbody></table>
    <p v-if="!daily.length">当日尚无生产段。</p>
    <div v-if="capacity" role="region" aria-label="每日容量依据"><p>日可用 {{capacity.available_hours}} 小时；日末 Recipe {{capacity.ending_recipe ?? '未知'}}。完整计划切机 {{evaluation?.switch_count}} 次。</p><div class="hour-summary"><p>生产 <ExactHours :value="capacity.production_hours" /></p><p>切机 <ExactHours :value="capacity.switch_hours" /></p><p>总占用 <ExactHours :value="capacity.occupied_hours" /></p></div><table class="input-table" aria-label="所选日Recipe容量"><thead><tr><th>Recipe</th><th>汇总投入</th><th>生产工时</th><th>全日产能</th><th>精确投入上限</th><th>状态</th></tr></thead><tbody><tr v-for="value in capacity.recipes" :key="value.recipe_code"><td>{{value.recipe_code}}</td><td>{{value.quantity}}</td><td><ExactHours :value="value.hours" /></td><td>{{value.full_day_capacity}}</td><td>{{value.limit}}</td><td>{{({known:'已知',zero:'已知为零',missing:'缺失',forbidden:'明确不能生产'} as Record<string,string>)[value.capacity_state]}}</td></tr></tbody></table></div>
    <p v-for="value in dailySwitches" :key="value.id">切机 {{value.from_recipe}} → {{value.to_recipe}}：<span v-for="(part,index) in value.allocations" :key="index">{{part.date}} 顺序{{part.order}}，<ExactHours v-if="hourDisplay?.switches[value.id]?.[index]?.hours" :value="hourDisplay!.switches[value.id]![index]!.hours" />；</span></p>
    <section v-if="dailyIssues.length" aria-label="所选日问题" class="inspector-issues"><h4>所选日问题</h4><p v-for="(issue,index) in dailyIssues" :key="index"><strong>{{issue.model_code||'计划'}}</strong> · {{issue.reason}}</p></section>
    <p v-if="dailyLocks.length">数量锁：<span v-for="(value,index) in dailyLocks" :key="index">{{value.date}} / {{value.line_code}} / {{value.model_code}} = {{value.quantity}}片；</span></p>
    <template v-if="shared.editor.value">
      <fieldset :disabled="readonly" class="edit-scope"><legend>本次授权调整范围</legend><label>范围起日 <input type="date" v-model="scope.start_date" aria-label="调整范围起日"></label><label>范围终日 <input type="date" v-model="scope.end_date" aria-label="调整范围终日"></label><label>可修改线体 <select multiple v-model="scope.line_codes" aria-label="调整范围线体"><option v-for="value in options.lines" :key="value.code" :value="value.code">{{value.code}}</option></select></label><label>可修改机种（不选表示全部）<select multiple v-model="scope.model_codes" aria-label="调整范围机种"><option v-for="value in options.models" :key="value.code" :value="value.code">{{value.code}}</option></select></label></fieldset>
      <form @submit.prevent="stage" class="manual-form">
        <fieldset :disabled="readonly"><legend>手动动作</legend><div class="input-toolbar"><label>操作 <select v-model="form.kind" aria-label="手动操作"><option v-for="(name,key) in kinds" :value="key" :key="key">{{name}}</option></select></label><p>已选 {{selected.length}} 段：{{chosen.map(value=>`${value.date}/${value.line_code}/${value.model_code} ${value.quantity}片`).join('；')}}</p><el-button @click="selected=[]" :disabled="readonly">取消生产段选择</el-button></div>
          <div v-if="form.kind==='append_fill'" class="input-toolbar"><label>机种 <select v-model="form.model_code" aria-label="追加机种" @change="form.line_code='';fillDays=[];capacities()"><option value="">请选择</option><option v-for="value in options.models" :key="value.code" :value="value.code">{{value.code}} {{value.name}}</option></select></label><label>追加起日 <input type="date" v-model="form.start_date" aria-label="追加起日"></label><label>追加终日 <input type="date" v-model="form.end_date" aria-label="追加终日"></label><el-button @click="form.line_code='';capacities()" :disabled="readonly || capacityBusy">读取可生产线体</el-button><label>逐线选择 <select v-model="form.line_code" aria-label="追加线体" @change="capacities"><option value="">请选择</option><option v-if="form.line_code && !fillLines.some(value=>value.code===form.line_code)" :value="form.line_code">{{form.line_code}} · 保留的线体，需重新试算</option><option v-for="value in fillLines" :key="value.code" :value="value.code">{{value.code}} {{value.name}}</option></select></label></div>
          <div v-if="form.kind==='append_fill' || form.kind==='split_segment'" class="input-toolbar"><label>{{form.kind==='split_segment'?'拆出数量':'新增总量'}} <input type="number" min="1" step="1" v-model.number="form.quantity" aria-label="操作数量" required></label><p v-if="form.continue_id">正在处理指定未分配记录，数量须与该记录一致。<el-button @click="form.continue_id=''" :disabled="readonly">改为新的追加</el-button></p></div>
          <div v-if="['move_segment','split_segment','copy_segments','overwrite_copy_segments'].includes(form.kind)" class="input-toolbar"><label>目标起始日 <input type="date" v-model="form.target_date" aria-label="目标起始日"></label><template v-if="['move_segment','split_segment'].includes(form.kind)"><label>目标线体 <select v-model="form.target_line" aria-label="目标线体"><option v-for="value in options.lines" :key="value.code" :value="value.code">{{value.code}}</option></select></label><label>插入位置（从0起）<input type="number" min="0" step="1" v-model.number="form.target_order" aria-label="目标位置"></label></template><template v-else><label v-for="value in sourceLines" :key="value">源 {{value}} 映射至 <select v-model="mapping[value]" :aria-label="`${value}目标线体`"><option v-for="target in options.lines" :key="target.code" :value="target.code">{{target.code}}</option></select></label></template></div>
          <el-alert v-if="form.kind==='overwrite_copy_segments'" title="覆盖复制会清空每个目标日期线体的已有生产段，再放入复制段。请核对完整预览。" type="warning" :closable="false" />
          <p v-if="optionError" role="alert">{{optionError}}</p><p v-if="capacityBusy" role="status">正在试算精确容量…</p>
          <table v-if="fillDays.length && form.kind==='append_fill'" class="input-table" aria-label="可追加数量"><thead><tr><th>日期</th><th>所选线体</th><th>最大可预排投入</th></tr></thead><tbody><tr v-for="value in fillDays" :key="value.date" :class="{'available-fill':value.quantity>0}"><td>{{value.date}}</td><td>{{value.line_code}}</td><td>{{value.quantity}}片</td></tr></tbody></table><p v-if="fillDays.length && form.kind==='append_fill'">容量试算不等于已确认可采用。完整动作预览另列物料、出货及保护状态。</p>
          <el-button native-type="submit" type="primary" :disabled="readonly">加入完整动作并预览</el-button>
        </fieldset>
      </form>
      <QuantityChange v-if="chosen.length===1" :shared="shared" :selected="selected" :scope="scope" />
      <form v-if="chosen.length===1" @submit.prevent="stageExactHours" aria-label="精确工时调整">
        <fieldset :disabled="readonly"><legend>精确工时调整</legend>
          <p v-if="chosen.length===1">当前段 {{chosen[0]!.model_code}} 原工时 {{chosen[0]!.hours}}。</p>
          <p v-else>选择一段生产安排后可调整精确工时。</p>
          <div class="input-toolbar"><label>精确工时 <input v-model="exactHours" aria-label="调整后精确工时" placeholder="例如 1/3 或 0.024001" :disabled="chosen.length!==1"/></label><el-button native-type="submit" type="primary" :disabled="readonly||chosen.length!==1">加入工时调整并预览</el-button></div>
        </fieldset>
      </form>

    </template>
      </aside>
    </div>
    <section v-if="shared.editor.value && unallocated.length" class="content-band" aria-label="保留的未分配投入">
      <h3>保留的未分配投入</h3><p>余额不会自动换线、延日或舍弃；它与需求缺口分别保留。</p><table class="input-table" aria-label="未分配投入"><thead><tr><th>机种</th><th>数量</th><th>原因</th><th>处理</th></tr></thead><tbody><tr v-for="value in unallocated" :key="value.id"><td>{{value.model_code}}</td><td>{{value.quantity}}</td><td>{{value.reason}}</td><td><el-button :disabled="readonly" @click="continueInput(value)">继续选线</el-button><el-button :disabled="readonly" @click="discard(value.id)">明确舍弃</el-button></td></tr></tbody></table>
    </section>
  </section>
</template>
