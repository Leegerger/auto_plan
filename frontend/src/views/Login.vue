<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, principal, online, type Principal } from '../api'
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
const router = useRouter()
async function login() {
  if (!username.value.trim() || !password.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    principal.value = await api<Principal>('/auth/login', { method: 'POST', body: JSON.stringify({ username: username.value, password: password.value }) })
    password.value = ''
    await router.replace('/')
  } catch (e) { error.value = e instanceof Error ? e.message : '登录失败'; password.value = '' }
  finally { busy.value = false }
}
</script>
<template>
  <section class="login-card">
    <p class="eyebrow">生产计划管理</p><h1>登录排程应用</h1>
    <p>使用公司账号登录。账号需先获本系统授权。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-form @submit.prevent="login" label-position="top">
      <el-form-item label="公司账号"><el-input v-model="username" name="username" aria-label="公司账号" autocomplete="username" :disabled="busy" /></el-form-item>
      <el-form-item label="公司密码"><el-input v-model="password" name="password" aria-label="公司密码" type="password" autocomplete="current-password" :disabled="busy" /></el-form-item>
      <el-button type="primary" native-type="submit" :loading="busy" :disabled="!online || !username.trim() || !password">{{ busy ? '正在验证…' : '登录' }}</el-button>
    </el-form>
  </section>
</template>
