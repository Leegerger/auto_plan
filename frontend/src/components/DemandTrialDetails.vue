<script setup lang="ts">
import type { DemandTrialDetails } from '../demandTrialTypes'
import type { useSharedPlan } from '../useSharedPlan'
import ExactHours from './ExactHours.vue'
defineProps<{shared:ReturnType<typeof useSharedPlan>;result:DemandTrialDetails}>()
const emit=defineEmits<{accept:[retain:boolean]}>()
const labels={complete:'本次覆盖目标已安排',partial:'仅安排部分按时覆盖',blocked:'暂不可安排',incomplete:'计算未完成'}
function pieces(value:number|null|undefined,unit='片合格产出'){return value==null ? '待完成计算' : `${value} ${unit}`}
function confirmation(row:{model_code:string;week:string;kind:string}){return {path:'/data/base',query:{category:'confirmations',model:row.model_code,week:row.week,confirmation:row.kind,from:'schedule'}}}
</script>
<template>
    <section aria-label="单线补排试算结果">
      <h4>{{labels[result.status]}}</h4>
      <p>服务端试算依据：共享修订 {{result.basis.revision}} · 输入版本 {{result.basis.input_version}} · 最近采用版 {{result.basis.latest_adoption}} · {{result.basis.as_of}}</p>
      <div class="demand-trial-amounts"><span>覆盖目标 {{pieces(result.request.target_output)}}</span><span>建议投入 {{pieces(result.suggested_input,'片投入')}}</span><span>按时产出已安排 {{pieces(result.scheduled_on_time_output)}}</span><span>有效按时覆盖 {{pieces(result.valid_on_time_output)}}</span><strong>{{result.request.goal_id?'本次试算剩余目标':'剩余补排目标'}} {{pieces(result.remaining_output_target)}}</strong><strong v-if="result.request.goal_id">原补排目标余额 {{pieces(result.goal_remaining_output_target)}}</strong></div>
      <p>本次只使用 {{result.request.line_code}}，投入日期 {{result.request.start_date}} 至 {{result.request.end_date}}。较早需求周继续优先归属，确认事项按原需求周分别列出。</p>
      <div class="input-table-scroll"><table class="input-table" aria-label="建议逐日投入"><thead><tr><th>生产日</th><th>线体</th><th>机种</th><th>投入片数</th><th>日内顺序</th><th>精确生产工时</th></tr></thead><tbody><tr v-for="row in result.placements" :key="row.id"><td>{{row.date}}</td><td>{{row.line_code}}</td><td>{{row.model_code}}</td><td>{{row.quantity}}</td><td>{{row.order}}</td><td>{{row.hours}} 小时</td></tr></tbody></table></div>
      <div class="input-table-scroll"><table class="input-table" aria-label="各需求周覆盖影响"><thead><tr><th>机种/需求周</th><th>归属产出，前/后</th><th>已安排按时产出，前/后</th><th>有效按时覆盖，前/后</th><th>迟交已安排，前/后</th><th>待物料/出货确认</th></tr></thead><tbody><tr v-for="row in result.week_effects" :key="`${row.model_code}/${row.week}`"><td>{{row.model_code}} / {{row.week}}</td><td>{{row.before.attributed}} / {{row.after.attributed}}</td><td>{{row.physical_on_time_before}} / {{row.physical_on_time_after}}</td><td>{{row.before.on_time}} / {{row.after.on_time}}</td><td>{{row.physical_late_before}} / {{row.physical_late_after}}</td><td>{{row.after.pending_material}} / {{row.after.pending_shipping}}</td></tr></tbody></table></div>
      <details><summary>前后切机与精确占用</summary><section v-for="phase in [{title:'试算前',rows:result.switch_effects.before},{title:'试算后',rows:result.switch_effects.after}]" :key="phase.title"><h5>{{phase.title}}</h5><table class="input-table" :aria-label="`${phase.title}切机影响`"><thead><tr><th>作业/线体</th><th>原/目标 Recipe</th><th>本次作业工时</th><th>普通空闲</th><th>额外占用</th></tr></thead><tbody><tr v-for="row in phase.rows" :key="row.id"><td>{{row.id}} / {{row.line_code}}<br>{{row.kind==='residual'?'已开始作业的剩余部分':'计划切机'}}</td><td>{{row.from_recipe}} → {{row.to_recipe}}</td><td><ExactHours :value="row.total_hours" /></td><td><ExactHours :value="row.idle_hours" /></td><td><ExactHours :value="row.extra_hours" /></td></tr></tbody></table></section></details>
      <div v-if="result.required_confirmations.length" class="input-table-scroll"><table class="input-table" aria-label="补排所需确认"><thead><tr><th>需求</th><th>确认类型</th><th>待确认合格产出</th><th>所需提前投入</th><th>日期边界</th><th>处理</th></tr></thead><tbody><tr v-for="(row,index) in result.required_confirmations" :key="index"><td>{{row.model_code}} / {{row.week}}</td><td>{{row.kind==='material'?'物料':'出货'}}{{row.banned?' · 跨周禁令':''}}</td><td>{{row.pending_output}} 片</td><td>{{row.required_input ?? '—'}} 片投入</td><td>{{row.earliest_input_date}} / {{row.latest_output_date}}</td><td><router-link :to="confirmation(row)">核对确认资料</router-link></td></tr></tbody></table></div>
      <div v-if="result.blockers.length" class="input-table-scroll"><table class="input-table" aria-label="补排阻碍与采用条件"><thead><tr><th>生产日/线体/机种</th><th>原因</th></tr></thead><tbody><tr v-for="(row,index) in result.blockers" :key="index"><td>{{row.date}} / {{row.line_code}} / {{row.model_code}}</td><td>{{row.reason}}</td></tr></tbody></table></div>
      <div class="actions"><el-button v-if="result.operation && shared.editor.value" type="primary" :disabled="shared.readonly.value" @click="emit('accept',false)">选用完整安排并预览</el-button><el-button v-else-if="result.retain_operation && shared.editor.value" :disabled="shared.readonly.value" @click="emit('accept',true)">仅保留补排目标并预览</el-button></div>
    </section>
</template>
<style scoped>
p{margin:6px 0}.demand-trial-amounts{display:flex;flex-wrap:wrap;gap:6px 18px;margin:8px 0}.input-table{font-size:12px;min-width:650px}.input-table th,.input-table td{padding:6px}details{margin:8px 0}
</style>
