<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { OutputEvaluation } from '../outputTypes'
const props=defineProps<{result:OutputEvaluation}>()
const pageSize=100, sourcePageSize=20
const weeklyPage=ref(1),sourcePage=ref(1),attributionPage=ref(1),confirmationPage=ref(1)
const openSources=ref(new Set<string>())
const weeklyRows=computed(()=>props.result.weekly_results.slice((weeklyPage.value-1)*pageSize,weeklyPage.value*pageSize))
const sourceRows=computed(()=>props.result.output_sources.slice((sourcePage.value-1)*sourcePageSize,sourcePage.value*sourcePageSize))
const attributionRows=computed(()=>props.result.attributions.slice((attributionPage.value-1)*pageSize,attributionPage.value*pageSize))
const confirmationRows=computed(()=>props.result.confirmation_usage.slice((confirmationPage.value-1)*pageSize,confirmationPage.value*pageSize))
watch(()=>props.result,()=>{weeklyPage.value=sourcePage.value=attributionPage.value=confirmationPage.value=1;openSources.value=new Set()})
watch(sourcePage,()=>{openSources.value=new Set()})
function toggleSource(event:Event,id:string){
  const opened=new Set(openSources.value)
  if((event.currentTarget as HTMLDetailsElement).open) opened.add(id)
  else opened.delete(id)
  openSources.value=opened
}
const statusNames:Record<string,string> = {allowed:'允许',pending:'待确认',forbidden:'整周禁令',revoked:'已撤销'}
</script>
<template>
  <section aria-label="产出需求与确认核算">
    <h2>产出、需求归属与确认</h2>
    <p>{{ result.executable ? '已排部分通过必要确认检查，可供采用前复核。' : '存在硬冲突、必要未确认或未完成计算，须处理后再采用。' }}</p>
    <p v-if="result.output_totals">{{ result.output_totals.computation_complete ? '预计产出' : '当前已算产出，尚未完成' }} {{ result.output_totals.expected }} 片；窗口外 {{ result.output_totals.outside_window }} 片；未分配 {{ result.output_totals.unallocated }} 片。总体按时率 {{ result.output_totals.on_time_rate_display }}。</p>
    <p>缺口按需求减去有效按时与迟交覆盖计算。待确认是缺口中已归属、尚未有效的部分，不与缺口重复相加。迟交补足不改写按时率。</p>
    <div class="input-table-scroll"><table class="input-table" aria-label="周需求覆盖"><thead><tr><th>机种</th><th>需求周周一</th><th>需求</th><th>有效按时</th><th>按时率</th><th>有效迟交</th><th>未补足缺口</th><th>待物料</th><th>待出货</th></tr></thead><tbody><tr v-for="row in weeklyRows" :key="`${row.model_code}-${row.week}`"><td>{{ row.model_code }}</td><td>{{ row.week }}</td><td>{{ row.demand }}</td><td>{{ row.on_time }}</td><td :title="`精确比例 ${row.on_time_rate ?? '不适用'}`">{{ row.on_time_rate_display }}</td><td>{{ row.late }}</td><td>{{ row.gap }}</td><td>{{ row.pending_material }}</td><td>{{ row.pending_shipping }}</td></tr></tbody></table></div>
    <el-pagination v-if="result.weekly_results.length>pageSize" v-model:current-page="weeklyPage" :page-size="pageSize" :total="result.weekly_results.length" layout="total,prev,pager,next" aria-label="周需求覆盖页" />
    <details v-if="result.excluded_demands.length"><summary>保留的窗口外需求 ({{ result.excluded_demands.length }} 行)</summary><p>历史完整需求保留在资料快照。本轮只核算当前十五周需求，过去完整需求不能再次作为未出货量占用本轮产出。</p><ul><li v-for="row in result.excluded_demands" :key="`${row.model_code}-${row.week}`">{{ row.model_code }} · {{ row.week }} · {{ row.quantity }} 片</li></ul></details>
    <h3>产出来源与逐笔取整</h3>
    <details v-for="source in sourceRows" :key="source.id" :open="openSources.has(source.id)" @toggle="toggleSource($event,source.id)"><summary>{{ source.model_code }} · {{ source.kind === 'wip' ? '起始在制品' : '跨线投入' }} · {{ source.input_date }} · {{ source.quantity }} 片 · 未分配产出 {{ source.unallocated_output }} 片</summary>
      <template v-if="openSources.has(source.id)">
      <p>来源 {{ source.id }}；良率月份 {{ source.yield_month }}；统一良率 {{ source.line_yield }}%，机种良率 {{ source.model_yield }}%；{{ source.kind === 'wip' ? '在制品不再套完整CT' : `CT ${source.cycle_time} 天` }}。</p>
      <table v-if="source.lines.length" class="input-table" aria-label="事实与有效剩余"><thead><tr><th>线体</th><th>计划</th><th>实际累计</th><th>剩余有效</th><th>过期未执行</th></tr></thead><tbody><tr v-for="line in source.lines" :key="line.line_code"><td>{{ line.line_code }}</td><td>{{ line.planned }}</td><td>{{ line.actual ?? '未知' }}</td><td>{{ line.remaining }}</td><td>{{ line.expired }}</td></tr></tbody></table>
      <table class="input-table" aria-label="产出取整依据"><thead><tr><th>产出日</th><th>CT分笔比例</th><th>取整前精确量</th><th>向上取整产出</th><th>未分配</th></tr></thead><tbody><tr v-for="(portion,i) in source.portions" :key="i"><td>{{ portion.date }}</td><td>{{ portion.fraction }}</td><td>{{ portion.before_rounding }}</td><td>{{ portion.quantity }}</td><td>{{ portion.unallocated }}</td></tr></tbody></table>
      </template>
    </details>
    <el-pagination v-if="result.output_sources.length>sourcePageSize" v-model:current-page="sourcePage" :page-size="sourcePageSize" :total="result.output_sources.length" layout="total,prev,pager,next" aria-label="产出来源页" />
    <h3>来源、需求与投入归属</h3>
    <p>同来源同需求周的归属投入是整组值，各产出分笔重复展示该组依据，不能将其逐行相加。比例分母不含未分配产出。</p>
    <div class="input-table-scroll"><table class="input-table" aria-label="产出归属确认明细"><thead><tr><th>来源</th><th>需求周</th><th>产出日</th><th>归属产出</th><th>该周来源产出/比例分母</th><th>归属投入/未取整份额</th><th>已确认投入/缺确认投入</th><th>物料已满足产出</th><th>待物料/待出货产出</th><th>有效按时/迟交</th><th>批次使用</th></tr></thead><tbody><tr v-for="(row,i) in attributionRows" :key="i"><td>{{ row.source_id }}</td><td>{{ row.demand_week }}</td><td>{{ row.date }}</td><td>{{ row.quantity }}</td><td>{{ row.source_week_output }} / {{ row.apportionment_denominator }}</td><td>{{ row.apportioned_input }} / {{ row.input_share_exact }}</td><td>{{ row.material_input_used }} / {{ row.material_input_pending }}</td><td>{{ row.material_satisfied }}</td><td>{{ row.pending_material }} / {{ row.pending_shipping }} <strong v-if="row.forbidden_material || row.forbidden_shipping">整周禁令</strong></td><td>{{ row.on_time }} / {{ row.late }}</td><td><p v-for="use in row.material_batches" :key="`m-${use.batch_id}`">物料 {{ use.batch_id }}: {{ use.quantity }} 投入片</p><p v-for="use in row.shipping_batches" :key="`s-${use.batch_id}`">出货 {{ use.batch_id }}: {{ use.quantity }} 合格片</p></td></tr></tbody></table></div>
    <el-pagination v-if="result.attributions.length>pageSize" v-model:current-page="attributionPage" :page-size="pageSize" :total="result.attributions.length" layout="total,prev,pager,next" aria-label="产出归属确认明细页" />
    <h3>确认批次额度</h3>
    <table class="input-table" aria-label="确认批次使用"><thead><tr><th>机种/需求周</th><th>类型/批次</th><th>状态</th><th>日期边界</th><th>原额度</th><th>使用量</th><th>余量</th></tr></thead><tbody><tr v-for="row in confirmationRows" :key="`${row.model_code}-${row.week}-${row.kind}-${row.batch_id}`"><td>{{ row.model_code }} / {{ row.week }}</td><td>{{ row.kind === 'material' ? '物料' : '出货' }} / {{ row.batch_id }}</td><td>{{ statusNames[row.status] }} {{ row.banned ? '（整周禁令优先）' : '' }}</td><td>{{ row.date ?? '不适用' }}</td><td>{{ row.quota ?? '不适用' }}</td><td>{{ row.used }}</td><td>{{ row.remaining }}</td></tr></tbody></table>
    <el-pagination v-if="result.confirmation_usage.length>pageSize" v-model:current-page="confirmationPage" :page-size="pageSize" :total="result.confirmation_usage.length" layout="total,prev,pager,next" aria-label="确认批次使用页" />
  </section>
</template>
