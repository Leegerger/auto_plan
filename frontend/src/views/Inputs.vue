<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { onBeforeRouteUpdate, useRoute, type LocationQuery } from 'vue-router'
import { api, basePath, online, principal } from '../api'
import { categories, fieldName, type CellValue, type InputRow, type InputSnapshot, type InputPreview } from '../inputFields'
import { useSharedPlan } from '../useSharedPlan'
import { usePageLeaveGuard } from '../usePageLeaveGuard'
import InputPagination from '../components/InputPagination.vue'
import InputPreviewPanel from '../components/InputPreview.vue'
const shared=useSharedPlan()
const route=useRoute()
const snapshot = ref<InputSnapshot | null>(null)
const selected = ref('recipes')
interface DemandInputContext {model:string;week:string;month:string;confirmation:string;recipe:string;fromSchedule:boolean}
const demandContext=ref<DemandInputContext|null>(null)
const contextSummary=computed(()=>{
  const value=demandContext.value
  return value ? [value.model,value.week ? `需求周 ${value.week}` : '',value.month,value.confirmation==='material'?'物料确认':value.confirmation==='shipping'?'出货确认':''].filter(Boolean).join(' · ') : ''
})
const category = computed(() => categories.find(c => c.kind === selected.value)!)
const categorySections = computed(() => [
  {key:'masters',title:'主资料'},
  {key:'parameters',title:'参数'},
  {key:'calendar',title:'日历'},
  {key:'business',title:'确认'},
].map(section => ({...section,items:categories.filter(category => category.section===section.key)})))
const rows = ref<InputRow[]>([])
const existing = ref<boolean[]>([])
const changedRows = ref(new Set<number>())
const paste = ref('')
const preview = ref<InputPreview | null>(null)
const busy = ref(false)
const query = ref('')
const page = ref(1)
const pageSize = ref(20)
const table = ref<HTMLTableElement | null>(null)
const error = ref('')
const status = ref('')
const editor = computed(() => principal.value?.roles.includes('editor') ?? false)
const editable = computed(() => editor.value && online.value && !busy.value && !shared.unknown.value)
const hasPendingChanges = computed(() => changedRows.value.size > 0 || existing.value.some(saved => !saved) || !!paste.value.trim() || preview.value !== null)
const filteredRows = computed(() => {
  const needle=query.value.trim().toLocaleLowerCase()
  return rows.value.map((row, index) => ({ row, index })).filter(({ row }) =>
    matchesContext(row) && (!needle || Object.values(row).some(value => String(value ?? '').toLocaleLowerCase().includes(needle))))
})
const visibleRows = computed(() => filteredRows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
watch([query, pageSize], () => { page.value = 1 }, { flush: 'sync' })
watch(() => filteredRows.value.length, total => {
  page.value = Math.min(page.value, Math.max(1, Math.ceil(total / pageSize.value)))
}, { flush: 'sync' })
const choices: Record<string, {value:string;label:string}[]> = {kind:[{value:'material',label:'物料确认'},{value:'shipping',label:'出货确认'}],status:[{value:'allowed',label:'允许'},{value:'pending',label:'待确认'},{value:'forbidden',label:'整周跨周禁令'},{value:'revoked',label:'撤销／解除本批禁令'}]}
const boolFields = ['active','allowed','holiday','enabled']
const refs: Record<string, string> = {recipe_code:'recipes',from_recipe:'recipes',to_recipe:'recipes',model_code:'models',line_code:'lines'}
function applyRouteContext(query:LocationQuery) {
  const text=(key:string)=>typeof query[key]==='string' ? query[key] as string : ''
  const kind=text('category')
  if(categories.some(category=>category.kind===kind))selected.value=kind
  const model=snapshot.value?.masters.models?.find(row=>row.code===text('model'))
  const week=text('week'), date=new Date(`${week}T00:00:00Z`)
  const validWeek=Number.isFinite(date.getTime()) && date.toISOString().slice(0,10)===week && date.getUTCDay()===1
  const month=text('month')
  demandContext.value = text('from')==='schedule' ? {
    model: model ? String(model.code) : '', recipe:model ? String(model.recipe_code) : '',
    week:validWeek ? week : '',month:/^\d{4}-(0[1-9]|1[0-2])$/.test(month)?month:'',
    confirmation:kind==='confirmations' && ['material','shipping'].includes(text('confirmation'))?text('confirmation'):'',fromSchedule:true,
  } : null
}
function matchesContext(row:InputRow) {
  const value=demandContext.value
  if(!value)return true
  return ![['model_code',value.model],['week',value.week],['month',value.month],['kind',value.confirmation],['recipe_code',value.recipe]].some(([field,expected])=>
    expected && category.value.fields.includes(field!) && String(row[field!]??'')!==expected)
}
onBeforeRouteUpdate(async to=>{
  if(!await confirmDiscard())return false
  applyRouteContext(to.query);showRows()
})
function invalidate() { preview.value = null; status.value = ''; error.value = '' }
async function confirmDiscard() {
  if (!principal.value) return true
  if (busy.value) return false
  if (!hasPendingChanges.value) return true
  try {
    await ElMessageBox.confirm('尚有未提交的资料编辑或预览。舍弃后无法恢复。', '舍弃未提交资料变更？', {
      type: 'warning', confirmButtonText: '舍弃变更', cancelButtonText: '继续编辑',
      autofocus: false, closeOnClickModal: false,
    })
    return true
  } catch { return false }
}
async function selectCategory(kind: string) {
  if (kind === selected.value || !await confirmDiscard()) return
  selected.value = kind
  demandContext.value=null
  showRows()
}
function changeCategory(event: Event) {
  const select = event.target as HTMLSelectElement
  const kind = select.value
  select.value = selected.value
  void selectCategory(kind)
}
async function reload() { if (await confirmDiscard()) await load() }
function beforeUnload(event: BeforeUnloadEvent) {
  if (principal.value && (hasPendingChanges.value || busy.value)) {
    event.preventDefault()
    event.returnValue = ''
  }
}
watch([hasPendingChanges, busy], ([pending, processing]) => {
  if (pending || processing) window.addEventListener('beforeunload', beforeUnload)
  else window.removeEventListener('beforeunload', beforeUnload)
}, { flush: 'sync' })
usePageLeaveGuard(confirmDiscard)
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))
function showRows() {
  rows.value = (snapshot.value?.[category.value.section][selected.value] ?? []).map(row => ({...row}))
  existing.value = rows.value.map(() => true)
  changedRows.value.clear()
  query.value = ''
  page.value = 1
  paste.value = ''
  invalidate()
}
async function load() {
  busy.value = true; error.value = ''
  try { snapshot.value = await api<InputSnapshot>('/inputs'); applyRouteContext(route.query); showRows() }
  catch (e) { error.value = e instanceof Error ? e.message : '无法读取资料' }
  finally { busy.value = false }
}
async function add() {
  query.value = ''
  const defaults:Record<string,string>={model_code:demandContext.value?.model??'',week:demandContext.value?.week??'',kind:demandContext.value?.confirmation??'',month:demandContext.value?.month??'',recipe_code:demandContext.value?.recipe??''}
  rows.value.push(Object.fromEntries(category.value.fields.map(f => [f, boolFields.includes(f) ? true : defaults[f]??''])))
  existing.value.push(false); invalidate()
  page.value = Math.max(1,Math.ceil(filteredRows.value.length / pageSize.value))
  await nextTick()
  table.value?.querySelector<HTMLInputElement | HTMLSelectElement>('tbody tr:last-child input, tbody tr:last-child select')?.focus()
}
function referenceOptions(field: string, current: CellValue) {
  const masterRows = snapshot.value?.masters[refs[field]!] ?? []
  return masterRows.filter(r => r.active === true || r.code === current)
}
function update(index: number, field: string, value: string) {
  const row = rows.value[index]!
  row[field] = boolFields.includes(field) ? value === 'true' : value
  if (existing.value[index]) {
    const original = snapshot.value![category.value.section][selected.value]![index]!
    if (category.value.fields.every(key => String(row[key] ?? '') === String(original[key] ?? ''))) changedRows.value.delete(index)
    else changedRows.value.add(index)
  }
  invalidate()
}
async function previewTable() {
  if (!snapshot.value) return
  busy.value = true; error.value = ''; status.value = ''; preview.value = null
  try { preview.value = await api<InputPreview>('/inputs/preview', {method:'POST',body:JSON.stringify({base_version:snapshot.value.input_version,changes:{[selected.value]:rows.value}})}) }
  catch (e) { error.value = e instanceof Error ? e.message : '预览失败' }
  finally { busy.value = false }
}
async function previewPaste() {
  if (!snapshot.value) return
  busy.value = true; error.value = ''; status.value = ''; preview.value = null
  try { preview.value = await api<InputPreview>('/inputs/paste/preview', {method:'POST',body:JSON.stringify({base_version:snapshot.value.input_version,kind:selected.value,text:paste.value})}) }
  catch (e) { error.value = e instanceof Error ? e.message : '预览失败' }
  finally { busy.value = false }
}
async function importExcel(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !snapshot.value) return
  busy.value = true; error.value = ''; status.value = ''; preview.value = null
  try { preview.value = await api<InputPreview>(`/inputs/excel/preview?base_version=${snapshot.value.input_version}`, {method:'POST',body:file,headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}}) }
  catch (e) { error.value = e instanceof Error ? e.message : '导入失败' }
  finally { busy.value = false; input.value = '' }
}
async function apply() {
  if (!preview.value?.valid) return
  const payload = {base_version:preview.value.base_version,changes:preview.value.changes}
  busy.value = true; error.value = ''; status.value = ''
  try {
    const saved = await api<InputSnapshot>('/inputs/apply', {method:'POST',body:JSON.stringify(payload)})
    snapshot.value = saved; showRows(); status.value = `已提交资料版本 ${saved.input_version}`
  } catch (e) { error.value = e instanceof Error ? e.message : '提交失败'; preview.value = null }
  finally { busy.value = false }
}
onMounted(load)
</script>
<template>
  <section v-if="principal">
    <header class="page-heading">
      <div><p class="eyebrow">主资料、参数、日历与确认</p><h1>基础资料</h1><p class="page-subtitle">编辑、粘贴或导入后先预览整批，再确认提交。任何错误都不会部分提交。</p></div>
      <span v-if="snapshot" class="section-count">资料版本 {{ snapshot.input_version }}</span>
    </header>
    <section v-if="demandContext" aria-label="需求资料定位" class="demand-input-context">
      <span>{{contextSummary}}</span>
      <span>定位仅筛选显示，整批预览仍包含其他已存资料。</span>
      <button type="button" @click="demandContext=null">取消定位筛选</button>
      <router-link v-if="demandContext.fromSchedule" to="/schedule">返回计划工作区</router-link>
    </section>
    <div class="input-toolbar">
      <a :href="`${basePath}/api/inputs/template.xlsx`">下载标准 Excel 模板</a>
      <a v-if="snapshot" :href="`${basePath}/api/inputs/export.xlsx?version=${snapshot.input_version}`">导出当前资料 Excel</a>
      <el-button @click="reload" :disabled="busy || !online">重新读取资料</el-button>
    </div>
    <el-alert v-if="!editor" title="当前为查看权限，可查询和导出资料" type="info" :closable="false" />
    <el-alert v-if="shared.unknown.value" title="共享计划保存结果待核实，资料编辑当前只读。" type="warning" :closable="false" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <p v-if="busy" role="status">正在处理资料…</p>
    <p v-if="status" role="status">{{ status }}</p>
    <div class="data-layout">
      <aside class="category-nav" aria-label="资料分类">
        <label>资料类别 <select aria-label="资料类别" :value="selected" @change="changeCategory" :disabled="busy"><option v-for="item in categories" :key="item.kind" :value="item.kind">{{ item.title }}</option></select></label>
        <section v-for="section in categorySections" :key="section.key"><h3>{{ section.title }}</h3><button v-for="item in section.items" :key="item.kind" type="button" :class="{active:item.kind===selected}" :disabled="busy" @click="selectCategory(item.kind)">{{ item.title }}</button></section>
      </aside>
      <div class="category-content">
    <div class="section-heading"><div><p class="eyebrow">当前资料类别</p><h2>{{ category.title }}</h2></div><div class="heading-actions"><el-input v-model="query" aria-label="搜索资料" placeholder="搜索资料" clearable /><el-button @click="add" :disabled="!editable">新增一行</el-button></div></div>
    <p class="category-note">{{ category.note }}</p>
    <InputPagination v-model:page="page" v-model:page-size="pageSize" :total="filteredRows.length" label="基础资料上方分页" :disabled="busy" />
    <div class="input-table-scroll">
      <table ref="table" :aria-label="category.title" class="input-table">
        <thead><tr><th>行</th><th v-for="field in category.fields" :key="field">{{ fieldName(field) }}</th><th>操作</th></tr></thead>
        <tbody><tr v-for="{ row, index } in visibleRows" :key="index"><td>{{ index + 1 }}</td>
          <td v-for="field in category.fields" :key="field">
            <select v-if="boolFields.includes(field)" :aria-label="`${fieldName(field)} 第${index+1}行`" :value="String(row[field])" @change="update(index,field,($event.target as HTMLSelectElement).value)" :disabled="!editable"><option value="true">是</option><option value="false">否</option></select>
            <select v-else-if="refs[field]" :aria-label="`${fieldName(field)} 第${index+1}行`" :value="String(row[field] ?? '')" @change="update(index,field,($event.target as HTMLSelectElement).value)" :disabled="!editable || (existing[index] && category.keys.includes(field))"><option value="">请选择</option><option v-for="master in referenceOptions(field,row[field] ?? null)" :key="String(master.code)" :value="String(master.code)">{{ master.code }} · {{ master.name }}{{ master.active ? '' : ' (停用)' }}</option></select>
            <select v-else-if="choices[field]" :aria-label="`${fieldName(field)} 第${index+1}行`" :value="String(row[field] ?? '')" @change="update(index,field,($event.target as HTMLSelectElement).value)" :disabled="!editable || (existing[index] && category.keys.includes(field))"><option value="">请选择</option><option v-for="choice in choices[field]" :key="choice.value" :value="choice.value">{{ choice.label }}</option></select>
            <el-input v-else :aria-label="`${fieldName(field)} 第${index+1}行`" :model-value="String(row[field] ?? '')" @update:model-value="update(index,field,$event)" :disabled="!editable || (existing[index] && category.keys.includes(field))" :placeholder="field === 'percent' ? '95.50 表示 95.50%' : field === 'month' ? 'YYYY-MM' : ['date','week'].includes(field) ? 'YYYY-MM-DD' : ''" />
          </td>
          <td><el-button v-if="!existing[index]" @click="rows.splice(index,1); existing.splice(index,1); invalidate()" :disabled="!editable">撤回新增</el-button><span v-else>已存资料</span></td>
        </tr></tbody>
      </table>
    </div>
    <InputPagination v-model:page="page" v-model:page-size="pageSize" :total="filteredRows.length" label="基础资料下方分页" :disabled="busy" />
    <p v-if="rows.length === 0">暂无资料。缺失值保持未知，请明确录入；零值不会由空白自动生成。</p>
    <p v-else-if="filteredRows.length === 0" class="empty-inline">没有符合搜索条件的资料。</p>
    <el-button type="primary" @click="previewTable" :disabled="!editable">预览表格变更</el-button>
    <div v-if="editor" class="input-import">
      <label for="input-paste">粘贴表格</label><p>首行请使用标准模板列名，列之间使用制表符：</p><code>{{ category.fields.join('　') }}</code>
      <el-input id="input-paste" aria-label="粘贴表格" type="textarea" :rows="5" v-model="paste" @input="invalidate" :disabled="!editable" />
      <el-button @click="previewPaste" :disabled="!editable || !paste">预览粘贴内容</el-button>
      <label class="excel-picker">选择 Excel 文件并预览 <input class="excel-file-input" type="file" aria-label="导入标准 Excel 并预览" accept=".xlsx" @change="importExcel" :disabled="!editable" /></label>
      <p>Excel 百分数列可填 95.50 或使用显示 95.50% 的百分比单元格。公式不接受。工作簿最多 10MB，整批最多 100000 行。</p>
    </div>
    <InputPreviewPanel v-if="preview" :preview="preview" />
    <el-button v-if="editor" type="success" @click="apply" :disabled="!editable || !preview?.valid">确认提交整批</el-button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.demand-input-context{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;padding:10px 12px;margin-bottom:12px;border:1px solid var(--hairline);border-radius:6px;font-size:12px;background:var(--canvas)}
.demand-input-context>span:first-child{font-weight:600}.demand-input-context>span:nth-child(2){color:var(--muted)}
.demand-input-context>a{margin-left:auto}
</style>
