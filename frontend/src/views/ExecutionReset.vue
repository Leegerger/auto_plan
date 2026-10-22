<script setup lang="ts">
import ExecutionResetPanel from '../components/ExecutionResetPanel.vue'
import { useSharedPlan } from '../useSharedPlan'

const shared=useSharedPlan()
</script>

<template>
  <section class="execution-reset">
    <header class="page-heading">
      <div><p class="eyebrow">执行与计划重置</p><h1>执行与重置</h1><p class="page-subtitle">登记事实、确认覆盖范围并准备每周重置。</p></div>
      <router-link to="/schedule"><el-button>返回计划工作区</el-button></router-link>
    </header>
    <ExecutionResetPanel :shared="shared" />
  </section>
</template>
