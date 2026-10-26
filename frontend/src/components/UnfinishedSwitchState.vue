<script setup lang="ts">
import type { UnfinishedSwitch } from '../lineStateTypes'
defineProps<{job:UnfinishedSwitch}>()
</script>
<template>
  <span class="unfinished-switch-state">
    <strong>未完成切机 {{job.from_recipe}} → {{job.to_recipe}}</strong>
    <span>原作业 {{job.job_id}}</span>
    <span>现场剩余 {{job.remaining_hours}} 小时</span>
    <span v-if="job.standard_hours!=null">整次标准 {{job.standard_hours}} 小时</span>
    <span v-if="job.completed_hours!=null">现场已完成 {{job.completed_hours}} 小时</span>
    <span>目标 Recipe 尚未就绪</span>
  </span>
</template>
