<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, ApiError, basePath, online, principal } from '../api'
import { categories, fieldNames, type InputError } from '../inputFields'
import { useSharedPlan } from '../useSharedPlan'

type Row=Record<string,string|number|boolean|null>
interface Preview {valid:boolean;errors:InputError[];payload?:Record<string,unknown>;plan?:{segments:Row[]};evaluation?:{executable:boolean}}
interface Head {revision:number;input_version:number;latest_adoption:number}
interface Pending {operation_id:string;mode:'initialization'}

const shared=useSharedPlan()
const editor=computed(()=>principal.value?.roles.includes('editor')??false)
const busy=ref(false),error=ref(''),notice=ref(''),storageWarning=ref('')
const head=ref<Head>({revision:0,input_version:0,latest_adoption:0}),result=ref<Preview|null>(null)
const kind=ref('segments'),paste=ref(''),complete=ref(false),cutoff=ref('')
const schemas:Record<string,string[]>={segments:['id','date','line_code','order','model_code','quantity'],demands:['model_code','week','week_label','quantity'],coverage:['start_date','end_date','line_codes','model_codes','blanks_are_zero'],reset:['batch_id','production_week','cutoff','model_code','wip','net_balance'],execution_facts:['date','line_code','model_code','quantity'],execution_coverage:['start_date','end_date','line_codes','model_codes','blanks_are_zero'],initial_states:['line_code','recipe_code'],...Object.fromEntries(categories.map(category=>[category.kind,category.fields]))}
const titles:Record<string,string>={segments:'现行计划生产段',demands:'完整十五周需求',coverage:'旧计划覆盖范围',reset:'外部周一快照',execution_facts:'累计执行事实',execution_coverage:'已确认执行事实范围',initial_states:'线体起始 Recipe',...Object.fromEntries(categories.map(category=>[category.kind,category.title]))}
const labels:Record<string,string>={...fieldNames,id:'段编号',quantity:'数量 (片)',order:'人工日内顺序',week_label:'周别',start_date:'覆盖起日',end_date:'覆盖终日',line_codes:'线体编码列表 (以 | 分隔)',model_codes:'机种编码列表 (以 | 分隔)',blanks_are_zero:'覆盖内空白为零 (true/false)',production_week:'所属周周一',cutoff:'周一08:00截止时点 (含时区)',wip:'在制品 (片)',net_balance:'净余额 (可为负)'}
const tables=ref<Record<string,Row[]>>(Object.fromEntries(Object.keys(schemas).map(key=>[key,[]])))
const columns=computed(()=>schemas[kind.value]??[])
const rows=computed(()=>tables.value[kind.value]??[])
const outstanding=ref<Pending|null>(null)
const storageKey=computed(()=>`auto-plan-import-pending:${basePath}:${principal.value?.user_id}`)
function remember(value:Pending|null){
  outstanding.value=value
  try{if(value)localStorage.setItem(storageKey.value,JSON.stringify(value));else localStorage.removeItem(storageKey.value)}
  catch{storageWarning.value='浏览器无法保存核实编号。仍可提交到服务器；结果未知时请保留此页并核实。'}
}
function changed(){result.value=null;notice.value=''}
function showError(value:unknown){
  error.value=value instanceof Error?value.message:'操作失败'
  if(value instanceof ApiError&&Array.isArray(value.data.errors))result.value={valid:false,errors:value.data.errors as InputError[]}
}
async function load(){head.value=await api('/plans/current')}
onMounted(async()=>{
  try{
    const raw=localStorage.getItem(storageKey.value)
    if(raw){const value=JSON.parse(raw);if(typeof value.operation_id==='string'&&value.mode==='initialization')outstanding.value=value}
  }catch{storageWarning.value='浏览器无法读取本地核实编号。请保留当前页面完成提交和核实。'}
  try{await load()}catch(e){showError(e)}
})
function add(){rows.value.push(Object.fromEntries(columns.value.map(key=>[key,''])));changed()}
function tableText(name:string,entries:Row[]){return [schemas[name]!.join('\t'),...entries.map(row=>schemas[name]!.map(key=>String(row[key]??'')).join('\t'))].join('\n')}
async function preview(){
  busy.value=true;error.value='';notice.value=''
  try{
    const texts=Object.fromEntries(Object.keys(schemas).map(key=>[key,tableText(key,tables.value[key]??[])]))
    texts.settings=`complete_weekly_demands\texecution_cutoff\n${complete.value}\t${cutoff.value}`
    result.value=await api('/initialization/paste/preview',{method:'POST',body:JSON.stringify({base_revision:head.value.revision,input_version:head.value.input_version,latest_adoption:head.value.latest_adoption,tables:texts})})
  }catch(e){showError(e)}finally{busy.value=false}
}
async function previewPaste(){
  busy.value=true;error.value=''
  try{
    const lines=paste.value.trimEnd().split('\n'),headers=lines.shift()?.split('\t')??[]
    if(headers.join('\t')!==schemas[kind.value]!.join('\t'))throw new Error('首行列名须与当前表格模板一致')
    tables.value[kind.value]=lines.filter(Boolean).map(line=>Object.fromEntries(line.split('\t').map((value,index)=>[headers[index]!,value])))
    changed();await preview()
  }catch(e){showError(e)}finally{busy.value=false}
}
async function upload(event:Event){
  const input=event.target as HTMLInputElement,file=input.files?.[0]
  if(!file)return
  busy.value=true;error.value=''
  try{result.value=await api(`/initialization/excel/preview?base_revision=${head.value.revision}&input_version=${head.value.input_version}&latest_adoption=${head.value.latest_adoption}`,{method:'POST',headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},body:file})}
  catch(e){showError(e)}finally{busy.value=false;input.value=''}
}
async function apply(){
  if(!result.value?.valid)return
  busy.value=true;error.value=''
  const pending:Pending={operation_id:crypto.randomUUID(),mode:'initialization'}
  try{
    remember(pending)
    await api('/initialization/apply',{method:'POST',body:JSON.stringify({...(result.value.payload??{}),operation_id:pending.operation_id})})
    remember(null);notice.value='首次接入已保存为待处理共享草稿。请在计划工作区检查后明确采用。';result.value=null;await load();await shared.refresh()
  }catch(e){showError(e);if(e instanceof ApiError&&((e.status>=400&&e.status<500&&e.status!==408)||e.data.state==='busy'))remember(null)}finally{busy.value=false}
}
async function verify(){
  if(!outstanding.value)return
  busy.value=true
  try{
    const answer=await api<{status:string}>('/plans/verify',{method:'POST',body:JSON.stringify({operation_id:outstanding.value.operation_id})})
    notice.value=answer.status==='COMMITTED'?'前次导入已提交，已重新读取。':'前次导入已作废，可重新预览后提交。'
    remember(null);await load();await shared.refresh()
  }catch(e){showError(e)}finally{busy.value=false}
}
</script>

<template>
  <section>
    <p class="eyebrow">首次接入</p><h1>首次接入</h1>
    <p>生产日以北京时间08:00分界，需求周从周一08:00开始，共十五周。需求文件归属日期以北京时间00:00分界。</p>
    <el-alert v-if="storageWarning" :title="storageWarning" type="warning" :closable="false"/><el-alert v-if="error" :title="error" type="error" :closable="false"/><el-alert v-if="notice" :title="notice" type="success" :closable="false"/>
    <el-alert v-if="shared.unknown.value" title="共享计划保存结果待核实，当前不能提交新的导入。" type="warning" :closable="false"/>
    <el-alert v-if="outstanding" title="前次导入结果待核实，当前只读。核实完成后才能继续提交。" type="warning" :closable="false"/><el-button v-if="outstanding" @click="verify" :disabled="busy||!online">核实前次导入</el-button>
    <div><p>现行计划已在外部完成本周重置，导入后保持原数量。本周不再次抵扣净余额，也不能更正本周重置。须提供完整十五周需求，包含已满足部分。旧计划覆盖内空白是否为零必须明确。</p><el-alert v-if="head.revision" title="已有共享计划，首次接入已关闭。后续请维护输入资料或导入需求变更。" type="info" :closable="false"/></div>
    <fieldset :disabled="!editor||busy||!online||!!outstanding||shared.unknown.value||head.revision>0">
      <p><a :href="`${basePath}/api/initialization/template.xlsx`">下载标准 Excel 模板</a> <label>上传标准工作簿并预览 <input type="file" accept=".xlsx" @change="upload"/></label></p>
      <label>编辑资料类别 <el-select v-model="kind"><el-option v-for="(title,key) in titles" :key="key" :label="title" :value="key"/></el-select></label><p>编码须与主资料一致。可先在输入资料维护主资料，也可在同一初始化模板中一起填写。</p><el-checkbox v-model="complete" @change="changed">确认完整整周需求包含已满足部分，零需求已显式填写</el-checkbox><label>已确认执行事实截止时点 <el-input v-model="cutoff" placeholder="2026-10-14T08:00:00+08:00" @input="changed"/></label>
      <el-button @click="add">添加一行</el-button>
      <div class="table-scroll"><table><thead><tr><th v-for="column in columns" :key="column">{{labels[column]??column}}</th><th>操作</th></tr></thead><tbody><tr v-for="(row,index) in rows" :key="index"><td v-for="column in columns" :key="column"><input v-model="row[column]" :aria-label="`${labels[column]??column} 第${index+1}行`" @input="changed"/></td><td><el-button @click="rows.splice(index,1);changed()">移除此行</el-button></td></tr></tbody></table></div>
      <p>粘贴带标准列名的制表符表格：</p><el-input v-model="paste" type="textarea" :rows="4"/><el-button @click="previewPaste">读取粘贴并预览</el-button><el-button @click="preview">预览全部资料</el-button><el-button type="primary" :disabled="!result?.valid" @click="apply">确认导入待处理草稿</el-button>
    </fieldset>
    <div v-if="result"><p role="status">{{result.valid?'预览通过，尚未写入。':'预览未通过，原资料保持。'}}</p><el-table v-if="result.errors.length" :data="result.errors"><el-table-column prop="sheet" label="工作表"/><el-table-column prop="row" label="行"/><el-table-column prop="field" label="字段"/><el-table-column prop="reason" label="原因"/></el-table><p v-if="result.plan">导入生产段 {{result.plan.segments.length}} 段。服务器已生成精确工时证书。{{result.evaluation?.executable?'规则核算可执行。':'仍有待处理事项，导入后检查共享工作区。'}}</p></div>
  </section>
</template>

<style scoped>fieldset{border:0;padding:1rem 0}label{display:block;margin:1rem 0}.table-scroll{overflow:auto}th,td{padding:.4rem}td input{width:150px;min-height:30px}a{margin-right:1rem}</style>
