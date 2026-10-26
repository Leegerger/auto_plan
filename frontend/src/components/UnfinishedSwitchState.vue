<script setup lang="ts">
import { computed } from 'vue'
import type { ObservedUnfinishedSwitch } from '../lineStateTypes'
const props=defineProps<{job:ObservedUnfinishedSwitch;observedAt?:string}>()
const observation=computed(()=>props.job.effective_at ?? props.observedAt)
const laterObservation=computed(()=>props.job.remaining_at_week_start_known===false)
function beijing(value:string){return new Date(Date.parse(value)+8*3600000).toISOString().slice(0,19).replace('T',' ')}
</script>
<template>
  <span class="unfinished-switch-state">
    <strong>未完成切机 {{job.from_recipe}} → {{job.to_recipe}}</strong>
    <span>原作业 {{job.job_id}}</span>
    <span v-if="laterObservation">周一起点剩余工时未知</span>
    <span>{{laterObservation ? '后续现场剩余' : '现场剩余'}} {{job.remaining_hours}} 小时</span>
    <span v-if="observation">剩余工时观测 {{beijing(observation)}} 北京时间</span>
    <span v-if="job.standard_hours!=null">整次标准 {{job.standard_hours}} 小时</span>
    <span v-if="job.completed_hours!=null">{{laterObservation ? '后续现场已完成' : '现场已完成'}} {{job.completed_hours}} 小时</span>
    <span>目标 Recipe 尚未就绪</span>
  </span>
</template>
