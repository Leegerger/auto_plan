<script setup lang="ts">
import { watch } from 'vue'
import { useRouter } from 'vue-router'
import HistoryPanel from '../components/HistoryPanel.vue'
import { useSharedPlan } from '../useSharedPlan'

const router=useRouter(),shared=useSharedPlan()
watch(()=>shared.operations.value.length,(after,before)=>{
  if(after>before)void router.push('/schedule')
})
</script>

<template>
  <section class="versions-view">
    <header class="page-heading">
      <div><p class="eyebrow">冻结记录</p><h1>版本与采用</h1><p class="page-subtitle">查看共享修订、采用版和历史资料快照。</p></div>
      <router-link to="/schedule"><el-button>返回计划工作区</el-button></router-link>
    </header>
    <HistoryPanel :shared="shared" />
  </section>
</template>
