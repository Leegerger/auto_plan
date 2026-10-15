<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, principal, online, type Authorization, type Role } from '../api'
import KnownJobStop from '../components/KnownJobStop.vue'
const users = ref<Authorization[]>([])
const username = ref('')
const roles = ref<Role[]>([])
const active = ref(true)
const busy = ref(false)
const error = ref('')
const success = ref('')
const roleNames = { viewer: '查看者', editor: '业务编辑者', administrator: '管理员' }
async function load() {
  try { users.value = await api<Authorization[]>('/admin/authorizations') }
  catch (e) { error.value = e instanceof Error ? e.message : '无法读取授权' }
}
function edit(user: Authorization) { username.value = user.user_id; roles.value = [...user.roles]; active.value = user.active; success.value = ''; error.value = '' }
async function save() {
  if (!username.value.trim() || busy.value) return
  busy.value = true; error.value = ''; success.value = ''
  try {
    const updated = await api<Authorization>(`/admin/authorizations/${encodeURIComponent(username.value.trim())}`, { method: 'PUT', body: JSON.stringify({ roles: roles.value, active: active.value }) })
    success.value = `已保存 ${updated.user_id} 的授权`; await load()
  } catch (e) { error.value = e instanceof Error ? e.message : '授权保存失败' }
  finally { busy.value = false }
}
onMounted(load)
</script>
<template>
  <section v-if="principal?.roles.includes('administrator')">
    <p class="eyebrow">系统管理</p><h1>账号授权</h1><p>管理员权限不包含业务编辑权限。至少保留一个有效管理员。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-alert v-if="success" :title="success" type="success" :closable="false" show-icon />
    <el-table :data="users" stripe><el-table-column prop="user_id" label="公司账号" /><el-table-column label="角色"><template #default="{ row }">{{ row.roles.map((role: Role) => roleNames[role]).join('、') || '无' }}</template></el-table-column><el-table-column label="状态"><template #default="{ row }">{{ row.active ? '有效' : '已停用' }}</template></el-table-column><el-table-column label="操作"><template #default="{ row }"><el-button @click="edit(row)" :disabled="busy || !online">编辑</el-button></template></el-table-column></el-table>
    <el-card class="work-card"><h2>登记或修改授权</h2><el-form @submit.prevent="save" label-position="top">
      <el-form-item label="公司账号"><el-input v-model="username" aria-label="授权账号" :disabled="busy || !online" /></el-form-item>
      <el-form-item label="角色"><el-checkbox-group v-model="roles" :disabled="busy || !online"><el-checkbox value="viewer">查看者</el-checkbox><el-checkbox value="editor">业务编辑者</el-checkbox><el-checkbox value="administrator">管理员</el-checkbox></el-checkbox-group></el-form-item>
      <el-form-item label="授权状态"><el-switch v-model="active" active-text="有效" inactive-text="已停用" :disabled="busy || !online" /></el-form-item>
      <el-button type="primary" native-type="submit" :loading="busy" :disabled="!username.trim() || !online">保存授权</el-button>
    </el-form></el-card>
    <KnownJobStop />
  </section>
  <el-alert v-else title="没有管理账号授权的权限" type="error" :closable="false" />
</template>
