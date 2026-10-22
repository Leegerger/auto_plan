<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, ApiError, basePath, online, principal } from '../api'
import type { InputError } from '../inputFields'
import { useSharedPlan } from '../useSharedPlan'

type Row=Record<string,string|number|boolean|null>
interface Preview {valid:boolean;errors:InputError[];payload?:Record<string,unknown>;evaluation?:{weekly_results:Row[]};demands?:Row[]}
interface Pending {operation_id:string;mode:'demands'}
interface Head {revision:number;input_version:number;latest_adoption:number}
const shared=useSharedPlan()
const editor=computed(()=>principal.value?.roles.includes('editor')??false)
const rows=ref<Row[]>([]),paste=ref(''),attribution=ref(''),result=ref<Preview|null>(null)
const files=ref<Array<{file_id:string;attribution_date:string;version:number;effective:boolean;rows:Row[]}>>([])
const outstanding=ref<Pending|null>(null),busy=ref(false),error=ref(''),notice=ref(''),storageWarning=ref('')
const head=ref<Head>({revision:0,input_version:0,latest_adoption:0})
const storageKey=computed(()=>`auto-plan-import-pending:${basePath}:${principal.value?.user_id}`)
const columns=['model_code','week','week_label','delta']
const labels:Record<string,string>={model_code:'机种编码',week:'需求周周一',week_label:'周别',delta:'需求增减量'}
function changed(){result.value=null;notice.value=''}
function remember(value:Pending|null){
  outstanding.value=value
  try{if(value)localStorage.setItem(storageKey.value,JSON.stringify(value));else localStorage.removeItem(storageKey.value)}
  catch{storageWarning.value='浏览器无法保存核实编号。仍可提交到服务器；结果未知时请保留此页并核实。'}
}
function showError(value:unknown){
  error.value=value instanceof Error?value.message:'操作失败'
  if(value instanceof ApiError&&Array.isArray(value.data.errors))result.value={valid:false,errors:value.data.errors as InputError[]}
}
async function load(){
  head.value=await api<Head>('/plans/current')
  if(!head.value.revision){files.value=[];return}
  files.value=(await api<{files:typeof files.value}>('/demands/files')).files
}
onMounted(async()=>{
  try{
    const raw=localStorage.getItem(storageKey.value)
    if(raw){const value=JSON.parse(raw);if(typeof value.operation_id==='string'&&value.mode==='demands')outstanding.value=value}
  }catch{storageWarning.value='浏览器无法读取本地核实编号。请保留当前页面完成提交和核实。'}
  try{await load()}catch(e){showError(e)}
})
function add(){rows.value.push({model_code:'',week:'',week_label:'',delta:''});changed()}
async function preview(){
  busy.value=true;error.value='';notice.value=''
  try{await load();result.value=await api('/demands/preview',{method:'POST',body:JSON.stringify({input_version:head.value.input_version,attribution_date:attribution.value||null,rows:rows.value})})}
  catch(e){showError(e)}finally{busy.value=false}
}
async function previewPaste(){
  busy.value=true;error.value='';notice.value=''
  try{await load();result.value=await api('/demands/paste/preview',{method:'POST',body:JSON.stringify({input_version:head.value.input_version,attribution_date:attribution.value||null,text:paste.value})})}
  catch(e){showError(e)}finally{busy.value=false}
}
async function upload(event:Event){
  const input=event.target as HTMLInputElement,file=input.files?.[0]
  if(!file)return
  busy.value=true;error.value='';notice.value=''
  try{
    await load()
    const attributionQuery=attribution.value?`&attribution_date=${attribution.value}`:''
    result.value=await api(`/demands/excel/preview?input_version=${head.value.input_version}${attributionQuery}`,{method:'POST',headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},body:file})
  }catch(e){showError(e)}finally{busy.value=false;input.value=''}
}
async function apply(){
  if(!result.value?.valid)return
  busy.value=true;error.value=''
  const pending:Pending={operation_id:crypto.randomUUID(),mode:'demands'}
  try{
    remember(pending)
    await api('/demands/apply',{method:'POST',body:JSON.stringify({...(result.value.payload??{}),operation_id:pending.operation_id})})
    remember(null);notice.value='整日需求文件已替换，旧版本保留。';result.value=null;await shared.load();await load()
  }catch(e){showError(e);if(e instanceof ApiError&&((e.status>=400&&e.status<500&&e.status!==408)||e.data.state==='busy'))remember(null)}finally{busy.value=false}
}
async function verify(){
  if(!outstanding.value)return
  busy.value=true
  try{
    const answer=await api<{status:string}>('/demands/verify',{method:'POST',body:JSON.stringify({operation_id:outstanding.value.operation_id})})
    notice.value=answer.status==='COMMITTED'?'前次导入已提交，已重新读取。':'前次导入已作废，可重新预览后提交。'
    remember(null);await shared.load();await load()
  }catch(e){showError(e)}finally{busy.value=false}
}
</script>

<template>
  <section class="demand-change-panel">
    <el-alert v-if="storageWarning" :title="storageWarning" type="warning" :closable="false"/>
    <el-alert v-if="shared.unknown.value" title="共享计划保存结果待核实，当前不能提交新的需求变更。" type="warning" :closable="false"/>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <el-alert v-if="notice" :title="notice" type="success" :closable="false"/>
    <el-alert v-if="outstanding" title="前次导入结果待核实，当前只读。核实完成后才能继续提交。" type="warning" :closable="false"/>
    <el-button v-if="outstanding" @click="verify" :disabled="busy||!online">核实前次导入</el-button>
    <p>同日新文件整份替换旧文件贡献，省略旧行即撤回。重复内容的独立行分别计入。历史修订会重放后来日期，每一步不得负数。</p>
    <label>变更归属日期 <el-input v-model="attribution" placeholder="YYYY-MM-DD，留空为北京今天" @input="changed"/></label>
    <fieldset :disabled="!editor||busy||!online||!!outstanding||shared.unknown.value">
      <p><a :href="`${basePath}/api/demands/template.xlsx`">下载标准 Excel 模板</a> <label>上传标准工作簿并预览 <input type="file" accept=".xlsx" @change="upload"/></label></p>
      <el-button @click="add">添加一行</el-button>
      <div class="table-scroll"><table><thead><tr><th v-for="column in columns" :key="column">{{labels[column]}}</th><th>操作</th></tr></thead><tbody><tr v-for="(row,index) in rows" :key="index"><td v-for="column in columns" :key="column"><input v-model="row[column]" :aria-label="`${labels[column]} 第${index+1}行`" @input="changed"/></td><td><el-button @click="rows.splice(index,1);changed()">移除此行</el-button></td></tr></tbody></table></div>
      <p>粘贴带标准列名的制表符表格：</p><el-input v-model="paste" type="textarea" :rows="4"/><el-button @click="previewPaste">读取粘贴并预览</el-button><el-button @click="preview">预览全部资料</el-button><el-button type="primary" :disabled="!result?.valid" @click="apply">确认替换整日需求文件</el-button>
    </fieldset>
    <div v-if="result"><p role="status">{{result.valid?'预览通过，尚未写入。':'预览未通过，原资料保持。'}}</p><el-table v-if="result.errors.length" :data="result.errors"><el-table-column prop="sheet" label="工作表"/><el-table-column prop="row" label="行"/><el-table-column prop="field" label="字段"/><el-table-column prop="reason" label="原因"/></el-table><el-table v-if="result.evaluation" :data="result.evaluation.weekly_results"><el-table-column prop="model_code" label="机种"/><el-table-column prop="week" label="需求周周一"/><el-table-column prop="demand" label="完整需求"/><el-table-column prop="gap" label="未补足缺口"/><el-table-column prop="on_time_rate_display" label="按时满足率"/></el-table><el-table v-if="result.demands" :data="result.demands"><el-table-column prop="model_code" label="机种"/><el-table-column prop="week" label="需求周周一"/><el-table-column prop="quantity" label="完整需求片数"/></el-table></div>
    <details v-if="files.length"><summary>需求文件版本和独立行</summary><div v-for="file in files" :key="file.file_id"><h3>{{file.attribution_date}} 第{{file.version}}版 {{file.effective?'当前有效':'已被替换'}}</h3><el-table :data="file.rows"><el-table-column prop="row_id" label="独立行编号"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="week" label="需求周"/><el-table-column prop="delta" label="增减片数"/></el-table></div></details>
  </section>
</template>

<style scoped>fieldset{border:0;padding:1rem 0}label{display:block;margin:1rem 0}.table-scroll{overflow:auto}th,td{padding:.4rem}td input{width:150px;min-height:30px}a{margin-right:1rem}</style>
