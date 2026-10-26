<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import type { LineStateRecord } from '../lineStateTypes'
import type { useSharedPlan } from '../useSharedPlan'
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
const form=props.shared.workspace.lineStates
const options=ref<{lines:{code:string;name:string}[];recipes:{code:string;name:string}[]}>({lines:[],recipes:[]})
const error=ref('')
const records=computed(()=>props.shared.context.value?.line_states?.records ?? [])
const audit=computed(()=>props.shared.context.value?.line_states?.audit ?? [])
function beijing(value:string){return new Date(Date.parse(value)+8*3600000).toISOString().slice(0,19)}
function effective(){return `${form.effectiveAt.length===16 ? form.effectiveAt+':00' : form.effectiveAt}+08:00`}
function existing(line:string){
  const retained=form.expected[line]
  if(retained && Date.parse(retained.effective_at)===Date.parse(effective()))return retained
  return records.value.find(row=>row.line_code===line && Date.parse(row.effective_at)===Date.parse(effective()))
}
function remember(line:string){const row=existing(line);if(row)form.expected[line]={...row}}
function applyRecipe(){for(const code of form.selectedLines)form.recipes[code]=form.batchRecipe}
function correct(row:LineStateRecord){
  form.selectedLines=[row.line_code];form.effectiveAt=beijing(row.effective_at);form.source=row.source
  form.recipes[row.line_code]=row.recipe_code;form.expected[row.line_code]={...row};form.reason='';error.value=''
}
async function stage(){
  error.value=''
  if(!form.selectedLines.length){error.value='请选择需要确认的线体';return}
  if(!form.effectiveAt || !form.source.trim()){error.value='请填写状态对应时点与现场来源';return}
  if(form.selectedLines.some(code=>!form.recipes[code])){error.value='请为所选线体填写 Recipe';return}
  const changed=form.selectedLines.some(code=>{const old=existing(code);return old && (old.recipe_code!==form.recipes[code] || old.source!==form.source.trim())})
  if(changed && !form.reason.trim()){error.value='更正确认状态必须填写原因';return}
  props.shared.appendOperation({kind:'confirm_line_states',reason:form.reason.trim() || null,rows:form.selectedLines.map(code=>{
    const old=existing(code)
    return {line_code:code,recipe_code:form.recipes[code],effective_at:effective(),source:form.source.trim(),...(old ? {expected_record_id:old.id} : {})}
  })})
  await props.shared.check()
}
onMounted(async()=>{
  form.effectiveAt ||= `${props.shared.head.value?.time.window_start ?? ''}T08:00:00`
  try{options.value=await api('/inputs/options');for(const row of options.value.lines)form.recipes[row.code] ??=''}
  catch(e){error.value=e instanceof Error ? e.message : '无法读取线体和 Recipe'}
})
</script>

<template>
  <section aria-label="现场线体状态">
    <p>状态对应时点与录入时间分别保留。确认先进入本机动作，预览后共享保存或正式采用。</p>
    <div class="input-table-scroll"><table class="input-table" aria-label="现场线体状态记录"><thead><tr><th>线体</th><th>已确认状态与来源</th><th v-if="shared.editor.value">更正</th></tr></thead><tbody><tr v-for="row in records" :key="row.id"><td>{{row.line_code}}</td><td><strong>Recipe {{row.recipe_code}}</strong><br>状态对应 {{beijing(row.effective_at).replace('T',' ')}} 北京时间<br>来源 {{row.source}}<br>确认人 {{row.actor}}<br>录入时间 {{beijing(row.recorded_at).replace('T',' ')}} 北京时间</td><td v-if="shared.editor.value"><el-button :disabled="shared.readonly.value" :aria-label="`更正线体 ${row.line_code} 状态`" @click="correct(row)">更正</el-button></td></tr></tbody></table></div>
    <p v-if="!records.length">尚无现场线体状态确认。旧计划起点与计划推算保留原来源。</p>
    <form v-if="shared.editor.value" aria-label="批量线体状态确认" @submit.prevent="stage">
      <fieldset :disabled="shared.readonly.value" class="line-state-form"><legend>确认或更正现场状态</legend>
        <label>状态对应时点，北京时间 <input v-model="form.effectiveAt" type="datetime-local" step="1" aria-label="线体状态对应时点" required></label>
        <label>现场来源 <input v-model="form.source" aria-label="线体状态来源" placeholder="例如现场点检或交班记录" required></label>
        <div class="input-toolbar"><label>批量 Recipe <select v-model="form.batchRecipe" aria-label="批量 Recipe"><option value="">请选择</option><option v-for="recipe in options.recipes" :key="recipe.code" :value="recipe.code">{{recipe.code}} {{recipe.name}}</option></select></label><el-button :disabled="!form.batchRecipe || !form.selectedLines.length" @click="applyRecipe">应用到所选线体</el-button></div>
        <div class="input-table-scroll"><table class="input-table" aria-label="待确认线体"><thead><tr><th>选择</th><th>线体</th><th>Recipe</th></tr></thead><tbody><tr v-for="row in options.lines" :key="row.code"><td><input v-model="form.selectedLines" :value="row.code" type="checkbox" :aria-label="`确认线体 ${row.code}`" @change="remember(row.code)"></td><td>{{row.code}} {{row.name}}</td><td><select v-model="form.recipes[row.code]" :aria-label="`状态 Recipe ${row.code}`"><option value="">请选择</option><option v-for="recipe in options.recipes" :key="recipe.code" :value="recipe.code">{{recipe.code}}</option></select></td></tr></tbody></table></div>
        <label>更正原因 <textarea v-model="form.reason" aria-label="线体状态更正原因" placeholder="更正已确认值时填写" /></label>
        <p v-if="error" role="alert">{{error}}</p>
        <el-button native-type="submit" type="primary" :disabled="shared.readonly.value">加入状态确认并预览</el-button>
      </fieldset>
    </form>
    <details v-if="audit.length"><summary>状态确认与更正记录</summary><p v-for="row in audit" :key="row.action_id">{{beijing(row.recorded_at).replace('T',' ')}} · {{row.actor}} · {{row.reason || '现场确认'}} · {{row.changes.length}} 项状态变化</p></details>
  </section>
</template>
