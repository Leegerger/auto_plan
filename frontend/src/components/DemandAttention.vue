<script setup lang="ts">
import { computed } from 'vue'
import DemandTrialPanel from './DemandTrialPanel.vue'
import type { DemandAttention, MissingDemandInput, OutputAttribution } from '../outputTypes'
import type { DemandGoal } from '../demandTrialTypes'
import { useSharedPlan } from '../useSharedPlan'

const props = defineProps<{ shared: ReturnType<typeof useSharedPlan> }>()
const view = props.shared.workspace.demand
const rows = computed(() => props.shared.evaluation.value?.demand_attention ?? [])
const models = computed(() => [...new Set(rows.value.map(row => row.model_code))].sort())
const goals = computed(() => Object.values(props.shared.lineage.value.demand_goals ?? {}))
const activeGoal = computed(() => goals.value.find(goal => goal.model_code===view.model_code && goal.demand_week===view.week && (props.shared.needsPreview.value || goal.remaining_output_target!==0)))
const selected = computed(() => rows.value.find(row => row.model_code === view.model_code && row.week === view.week))
const filtered = computed(() => rows.value.filter(row => (!view.modelFilter || row.model_code === view.modelFilter)
  && (!view.weekFilter || row.week === view.weekFilter) && (!view.reasonFilter || row.reasons.includes(view.reasonFilter))))
const attributions = computed(() => props.shared.evaluation.value?.attributions.filter(row => row.model_code === view.model_code && row.demand_week === view.week) ?? [])
const reasons: Record<string, string> = {
  unarranged: '尚无产出安排', pending_material: '待物料确认', pending_shipping: '待出货确认',
  forbidden_material: '物料跨周禁令', forbidden_shipping: '出货跨周禁令', confirmed_late: '已确认迟交预计覆盖', incomplete: '计算未完成',
}
const categories: Record<string, string> = { line_yields: '线体良率', model_yields: '机种良率', cycle_times: 'Cycle Time', capacities: '全日产能' }
function choose(row: DemandAttention) { view.model_code = row.model_code; view.week = row.week }
function openTrial(){
  const trial=props.shared.workspace.demandTrial
  if(trial.goalId || trial.modelCode!==view.model_code || trial.demandWeek!==view.week || !trial.actionId){
    trial.goalId='';trial.mode='on_time';trial.modelCode=view.model_code;trial.demandWeek=view.week;trial.targetOutput=selected.value?.unarranged_output ?? '';trial.actionId=crypto.randomUUID();trial.result=null;trial.notice=''
  }
  trial.startDate ||= props.shared.workspace.scope.start_date;trial.endDate ||= props.shared.workspace.scope.end_date;trial.open=true
}
function continueGoal(goal:DemandGoal){
  if(props.shared.readonly.value || props.shared.needsPreview.value || goal.remaining_output_target==null || goal.remaining_output_target<=0)return
  const trial=props.shared.workspace.demandTrial
  view.model_code=goal.model_code;view.week=goal.demand_week
  trial.goalId=goal.id;trial.mode='on_time';trial.modelCode=goal.model_code;trial.demandWeek=goal.demand_week;trial.targetOutput=goal.remaining_output_target
  trial.actionId=crypto.randomUUID();trial.result=null;trial.requestKey='';trial.notice='';trial.lineCode=''
  trial.open=true
}
function output(value:number|null){return props.shared.needsPreview.value ? '待重新预览' : value===null ? '待完成计算' : `${value} 片合格产出`}

function maintenance(kind: 'material' | 'shipping') {
  return { path: '/data/base', query: { category: 'confirmations', model: view.model_code, week: view.week, confirmation: kind, from: 'schedule' } }
}
function parameter(row: MissingDemandInput) {
  return { path: '/data/base', query: { category: row.category, model: row.model_code, week: view.week, month: row.month ?? undefined, from: 'schedule' } }
}
function locate(row: OutputAttribution) {
  const source = props.shared.evaluation.value?.output_sources.find(source => source.id === row.source_id)
  const line = source?.lines[0]?.line_code
  const week = props.shared.head.value?.weeks.find(week => week.dates.includes(row.input_date))
  if (!line || !week) return
  props.shared.workspace.day = row.input_date
  props.shared.workspace.line = line
  props.shared.workspace.week = week.number
  props.shared.workspace.panelOpen = true
}
</script>

<template>
  <section class="demand-attention" aria-label="十五周待处理需求">
    <div class="demand-heading">
      <strong>十五周需求</strong>
      <span v-if="shared.evaluation.value">{{ rows.length }} 项待处理需求</span>
      <span v-else>待重新预览</span>
      <button v-if="goals.length" type="button" class="demand-goal-toggle" :aria-expanded="shared.workspace.demandTrial.goalsExpanded" @click="shared.workspace.demandTrial.goalsExpanded=!shared.workspace.demandTrial.goalsExpanded">补排目标 {{goals.length}}</button>
      <button type="button" class="demand-toggle" :aria-expanded="view.expanded" aria-controls="demand-attention-list"
        :aria-label="view.expanded ? '收起待处理需求' : '展开待处理需求'" @click="view.expanded = !view.expanded">{{ view.expanded ? '收起清单' : '展开清单' }}</button>
    </div>
    <section v-if="goals.length && shared.workspace.demandTrial.goalsExpanded" aria-label="补排目标余额" class="demand-selection">
      <p class="demand-note">余额按合格产出核算，与未分配投入和重置调整量分别保留。安排完成仍须核对物料、出货及其他采用条件。</p>
      <article v-for="goal in goals" :key="goal.id"><strong>{{goal.model_code}} · 需求周 {{goal.demand_week}}</strong><p>原覆盖目标 {{goal.requested_output}} 片合格产出 · 剩余补排目标 {{output(goal.remaining_output_target)}}</p><p v-if="!shared.needsPreview.value">{{({pending:'仍有产出待安排',complete:'补排安排完成',incomplete:'核算未完成',unavailable:'当前窗口外'} as Record<string,string>)[goal.status]}} · 有效按时覆盖 {{output(goal.on_time)}} · 待物料确认 {{output(goal.pending_material)}} · 待出货确认 {{output(goal.pending_shipping)}}</p><el-button v-if="shared.editor.value && (shared.needsPreview.value || (goal.remaining_output_target ?? 0)>0)" :disabled="shared.readonly.value || shared.needsPreview.value" :aria-label="`继续补排 ${goal.model_code} ${goal.demand_week}`" @click="continueGoal(goal)">继续补排</el-button><details><summary>已接受投入安排</summary><p>目标来源共享修订 {{goal.origin_revision}} / 输入版本 {{goal.origin_input_version}}</p><p v-for="(action,id) in goal.accepted_actions" :key="id">线体 {{action.line_code}} · {{action.start_date}} 至 {{action.end_date}} · {{action.suggested_input}} 片投入</p></details></article>
    </section>
    <section v-if="view.model_code" aria-label="所选需求摘要" class="demand-selection">
      <div><strong>{{ view.model_code }} · 需求周 {{ view.week }}</strong><span v-if="selected"> · 截止 {{ selected.deadline.slice(0, 16).replace('T', ' ') }} 北京时间</span></div>
      <template v-if="selected">
        <div class="demand-quantities">
          <span>尚无产出安排 {{ selected.unarranged_output === null ? '待完成计算' : `${selected.unarranged_output} 片合格产出` }}</span>
          <span>按时覆盖 {{ selected.on_time }} 片</span>
          <span v-if="selected.pending_material">待物料确认 {{ selected.pending_material }} 片</span>
          <span v-if="selected.pending_shipping">待出货确认 {{ selected.pending_shipping }} 片</span>
          <span v-if="selected.late">已确认迟交预计覆盖 {{ selected.late }} 片</span>
          <span>未补足缺口 {{ selected.computation_complete ? `${selected.gap} 片` : '待完成计算' }}</span>
        </div>
        <p v-if="selected.pending && !selected.unarranged_output" class="demand-note">已有归属产出待确认，请先处理确认。尚无产出安排与未补足缺口分别核算。</p>
        <p v-if="selected.late" class="demand-note">迟交为预计覆盖，不表示已经实际出货。已有覆盖不需要重复补排或确认。</p>
        <div class="demand-actions">
          <el-button v-if="shared.editor.value && selected.unarranged_output!==0 && !activeGoal" :disabled="shared.readonly.value" @click="openTrial">按需补排</el-button>
          <span v-if="activeGoal">此需求已有补排目标 <button type="button" @click="shared.workspace.demandTrial.goalsExpanded=true">查看补排目标</button></span>
          <router-link v-if="selected.pending_material || selected.reasons.includes('forbidden_material')" :to="maintenance('material')">{{ selected.reasons.includes('forbidden_material') ? '处理物料禁令' : shared.editor.value ? '补物料确认' : '查看物料确认' }}</router-link>
          <router-link v-if="selected.pending_shipping || selected.reasons.includes('forbidden_shipping')" :to="maintenance('shipping')">{{ selected.reasons.includes('forbidden_shipping') ? '处理出货禁令' : shared.editor.value ? '补出货确认' : '查看出货确认' }}</router-link>
          <router-link v-for="item in selected.missing_inputs" :key="`${item.category}/${item.month}`" :to="parameter(item)">{{ shared.editor.value ? '补' : '查看' }}{{ item.month ? `${item.month} ` : '' }}{{ categories[item.category] ?? item.category }}</router-link>
        </div>
        <p v-if="selected.missing_inputs.length" class="demand-note">{{ selected.missing_inputs.map(item => `${item.model_code} ${item.month ?? ''} ${item.reason}`).join('；') }}。补排按实际投入月份重新核验。</p>
        <details v-if="attributions.length" class="demand-trace">
          <summary>追溯需求归属与调整依据</summary>
          <div class="input-table-scroll"><table class="input-table" aria-label="所选需求产出归属"><thead><tr><th>投入日</th><th>预计产出日</th><th>归属合格产出</th><th>按时覆盖</th><th>迟交预计覆盖</th><th>定位</th></tr></thead><tbody><tr v-for="(row, index) in attributions" :key="index"><td>{{ row.input_date }}</td><td>{{ row.date }}</td><td>{{ row.quantity }} 片</td><td>{{ row.on_time }} 片</td><td>{{ row.late }} 片</td><td><button v-if="shared.evaluation.value?.output_sources.find(source => source.id === row.source_id)?.kind === 'input'" type="button" @click="locate(row)">在矩阵查看</button><span v-else>起始在制品</span></td></tr></tbody></table></div>
          <p class="demand-note">选择对应生产段后可在对象面板调整，所有修改进入同一本机动作队列。</p>
        </details>
      </template>
      <p v-else class="demand-note">{{ shared.needsPreview.value ? '旧试算已失效，保留需求选择，请重新预览。' : '当前已无此项待处理原因，需求选择保留。' }}</p>
    </section>
    <DemandTrialPanel v-if="shared.workspace.demandTrial.open" :shared="shared" />
    <div v-show="view.expanded" id="demand-attention-list">
      <div class="demand-filters">
        <label>机种 <select v-model="view.modelFilter" aria-label="待处理需求机种"><option value="">全部机种</option><option v-for="model in models" :key="model" :value="model">{{ model }}</option></select></label>
        <label>需求周 <select v-model="view.weekFilter" aria-label="待处理需求周"><option value="">全部十五周</option><option v-for="week in shared.head.value?.weeks" :key="week.start_date" :value="week.start_date">{{ week.label }} · {{ week.start_date }}</option></select></label>
        <label>原因 <select v-model="view.reasonFilter" aria-label="待处理需求原因"><option value="">全部原因</option><option v-for="(label, code) in reasons" :key="code" :value="code">{{ label }}</option></select></label>
        <span>按截止时间排序，数量均为合格产出片数</span>
      </div>
      <div class="demand-table-scroll"><table class="input-table" aria-label="待处理需求清单">
        <thead><tr><th>机种</th><th>需求周</th><th>尚无产出安排</th><th>按时覆盖</th><th>迟交预计覆盖</th><th>待物料确认</th><th>待出货确认</th><th>未补足缺口</th><th>原因</th><th>选择</th></tr></thead>
        <tbody><tr v-for="row in filtered" :key="`${row.model_code}/${row.week}`" :class="{ 'demand-selected': row === selected }">
          <td>{{ row.model_code }}</td><td>{{ row.week }}</td><td>{{ row.unarranged_output === null ? '待完成计算' : `${row.unarranged_output} 片` }}</td>
          <td>{{ row.computation_complete ? `${row.on_time} 片` : '计算未完成' }}</td><td>{{ row.computation_complete ? `${row.late} 片` : '计算未完成' }}</td>
          <td>{{ row.computation_complete ? `${row.pending_material} 片` : '计算未完成' }}</td><td>{{ row.computation_complete ? `${row.pending_shipping} 片` : '计算未完成' }}</td><td>{{ row.computation_complete ? `${row.gap} 片` : '计算未完成' }}</td>
          <td>{{ row.reasons.map(reason => reasons[reason]).join('；') }}</td><td><button type="button" :aria-pressed="row === selected" :aria-label="`选择需求 ${row.model_code} ${row.week}`" @click="choose(row)">查看需求</button></td>
        </tr></tbody>
      </table></div>
      <p v-if="!filtered.length" class="demand-note">{{ shared.needsPreview.value ? '待重新预览后更新需求清单。' : '没有符合筛选条件的待处理需求。' }}</p>
    </div>
  </section>
</template>

<style scoped>
.demand-attention{border:1px solid var(--hairline);border-radius:7px;background:var(--canvas);margin:0 0 10px;font-size:12px;min-width:0}
.demand-heading{display:flex;align-items:center;gap:12px;padding:8px 12px;min-height:38px}
.demand-heading strong{font-size:13px}.demand-heading>span{color:var(--muted)}
.demand-goal-toggle{border:0;background:none;color:var(--accent);font-size:12px;cursor:pointer;white-space:nowrap}
.demand-toggle{margin-left:auto;border:0;background:none;color:var(--accent);font-size:12px;cursor:pointer;padding:3px 8px}
.demand-selection{border-top:1px solid var(--hairline-soft);padding:8px 12px;color:var(--body)}
.demand-selection>div:first-child{font-size:12px}.demand-selection>div:first-child>span{color:var(--muted)}
.demand-quantities,.demand-actions,.demand-filters{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center}
.demand-quantities{margin-top:5px}.demand-actions{margin-top:6px}.demand-actions a{color:var(--accent)}
.demand-note{color:var(--muted);font-size:12px;margin:5px 0;line-height:1.5}
.demand-filters{padding:8px 12px;border-top:1px solid var(--hairline-soft)}
.demand-filters label{display:flex;align-items:center;gap:6px}.demand-filters select{max-width:190px;min-height:28px}.demand-filters>span{color:var(--muted)}
.demand-table-scroll{overflow:auto;max-height:230px}.demand-table-scroll table{min-width:1120px}
.demand-table-scroll .input-table th,.demand-table-scroll .input-table td{padding:6px 8px;font-size:12px}
.demand-table-scroll .input-table th{position:sticky;top:0;background:var(--canvas);z-index:1}
.demand-selected{background:var(--surface-soft)}.demand-trace{margin-top:7px}.demand-trace summary{cursor:pointer}
</style>
