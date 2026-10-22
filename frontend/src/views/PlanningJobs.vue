<script setup lang="ts">
import JobsPanel from '../components/JobsPanel.vue'
import { useSharedPlan } from '../useSharedPlan'

const shared=useSharedPlan()
</script>

<template>
  <section class="planning-jobs">
    <header class="page-heading">
      <div><p class="eyebrow">自动排程</p><h1>计算任务</h1><p class="page-subtitle">任务使用已保存的共享草稿与完整输入。</p></div>
      <router-link to="/schedule"><el-button>返回计划工作区</el-button></router-link>
    </header>
    <JobsPanel :shared="shared" />
  </section>
</template>
