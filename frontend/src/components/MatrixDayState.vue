<script setup lang="ts">
import { computed } from 'vue'
import UnfinishedSwitchState from './UnfinishedSwitchState.vue'
import type { WorkspaceCapacity, WorkspaceDay } from '../workspaceTypes'
const props=defineProps<{day?:WorkspaceDay;capacities:WorkspaceCapacity[];recipeCode?:string;stale?:boolean}>()
const shownCapacities=computed(()=>props.capacities.filter(value=>props.recipeCode ? value.recipe_code===props.recipeCode : value.state!=='known'))
</script>

<template>
  <span class="matrix-day-state">
    <span v-if="!day">{{stale ? '容量待重新预览' : '尚未取得日容量'}}</span>
    <template v-else>
      <span :title="`精确值 ${day.available_hours.exact} 小时`">{{day.basis==='confirmed_remaining_day' ? '现场剩余可用' : '可用'}} {{day.available_hours.display}} 小时</span>
      <span :title="`精确值 ${day.remaining_hours.exact} 小时`">剩余 {{day.remaining_hours.display}} 小时</span>
      <span v-if="day.occupied_hours.exact!=='0'" :title="`精确值 ${day.occupied_hours.exact} 小时`">已占用 {{day.occupied_hours.display}} 小时</span>
      <span v-if="day.switch_hours.exact!=='0'" :title="`精确值 ${day.switch_hours.exact} 小时`">切机 {{day.switch_hours.display}} 小时</span>
      <UnfinishedSwitchState v-if="day.unfinished_switch" :job="day.unfinished_switch" />
      <span v-for="job in day.residual_switches ?? []" :key="job.id">剩余作业 {{job.job_id}} · 本日安排 {{job.allocated_hours.display}} 小时</span>
      <span v-if="day.holiday">Holiday<span v-if="day.opening_exception"> · 开工例外</span></span>
      <span v-else-if="day.opening_exception">开工例外</span>
      <span v-if="day.maintenance_hours">保养 {{day.maintenance_hours}} 小时</span>
      <span v-if="day.experiment_hours">实验 {{day.experiment_hours}} 小时</span>
      <span v-for="lock in day.locks" :key="lock.model_code">锁定 {{lock.model_code}} = {{lock.quantity}} 片</span>
      <span v-if="day.issues.length" class="state-attention">有待处理事项</span>
      <span v-for="capacity in shownCapacities" :key="capacity.recipe_code" :class="{'state-attention':capacity.state!=='known'}">{{capacity.recipe_code}} · {{capacity.state==='known' ? `全日产能 ${capacity.full_day_capacity} 片/24小时` : ({zero:'已知零产能',missing:'缺产能资料',forbidden:'明确不能生产'} as Record<string,string>)[capacity.state]}}</span>
    </template>
  </span>
</template>
