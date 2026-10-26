<script setup lang="ts">
import { computed } from 'vue'
import type { CandidateView } from '../jobTypes'
import UnfinishedSwitchState from './UnfinishedSwitchState.vue'
const props=defineProps<{candidate:CandidateView['candidate']}>()
const residuals=computed(()=>(props.candidate.evaluation.switches ?? []).filter(row=>row.kind==='residual').map(row=>({
  evaluation:row,record:props.candidate.snapshot?.line_states?.records.find(record=>record.id===row.residual_record_id)
})))
</script>
<template>
  <section aria-label="候选现场依据">
    <h4>候选现场依据</h4>
    <p>以下采用条件及剩余工时使用任务冻结的现场依据。候选中的完成安排是计划推算，应用时仍须重新核验。</p>
    <ul v-if="candidate.evaluation.adoption_conditions?.length">
      <li v-for="(condition,index) in candidate.evaluation.adoption_conditions" :key="index">{{condition.line_code}} · {{condition.date}} · {{condition.reason}}</li>
    </ul>
    <p v-else>冻结检查未列出额外现场采用条件，应用预览仍会检查当前依据。</p>
    <div v-for="row in residuals" :key="row.evaluation.id">
      <p>{{row.evaluation.line_code}} · 原作业 {{row.evaluation.id}} · 候选预留 {{row.evaluation.total_hours.display}} 小时</p>
      <UnfinishedSwitchState v-if="row.record?.unfinished_switch" :job="row.record.unfinished_switch" :observed-at="row.record.effective_at" />
      <p v-if="row.record">现场来源 {{row.record.source}} · 记录人 {{row.record.actor}} · 记录时点 {{row.record.recorded_at}}</p>
    </div>
  </section>
</template>
