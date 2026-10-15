<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import type { BusinessOperation } from '../useSharedPlan'
import { useSharedPlan } from '../useSharedPlan'
import ExactHours from './ExactHours.vue'
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
const {head,rows,plan,hourDisplay,readonly,appendOperation,check,unallocated}=props.shared
const options=ref<{models:{code:string;name:string}[];lines:{code:string;name:string}[]}>({models:[],lines:[]})
const week=ref(1), day=ref(''), line=ref(''), selected=ref<string[]>([])
const form=reactive({kind:'append_fill',model_code:'',quantity:1,line_code:'',start_date:'',end_date:'',target_date:'',target_line:'',target_order:0,continue_id:''})
const scope=reactive({start_date:'',end_date:'',line_codes:[] as string[],model_codes:[] as string[]})
const mapping=reactive<Record<string,string>>({}), optionError=ref(''), capacityBusy=ref(false)
const fillLines=ref<{code:string;name:string}[]>([]), fillDays=ref<{date:string;line_code:string;quantity:number}[]>([])
const currentWeek=computed(()=>head.value?.weeks?.find(value=>value.number===week.value))
const displayLines=computed(()=>[...options.value.lines,...[...new Set(rows.value.map(row=>row.line_code))].filter(code=>!options.value.lines.some(row=>row.code===code)).map(code=>({code,name:'历史引用'}))])
const daily=computed(()=>rows.value.filter(row=>row.date===day.value && row.line_code===line.value).sort((a,b)=>a.order-b.order))
const chosen=computed(()=>rows.value.filter(row=>selected.value.includes(row.id)))
const sourceLines=computed(()=>[...new Set(chosen.value.map(row=>row.line_code))])
const dailySwitches=computed(()=>plan.value?.switches.filter(value=>value.line_code===line.value && value.allocations.some(part=>part.date===day.value)) ?? [])
const kinds:Record<string,string>={append_fill:'追加填量',swap_segments:'交换两段',move_segment:'移动一段',split_segment:'拆分一段',clear_segments:'清空所选段',copy_segments:'复制新增',overwrite_copy_segments:'覆盖复制'}
watch(head,value=>{ if (!value || scope.start_date) return; const first=value.weeks[0]!; scope.start_date=first.dates[0]!;scope.end_date=first.dates[6]!;form.start_date=scope.start_date;form.end_date=scope.end_date;form.target_date=scope.start_date;day.value=scope.start_date })
watch(sourceLines,values=>values.forEach(value=>{mapping[value] ??=value}))
onMounted(async()=>{try {options.value=await api('/inputs/options');scope.line_codes=options.value.lines.map(value=>value.code);line.value=options.value.lines[0]?.code ?? '';form.target_line=line.value} catch(e){optionError.value=e instanceof Error ? e.message : '无法读取主资料'}})
function cell(date:string,code:string){return rows.value.filter(row=>row.date===date && row.line_code===code).sort((a,b)=>a.order-b.order)}
function open(date:string,code:string){day.value=date;line.value=code}
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
function continueInput(value:{id:string;model_code:string;quantity:number}){form.kind='append_fill';form.model_code=value.model_code;form.quantity=value.quantity;form.continue_id=value.id;form.line_code='';fillDays.value=[];void capacities()}
async function discard(id:string){appendOperation({kind:'discard_unallocated',result_ids:[id],scope:operationScope()});await check()}
</script>
<template>
  <section aria-label="周计划编辑">
    <h2>十五周计划</h2>
    <div class="input-toolbar"><label>周次 <select v-model.number="week" aria-label="计划周次"><option v-for="value in head?.weeks" :key="value.number" :value="value.number">第 {{value.number}} 周 · {{value.label}} · {{value.start_date}}</option></select></label><span>点击日期线体查看日内顺序。可跨日保留生产段选择。</span></div>
    <div class="input-table-scroll"><table class="input-table weekly-grid" aria-label="十五周日期线体计划"><thead><tr><th>线体</th><th v-for="date in currentWeek?.dates" :key="date">{{date}}</th></tr></thead><tbody><tr v-for="value in displayLines" :key="value.code"><th>{{value.code}} {{value.name}}</th><td v-for="date in currentWeek?.dates" :key="date" :class="{'selected-cell':date===day && value.code===line}"><button type="button" class="plan-cell" :aria-label="`${date} ${value.code} 日内详情`" @click="open(date,value.code)"><span v-for="row in cell(date,value.code)" :key="row.id">{{row.model_code}} · {{row.quantity}}片<br></span><span v-if="!cell(date,value.code).length">空白</span></button></td></tr></tbody></table></div>
    <h3>{{day}} · {{line}} 日内详情</h3>
    <table class="input-table" aria-label="日内生产顺序"><thead><tr><th>选择</th><th>顺序 / 机种</th><th>投入量</th><th>精确工时</th><th>排序</th></tr></thead><tbody><tr v-for="(row,index) in daily" :key="row.id"><td><input type="checkbox" :checked="selected.includes(row.id)" :disabled="readonly" :aria-label="`选择生产段 ${row.id}`" @change="choose(row.id)"></td><td>{{row.order}} · {{row.model_code}}</td><td>{{row.quantity}}片</td><td><ExactHours v-if="hourDisplay?.segments[row.id]" :value="hourDisplay!.segments[row.id]!" /></td><td><el-button size="small" :disabled="readonly || index===0" :aria-label="`${row.id}上移`" @click="order(row.id,-1)">上移</el-button><el-button size="small" :disabled="readonly || index===daily.length-1" :aria-label="`${row.id}下移`" @click="order(row.id,1)">下移</el-button></td></tr></tbody></table>
    <p v-if="!daily.length">当日尚无生产段。</p>
    <p v-for="value in dailySwitches" :key="value.id">切机 {{value.from_recipe}} → {{value.to_recipe}}：<span v-for="(part,index) in value.allocations" :key="index">{{part.date}} 顺序{{part.order}}，<ExactHours v-if="hourDisplay?.switches[value.id]?.[index]?.hours" :value="hourDisplay!.switches[value.id]![index]!.hours" />；</span></p>
    <p v-if="head?.context.locks.length">数量锁：<span v-for="(value,index) in head.context.locks" :key="index">{{value.date}} / {{value.line_code}} / {{value.model_code}} = {{value.quantity}}片；</span></p>
    <template v-if="shared.editor.value">
      <fieldset :disabled="readonly" class="edit-scope"><legend>本次授权调整范围</legend><label>范围起日 <input type="date" v-model="scope.start_date" aria-label="调整范围起日"></label><label>范围终日 <input type="date" v-model="scope.end_date" aria-label="调整范围终日"></label><label>可修改线体 <select multiple v-model="scope.line_codes" aria-label="调整范围线体"><option v-for="value in options.lines" :key="value.code" :value="value.code">{{value.code}}</option></select></label><label>可修改机种（不选表示全部）<select multiple v-model="scope.model_codes" aria-label="调整范围机种"><option v-for="value in options.models" :key="value.code" :value="value.code">{{value.code}}</option></select></label></fieldset>
      <form @submit.prevent="stage" class="manual-form">
        <fieldset :disabled="readonly"><legend>手动动作</legend><div class="input-toolbar"><label>操作 <select v-model="form.kind" aria-label="手动操作"><option v-for="(name,key) in kinds" :value="key" :key="key">{{name}}</option></select></label><p>已选 {{selected.length}} 段：{{chosen.map(value=>`${value.date}/${value.line_code}/${value.model_code} ${value.quantity}片`).join('；')}}</p><el-button @click="selected=[]" :disabled="readonly">取消生产段选择</el-button></div>
          <div v-if="form.kind==='append_fill'" class="input-toolbar"><label>机种 <select v-model="form.model_code" aria-label="追加机种" @change="form.line_code='';fillDays=[];capacities()"><option value="">请选择</option><option v-for="value in options.models" :key="value.code" :value="value.code">{{value.code}} {{value.name}}</option></select></label><label>追加起日 <input type="date" v-model="form.start_date" aria-label="追加起日"></label><label>追加终日 <input type="date" v-model="form.end_date" aria-label="追加终日"></label><el-button @click="form.line_code='';capacities()" :disabled="readonly || capacityBusy">读取可生产线体</el-button><label>逐线选择 <select v-model="form.line_code" aria-label="追加线体" @change="capacities"><option value="">请选择</option><option v-for="value in fillLines" :key="value.code" :value="value.code">{{value.code}} {{value.name}}</option></select></label></div>
          <div v-if="form.kind==='append_fill' || form.kind==='split_segment'" class="input-toolbar"><label>{{form.kind==='split_segment'?'拆出数量':'新增总量'}} <input type="number" min="1" step="1" v-model.number="form.quantity" aria-label="操作数量" required></label><p v-if="form.continue_id">正在处理指定未分配记录，数量须与该记录一致。<el-button @click="form.continue_id=''" :disabled="readonly">改为新的追加</el-button></p></div>
          <div v-if="['move_segment','split_segment','copy_segments','overwrite_copy_segments'].includes(form.kind)" class="input-toolbar"><label>目标起始日 <input type="date" v-model="form.target_date" aria-label="目标起始日"></label><template v-if="['move_segment','split_segment'].includes(form.kind)"><label>目标线体 <select v-model="form.target_line" aria-label="目标线体"><option v-for="value in options.lines" :key="value.code" :value="value.code">{{value.code}}</option></select></label><label>插入位置（从0起）<input type="number" min="0" step="1" v-model.number="form.target_order" aria-label="目标位置"></label></template><template v-else><label v-for="value in sourceLines" :key="value">源 {{value}} 映射至 <select v-model="mapping[value]" :aria-label="`${value}目标线体`"><option v-for="target in options.lines" :key="target.code" :value="target.code">{{target.code}}</option></select></label></template></div>
          <el-alert v-if="form.kind==='overwrite_copy_segments'" title="覆盖复制会清空每个目标日期线体的已有生产段，再放入复制段。请核对完整预览。" type="warning" :closable="false" />
          <p v-if="optionError" role="alert">{{optionError}}</p><p v-if="capacityBusy" role="status">正在试算精确容量…</p>
          <table v-if="fillDays.length && form.kind==='append_fill'" class="input-table" aria-label="可追加数量"><thead><tr><th>日期</th><th>所选线体</th><th>最大可预排投入</th></tr></thead><tbody><tr v-for="value in fillDays" :key="value.date" :class="{'available-fill':value.quantity>0}"><td>{{value.date}}</td><td>{{value.line_code}}</td><td>{{value.quantity}}片</td></tr></tbody></table><p v-if="fillDays.length && form.kind==='append_fill'">容量试算不等于已确认可采用。完整动作预览另列物料、出货及保护状态。</p>
          <el-button native-type="submit" type="primary" :disabled="readonly">加入完整动作并预览</el-button>
        </fieldset>
      </form>
      <h3>保留的未分配投入</h3><p>余额不会自动换线、延日或舍弃；它与需求缺口分别保留。</p><table class="input-table" aria-label="未分配投入"><thead><tr><th>机种</th><th>数量</th><th>原因</th><th>处理</th></tr></thead><tbody><tr v-for="value in unallocated" :key="value.id"><td>{{value.model_code}}</td><td>{{value.quantity}}</td><td>{{value.reason}}</td><td><el-button :disabled="readonly" @click="continueInput(value)">继续选线</el-button><el-button :disabled="readonly" @click="discard(value.id)">明确舍弃</el-button></td></tr></tbody></table>
    </template>
  </section>
</template>
