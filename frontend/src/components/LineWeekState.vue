<script setup lang="ts">
import UnfinishedSwitchState from './UnfinishedSwitchState.vue'
import type { WorkspaceLineWeek } from '../workspaceTypes'
defineProps<{ state?: WorkspaceLineWeek; stale?: boolean; switchPaths?:string[] }>()
</script>

<template>
  <span class="line-week-state">
    <span v-if="!state">{{stale ? '待重新预览周初状态' : '尚未取得周初状态'}}</span>
    <template v-else>
      <span>{{state.week}} 周一 08:00</span>
      <UnfinishedSwitchState v-if="state.state==='unfinished_switch' && state.unfinished_switch" :job="state.unfinished_switch" />
      <strong v-else-if="state.state==='planned_switch' && state.planned_switch">计划切机 {{state.planned_switch.from_recipe}} → {{state.planned_switch.to_recipe}} 尚未完成</strong>
      <strong v-else-if="state.recipe_code">保留 {{state.recipe_code}}</strong>
      <strong v-else>起点未知</strong>
      <span class="state-provenance">{{state.source==='confirmed_state' ? '现场确认起点' : state.source==='plan_projection' ? '计划推算，未作现场确认' : '窗口起点，旧记录未含现场确认来源'}}</span>
      <span v-if="state.new_switch_count!==undefined">本周新增 {{state.new_switch_count}} 次 · 跨周承接 {{state.carried_switch_count}} 次<span v-if="state.switch_hours"> · 切机占用 {{state.switch_hours.display}} 小时</span></span>
      <template v-if="state.first_production">
        <span v-if="state.first_production.duration_state==='unknown_initial'" class="state-attention">起点未知，首次切机尚未计入</span>
        <span v-else-if="state.first_production.duration_state==='same_recipe'">首段 {{state.first_production.recipe_code}} · 同 Recipe 续做</span>
        <template v-else>
          <span><template v-if="switchPaths && switchPaths.length>1">切机路径 {{switchPaths.join('；')}}</template><template v-else>首段切机 {{state.first_production.from_recipe}} → {{state.first_production.recipe_code}}</template> · {{state.first_production.required_hours.display}} 小时 · {{state.first_production.arranged ? '已安排' : '尚未安排'}}</span>
          <span v-if="state.first_production.duration_state==='default_missing'" class="state-attention">切机参数缺失，按 {{state.first_production.required_hours.display}} 小时试算</span>
        </template>
        <span v-if="state.first_production.issues.length" class="state-attention">首段衔接有待核对事项</span>
      </template>
      <span v-else>本周暂无生产</span>
    </template>
  </span>
</template>
