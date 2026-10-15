<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, online, principal } from '../api'
import ExactHours from '../components/ExactHours.vue'
import OutputResults from '../components/OutputResults.vue'
import type { OutputEvaluation } from '../outputTypes'
interface Master { code: string; name: string; active: boolean; recipe_code?: string }
interface Inputs { input_version: number; masters: { models: Master[]; recipes: Master[]; lines: Master[] } }
interface Segment { id: string; date: string; line_code: string; order: string | number; model_code: string; quantity: string | number; hours: string }
interface Allocation { date: string; order: string | number; hours: string; idle_hours: string }
interface Switch { id: string; line_code: string; from_recipe: string; to_recipe: string; allocations: Allocation[] }
interface Idle { date: string; line_code: string; hours: string }
interface Plan { segments: Segment[]; switches: Switch[]; initial_states: { line_code: string; recipe_code: string | null }[]; ordinary_idle: Idle[] }
interface Hours { exact: string; display: string }
interface Issue { code: string; reason: string; severity: string; date: string | null; line_code: string | null; model_code: string | null; switch_id: string | null; demand_week:string|null; related_batch_ids:string[]; line_codes:string[] }
interface Recipe { recipe_code: string; quantity: number; hours: Hours; full_day_capacity: number; limit: number; capacity_state: string }
interface Day { date: string; line_code: string; available_hours: number; production_hours: Hours; switch_hours: Hours; occupied_hours: Hours; recipes: Recipe[]; ending_recipe: string | null }
interface Evaluation extends OutputEvaluation { capacity_valid: boolean; computation_complete: boolean; issues: Issue[]; days: Day[]; switch_count: number; switches: { id: string; duration_state: string; total_hours: Hours; idle_hours: Hours; extra_hours: Hours }[] }
const inputs = ref<Inputs | null>(null)
const selectedDate = ref(new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(Date.now() - 8 * 3600000)))
const selectedLine = ref('')
const plan = ref<Plan>({ segments: [], switches: [], initial_states: [], ordinary_idle: [] })
const result = ref<Evaluation | null>(null)
const error = ref('')
const message = ref('')
const busy = ref(false)
const canEdit = computed(() => online.value && !busy.value && principal.value?.roles.includes('editor'))
const activeModels = computed(() => inputs.value?.masters.models.filter(row => row.active) ?? [])
const selectedInitial = computed({
  get: () => plan.value.initial_states.find(row => row.line_code === selectedLine.value)?.recipe_code ?? '',
  set: (recipe: string) => {
    plan.value.initial_states = plan.value.initial_states.filter(row => row.line_code !== selectedLine.value)
    if (recipe) plan.value.initial_states.push({ line_code: selectedLine.value, recipe_code: recipe })
  },
})
watch(plan, () => { result.value = null; message.value = '' }, { deep: true, flush: 'sync' })
async function load() {
  busy.value = true; error.value = ''
  try { inputs.value = await api<Inputs>('/inputs'); selectedLine.value ||= inputs.value.masters.lines.find(row => row.active)?.code ?? '' }
  catch (e) { error.value = e instanceof Error ? e.message : '读取资料失败' }
  finally { busy.value = false }
}
function nextOrder() {
  const orders = [...plan.value.segments.filter(row => row.date === selectedDate.value && row.line_code === selectedLine.value).map(row => Number(row.order)),
    ...plan.value.switches.filter(row => row.line_code === selectedLine.value).flatMap(row => row.allocations.filter(a => a.date === selectedDate.value).map(a => Number(a.order)))]
  return orders.length ? Math.max(...orders) + 1 : 0
}
function addSegment() {
  plan.value.segments.push({ id: crypto.randomUUID(), date: selectedDate.value, line_code: selectedLine.value, order: nextOrder(), model_code: activeModels.value[0]?.code ?? '', quantity: '0', hours: '0' })
}
function addSwitch() {
  plan.value.switches.push({ id: crypto.randomUUID(), line_code: selectedLine.value,
    from_recipe: inputs.value?.masters.recipes[0]?.code ?? '', to_recipe: inputs.value?.masters.recipes[1]?.code ?? '',
    allocations: [{ date: selectedDate.value, order: nextOrder(), hours: '0', idle_hours: '0' }] })
}
async function preview(construct = false) {
  if (!inputs.value) return
  busy.value = true; error.value = ''; result.value = null; message.value = ''
  try {
    if (construct) {
      const source = { segments: plan.value.segments.map(({ hours, ...row }) => row), initial_states: plan.value.initial_states }
      const response = await api<{ plan: Plan | null; evaluation: Evaluation | null; issues: Issue[] }>('/plans/certificate/preview', {
        method: 'POST', body: JSON.stringify({ snapshot: { inputs: inputs.value, plan: source } }),
      })
      if (response.plan && response.evaluation) { plan.value = response.plan; result.value = response.evaluation; message.value = '已生成精确工时依据，尚未保存' }
      else { error.value = response.issues.map(issue => [issue.date, issue.line_code, issue.reason].filter(Boolean).join(' ')).join('；') }
    } else {
      result.value = await api<Evaluation>('/plans/evaluate', { method: 'POST', body: JSON.stringify({ snapshot: { inputs: inputs.value, plan: plan.value } }) })
    }
  } catch (e) { error.value = e instanceof Error ? e.message : '预览失败' }
  finally { busy.value = false }
}
onMounted(load)
</script>
<template>
  <h1>日内详情与工时预览</h1>
  <p>生产日从北京时间08:00起算。同日同线同 Recipe 汇总投入与工时，只向上取整一次。此处预览尚未保存。</p>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <p v-if="message" role="status">{{ message }}</p>
  <div class="input-toolbar">
    <label>生产日期 <input v-model="selectedDate" type="date" aria-label="详情生产日期" /></label>
    <label>线体 <select v-model="selectedLine" aria-label="详情线体"><option v-for="line in inputs?.masters.lines" :key="line.code" :value="line.code">{{ line.name }} ({{ line.code }})</option></select></label>
    <label>起始 Recipe <select v-model="selectedInitial" aria-label="起始 Recipe" :disabled="!canEdit"><option value="">未知，首次生产不扣切机</option><option v-for="recipe in inputs?.masters.recipes" :key="recipe.code" :value="recipe.code">{{ recipe.name }} ({{ recipe.code }})</option></select></label>
    <el-button @click="load" :disabled="!online || busy">重新读取资料</el-button>
    <span v-if="inputs">资料版本 {{ inputs.input_version }}</span>
  </div>
  <h2>生产段顺序与精确工时</h2>
  <p>工时可填十进制或分数，如0.024001或1/3。按数量生成工时依据会重新分配全部段工时与切机；工时没有最小步长。</p>
  <el-button @click="addSegment" :disabled="!canEdit || !selectedLine || !activeModels.length">添加生产段</el-button>
  <div class="input-table-scroll"><table class="input-table" aria-label="生产段详情">
    <thead><tr><th>生产日期</th><th>线体</th><th>顺序</th><th>机种</th><th>投入片数</th><th>精确工时</th><th>操作</th></tr></thead>
    <tbody><tr v-for="(row, i) in plan.segments" :key="row.id">
      <td><input v-model="row.date" type="date" :aria-label="`生产日期 第${i + 1}段`" :disabled="!canEdit" /></td>
      <td><select v-model="row.line_code" :aria-label="`线体 第${i + 1}段`" :disabled="!canEdit"><option v-for="line in inputs?.masters.lines" :key="line.code" :value="line.code">{{ line.code }}</option></select></td>
      <td><input v-model="row.order" inputmode="numeric" :aria-label="`顺序 第${i + 1}段`" :disabled="!canEdit" size="4" /></td>
      <td><select v-model="row.model_code" :aria-label="`机种 第${i + 1}段`" :disabled="!canEdit"><option v-for="model in activeModels" :key="model.code" :value="model.code">{{ model.name }} ({{ model.code }})</option></select></td>
      <td><input v-model="row.quantity" inputmode="numeric" :aria-label="`投入片数 第${i + 1}段`" :disabled="!canEdit" size="8" /></td>
      <td><input v-model="row.hours" :aria-label="`精确工时 第${i + 1}段`" :disabled="!canEdit" size="16" /></td>
      <td><el-button @click="plan.segments.splice(i, 1)" :disabled="!canEdit">移除</el-button></td>
    </tr></tbody>
  </table></div>
  <h2>有向切机与跨日分配</h2>
  <p>跨日分配合计必须精确等于切机参数，整次只计一次。每日工时校验通过仍需现场落实连续切机。</p>
  <el-button @click="addSwitch" :disabled="!canEdit || !selectedLine">添加切机</el-button>
  <section v-for="(change, i) in plan.switches" :key="change.id" class="switch-card">
    <div class="input-toolbar">
      <label>线体 <select v-model="change.line_code" :aria-label="`切机线体 第${i + 1}次`" :disabled="!canEdit"><option v-for="line in inputs?.masters.lines" :key="line.code" :value="line.code">{{ line.code }}</option></select></label>
      <label>原 Recipe <select v-model="change.from_recipe" :aria-label="`原 Recipe 第${i + 1}次`" :disabled="!canEdit"><option v-for="recipe in inputs?.masters.recipes" :key="recipe.code" :value="recipe.code">{{ recipe.code }}</option></select></label>
      <label>目标 Recipe <select v-model="change.to_recipe" :aria-label="`目标 Recipe 第${i + 1}次`" :disabled="!canEdit"><option v-for="recipe in inputs?.masters.recipes" :key="recipe.code" :value="recipe.code">{{ recipe.code }}</option></select></label>
      <el-button @click="change.allocations.push({date:selectedDate,order:nextOrder(),hours:'0',idle_hours:'0'})" :disabled="!canEdit">追加日分配</el-button>
      <el-button @click="plan.switches.splice(i, 1)" :disabled="!canEdit">移除切机</el-button>
    </div>
    <div v-for="(allocation, j) in change.allocations" :key="j" class="input-toolbar">
      <label>生产日期 <input v-model="allocation.date" type="date" :aria-label="`切机日期 第${i + 1}次第${j + 1}日`" :disabled="!canEdit" /></label>
      <label>顺序 <input v-model="allocation.order" :aria-label="`切机顺序 第${i + 1}次第${j + 1}日`" :disabled="!canEdit" size="4" /></label>
      <label>精确工时 <input v-model="allocation.hours" :aria-label="`切机工时 第${i + 1}次第${j + 1}日`" :disabled="!canEdit" size="12" /></label>
      <label>使用普通空闲 <input v-model="allocation.idle_hours" :aria-label="`空闲抵扣 第${i + 1}次第${j + 1}日`" :disabled="!canEdit" size="12" /></label>
      <el-button @click="change.allocations.splice(j, 1)" :disabled="!canEdit || change.allocations.length === 1">移除此日</el-button>
    </div>
  </section>
  <h2>普通空闲预算</h2>
  <p>只有未分配给生产、Holiday、保养或实验的工时可抵扣。一天一份预算，同日不同切机共用。</p>
  <el-button @click="plan.ordinary_idle.push({date:selectedDate,line_code:selectedLine,hours:'0'})" :disabled="!canEdit || !selectedLine">添加普通空闲</el-button>
  <div v-for="(idle, i) in plan.ordinary_idle" :key="i" class="input-toolbar">
    <label>生产日期 <input v-model="idle.date" type="date" :aria-label="`空闲日期 第${i + 1}行`" :disabled="!canEdit" /></label>
    <label>线体 <select v-model="idle.line_code" :aria-label="`空闲线体 第${i + 1}行`" :disabled="!canEdit"><option v-for="line in inputs?.masters.lines" :key="line.code" :value="line.code">{{ line.code }}</option></select></label>
    <label>精确工时 <input v-model="idle.hours" :aria-label="`空闲工时 第${i + 1}行`" :disabled="!canEdit" /></label>
    <el-button @click="plan.ordinary_idle.splice(i, 1)" :disabled="!canEdit">移除空闲</el-button>
  </div>
  <div class="input-toolbar">
    <el-button type="primary" @click="preview(false)" :disabled="!canEdit">预览精确工时</el-button>
    <el-button @click="preview(true)" :disabled="!canEdit">按数量生成工时依据</el-button>
  </div>
  <section v-if="result" class="input-preview" aria-label="工时预览结果">
    <h2>{{ result.capacity_valid ? '容量与顺序通过' : '容量或顺序存在问题' }}</h2>
    <p v-if="!result.computation_complete">缺少关键参数，计算尚未完成。</p>
    <p>切机次数 {{ result.switch_count }}。产出与确认按下方固定归属顺序核算。</p>
    <table v-if="result.issues.length" class="input-table" aria-label="预览问题"><thead><tr><th>生产日期</th><th>线体</th><th>机种</th><th>需求周</th><th>相关批次</th><th>问题</th></tr></thead><tbody><tr v-for="(issue, i) in result.issues" :key="i"><td>{{ issue.date ?? '快照' }}</td><td>{{ issue.line_code ?? issue.line_codes.join('、') }}</td><td>{{ issue.model_code }}</td><td>{{ issue.demand_week }}</td><td>{{ issue.related_batch_ids.join('、') }}</td><td>{{ issue.reason }}</td></tr></tbody></table>
    <article v-for="day in result.days" :key="`${day.date}-${day.line_code}`">
      <h3>{{ day.date }} · {{ day.line_code }} · 日可用 {{ day.available_hours }} 小时</h3>
      <div class="hour-summary"><p>生产 <ExactHours :value="day.production_hours" /></p><p>切机 <ExactHours :value="day.switch_hours" /></p><p>总占用 <ExactHours :value="day.occupied_hours" /></p></div>
      <p>日末 Recipe {{ day.ending_recipe ?? '未知' }}</p>
      <table class="input-table" aria-label="Recipe 容量依据"><thead><tr><th>Recipe</th><th>汇总投入</th><th>生产分配工时</th><th>全日产能</th><th>投入上限</th><th>参数状态</th></tr></thead><tbody><tr v-for="recipe in day.recipes" :key="recipe.recipe_code"><td>{{ recipe.recipe_code }}</td><td>{{ recipe.quantity }}</td><td><ExactHours :value="recipe.hours" /></td><td>{{ recipe.full_day_capacity }}</td><td>{{ recipe.limit }}</td><td>{{ ({known:'已知',zero:'已知为零',missing:'缺失',forbidden:'明确不能生产'} as Record<string,string>)[recipe.capacity_state] }}</td></tr></tbody></table>
    </article>
    <OutputResults :result="result" />
    <table v-if="result.switches.length" class="input-table" aria-label="切机依据"><thead><tr><th>切机</th><th>总时长</th><th>普通空闲抵扣</th><th>额外扣减</th><th>参数状态</th></tr></thead><tbody><tr v-for="change in result.switches" :key="change.id"><td>{{ change.id }}</td><td><ExactHours :value="change.total_hours" /></td><td><ExactHours :value="change.idle_hours" /></td><td><ExactHours :value="change.extra_hours" /></td><td>{{ change.duration_state === 'default_missing' ? '缺失，按12小时' : '已知' }}</td></tr></tbody></table>
  </section>
</template>
