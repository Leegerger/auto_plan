<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, principal, online, refreshPrincipal } from './api'
const router = useRouter()
const loading = ref(true)
const error = ref('')
async function load() {
  error.value = ''
  loading.value = true
  try { await refreshPrincipal(); if (!principal.value) await router.replace('/login') }
  catch (e) { error.value = e instanceof Error ? e.message : '无法连接应用' }
  finally { loading.value = false }
}
async function logout() {
  try { await api('/auth/logout', { method: 'POST' }); principal.value = null; await router.replace('/login') }
  catch (e) { error.value = e instanceof Error ? e.message : '退出失败' }
}
watch(principal, value => { if (!value && !loading.value) void router.replace('/login') })
onMounted(load)
</script>
<template>
  <header class="topbar">
    <router-link to="/" class="brand">Bonding 生产排程</router-link>
    <nav v-if="principal">
      <router-link to="/">工作台</router-link>
      <router-link v-if="principal.roles.some(role => role === 'viewer' || role === 'editor')" to="/inputs">输入资料</router-link>
      <router-link v-if="principal.roles.some(role => role === 'viewer' || role === 'editor')" to="/initialize">首次接入与需求</router-link>
      <router-link v-if="principal.roles.some(role => role === 'viewer' || role === 'editor')" to="/plans/shared">共享计划</router-link>
      <router-link v-if="principal.roles.some(role => role === 'viewer' || role === 'editor')" to="/plans/preview">日内详情</router-link>
      <router-link v-if="principal.roles.includes('administrator')" to="/admin">账号授权</router-link>
      <span>{{ principal.user_id }}</span>
      <el-button @click="logout" :disabled="!online">退出登录</el-button>
    </nav>
  </header>
  <main>
    <el-alert v-if="!online" title="网络已断开，恢复连接后才能继续操作" type="warning" :closable="false" show-icon />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-button v-if="error" @click="load" :disabled="!online">重试连接</el-button>
    <p v-if="loading" role="status">正在连接应用…</p>
    <router-view v-else-if="!error" />
  </main>
</template>
