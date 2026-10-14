<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, basePath, online, principal } from '../api'
import { categories, fieldNames, type CellValue, type InputRow, type InputSnapshot, type InputPreview } from '../inputFields'
const snapshot = ref<InputSnapshot | null>(null)
const selected = ref('recipes')
const category = computed(() => categories.find(c => c.kind === selected.value)!)
const rows = ref<InputRow[]>([])
const existing = ref<boolean[]>([])
const paste = ref('')
const preview = ref<InputPreview | null>(null)
const previewTables = computed(() => Object.entries(preview.value?.changes ?? {}).filter(([, changed]) => changed.length))
const busy = ref(false)
const error = ref('')
const status = ref('')
const editor = computed(() => principal.value?.roles.includes('editor') ?? false)
const editable = computed(() => editor.value && online.value && !busy.value)
const boolFields = ['active','allowed','holiday','enabled']
const refs: Record<string, string> = {recipe_code:'recipes',from_recipe:'recipes',to_recipe:'recipes',model_code:'models',line_code:'lines'}
function fieldName(field: string) { return field.split(',').map(f => fieldNames[f] ?? f).join('、') }
function invalidate() { preview.value = null; status.value = ''; error.value = '' }
function showRows() {
  rows.value = (snapshot.value?.[category.value.section][selected.value] ?? []).map(row => ({...row}))
  existing.value = rows.value.map(() => true)
  paste.value = ''
  invalidate()
}
async function load() {
  busy.value = true; error.value = ''
  try { snapshot.value = await api<InputSnapshot>('/inputs'); showRows() }
  catch (e) { error.value = e instanceof Error ? e.message : '无法读取资料' }
  finally { busy.value = false }
}
function add() {
  rows.value.push(Object.fromEntries(category.value.fields.map(f => [f, boolFields.includes(f) ? true : ''])))
  existing.value.push(false); invalidate()
}
function referenceOptions(field: string, current: CellValue) {
  const masterRows = snapshot.value?.masters[refs[field]!] ?? []
  return masterRows.filter(r => r.active === true || r.code === current)
}
function update(row: InputRow, field: string, value: string) {
  row[field] = boolFields.includes(field) ? value === 'true' : value
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
    <h1>输入资料维护</h1>
    <p>编辑、粘贴或导入 Excel 后，先预览整批，再确认提交。未提供的组合保持原值。任何错误都不会部分提交。</p>
    <p v-if="snapshot">当前资料版本 {{ snapshot.input_version }}。当月及后四个月的缺失月度参数自动复制上月值，已有值保持独立。</p>
    <div class="input-toolbar">
      <a :href="`${basePath}/api/inputs/template.xlsx`">下载标准 Excel 模板</a>
      <a v-if="snapshot" :href="`${basePath}/api/inputs/export.xlsx?version=${snapshot.input_version}`">导出当前资料 Excel</a>
      <el-button @click="load" :disabled="busy || !online">重新读取资料</el-button>
    </div>
    <el-alert v-if="!editor" title="当前为查看权限，可查询和导出资料" type="info" :closable="false" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <p v-if="busy" role="status">正在处理资料…</p>
    <p v-if="status" role="status">{{ status }}</p>
    <label>资料类别 <select aria-label="资料类别" v-model="selected" @change="showRows" :disabled="busy"><option v-for="item in categories" :key="item.kind" :value="item.kind">{{ item.title }}</option></select></label>
    <p>{{ category.note }}</p>
    <el-button @click="add" :disabled="!editable">新增一行</el-button>
    <div class="input-table-scroll">
      <table :aria-label="category.title" class="input-table">
        <thead><tr><th>行</th><th v-for="field in category.fields" :key="field">{{ fieldName(field) }}</th><th>操作</th></tr></thead>
        <tbody><tr v-for="(row, index) in rows" :key="index"><td>{{ index + 1 }}</td>
          <td v-for="field in category.fields" :key="field">
            <select v-if="boolFields.includes(field)" :aria-label="`${fieldName(field)} 第${index+1}行`" :value="String(row[field])" @change="update(row,field,($event.target as HTMLSelectElement).value)" :disabled="!editable"><option value="true">是</option><option value="false">否</option></select>
            <select v-else-if="refs[field]" :aria-label="`${fieldName(field)} 第${index+1}行`" :value="String(row[field] ?? '')" @change="update(row,field,($event.target as HTMLSelectElement).value)" :disabled="!editable || (existing[index] && category.keys.includes(field))"><option value="">请选择</option><option v-for="master in referenceOptions(field,row[field] ?? null)" :key="String(master.code)" :value="String(master.code)">{{ master.code }} · {{ master.name }}{{ master.active ? '' : ' (停用)' }}</option></select>
            <el-input v-else :aria-label="`${fieldName(field)} 第${index+1}行`" :model-value="String(row[field] ?? '')" @update:model-value="update(row,field,$event)" :disabled="!editable || (existing[index] && category.keys.includes(field))" :placeholder="field === 'percent' ? '95.50 表示 95.50%' : field === 'month' ? 'YYYY-MM' : field === 'date' ? 'YYYY-MM-DD' : ''" />
          </td>
          <td><el-button v-if="!existing[index]" @click="rows.splice(index,1); existing.splice(index,1); invalidate()" :disabled="!editable">撤回新增</el-button><span v-else>已存资料</span></td>
        </tr></tbody>
      </table>
    </div>
    <p v-if="rows.length === 0">暂无资料。缺失值保持未知，请明确录入；零值不会由空白自动生成。</p>
    <el-button type="primary" @click="previewTable" :disabled="!editable">预览表格变更</el-button>
    <div v-if="editor" class="input-import">
      <label for="input-paste">粘贴表格</label><p>首行请使用标准模板列名，列之间使用制表符：</p><code>{{ category.fields.join('　') }}</code>
      <el-input id="input-paste" aria-label="粘贴表格" type="textarea" :rows="5" v-model="paste" @input="invalidate" :disabled="!editable" />
      <el-button @click="previewPaste" :disabled="!editable || !paste">预览粘贴内容</el-button>
      <label class="excel-picker">选择 Excel 文件并预览 <input class="excel-file-input" type="file" aria-label="导入标准 Excel 并预览" accept=".xlsx" @change="importExcel" :disabled="!editable" /></label>
      <p>Excel 百分数列可填 95.50 或使用显示 95.50% 的百分比单元格。公式不接受。工作簿最多 10MB，整批最多 100000 行。</p>
    </div>
    <div v-if="preview" class="input-preview">
      <h2>整批预览</h2>
      <p v-if="preview.valid">预览通过，尚未提交</p><p v-else>发现 {{ preview.errors.length }} 个错误，整批未提交。</p>
      <table v-if="preview.errors.length" aria-label="导入错误" class="input-table"><thead><tr><th>工作表</th><th>源行</th><th>字段</th><th>原因</th></tr></thead><tbody><tr v-for="(item,index) in preview.errors" :key="index"><td>{{ categories.find(c=>c.kind === item.sheet)?.title ?? item.sheet }}</td><td>{{ item.row }}</td><td>{{ fieldName(item.field) }}</td><td>{{ item.reason }}</td></tr></tbody></table>
      <p v-if="preview.valid && previewTables.length === 0">本批没有资料行，当前资料保持原值。</p>
      <div v-for="[kind,changed] in previewTables" :key="kind"><h3>{{ categories.find(c=>c.kind === kind)?.title ?? kind }} ({{ changed.length }} 行)</h3><div class="input-table-scroll"><table :aria-label="`${kind} 预览`" class="input-table"><thead><tr><th v-for="field in categories.find(c=>c.kind === kind)?.fields" :key="field">{{ fieldName(field) }}</th></tr></thead><tbody><tr v-for="(row,index) in changed" :key="index"><td v-for="field in categories.find(c=>c.kind === kind)?.fields" :key="field">{{ typeof row[field] === 'boolean' ? (row[field] ? '是' : '否') : row[field] }}</td></tr></tbody></table></div></div>
    </div>
    <el-button v-if="editor" type="success" @click="apply" :disabled="!editable || !preview?.valid">确认提交整批</el-button>
  </section>
</template>
