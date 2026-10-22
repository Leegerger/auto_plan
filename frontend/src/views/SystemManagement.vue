<script setup lang="ts">
import Authorizations from './Authorizations.vue'
</script>

<template>
  <section class="system-management">
    <header class="page-heading">
      <div><p class="eyebrow">系统管理</p><h1>账号与运行控制</h1><p class="page-subtitle">维护公司账号授权并停止已知计算任务。</p></div>
    </header>
    <Authorizations />
  </section>
</template>
