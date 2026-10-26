<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowRight, Calculator, RefreshCw, ShieldCheck } from '@lucide/vue'
import ManualEditor from '../components/ManualEditor.vue'
import StabilityPanel from '../components/StabilityPanel.vue'
import { useSharedPlan } from '../useSharedPlan'

const shared=useSharedPlan()
const {head,evaluation,operations,editor}=shared
const totals=computed(()=>evaluation.value?.output_totals??null)
const week=computed(()=>head.value?.weeks[0])
const ready=ref(false)
onMounted(async()=>{await shared.refresh();ready.value=true})
</script>

<template>
  <section v-if="ready" class="schedule-workspace">
    <header class="page-heading">
      <div><p class="eyebrow">单周排程</p><h1>计划工作区</h1><p class="page-subtitle">第 1 周 · {{ week?.start_date??'尚未读取' }} 至 {{ week?.dates[6]??'—' }}</p></div>
      <div class="heading-actions">
        <router-link v-if="editor" to="/schedule/jobs"><el-button><Calculator :size="16" />建立计算任务</el-button></router-link>
        <router-link to="/schedule/day"><el-button><RefreshCw :size="16" />精确工时依据</el-button></router-link>
      </div>
    </header>

    <section class="schedule-summary" aria-label="排程摘要">
      <div><span>共享修订</span><strong>{{ head?.revision??'—' }}</strong></div>
      <div><span>本机动作</span><strong>{{ operations.length }}</strong></div>
      <div><span>未补足缺口</span><strong>{{ totals?.gap??'—' }}</strong></div>
      <div><span>待确认产出</span><strong>{{ totals?.pending??'—' }}</strong></div>
      <div><span>切机次数</span><strong>{{ evaluation?.switch_count??'—' }}</strong></div>
    </section>

    <p role="status">{{ shared.evaluationState.value }}<span v-if="shared.needsPreview.value">。当前生产段保留共享基准，指标待重新计算。</span></p>
    <ManualEditor :shared="shared" />

    <details class="content-band schedule-details">
      <summary><span><ShieldCheck :size="19" />稳定性、锁定与变化比较</span><ArrowRight :size="17" /></summary>
      <StabilityPanel :shared="shared" />
    </details>
  </section>
  <p v-else class="empty-inline" role="status">正在读取最新计划状态…</p>
</template>
