<script setup lang="ts">
import { principal } from '../api'
const names = { viewer: '查看者', editor: '业务编辑者', administrator: '管理员' }
</script>
<template>
  <section v-if="principal">
    <p class="eyebrow">排程工作台</p><h1>欢迎，{{ principal.user_id }}</h1>
    <p>当前权限：{{ principal.roles.map(role => names[role]).join('、') }}</p>
    <el-card v-if="principal.roles.includes('administrator')" class="work-card"><h2>账号授权</h2><p>登记公司账号，分别授予查看、业务编辑与管理员权限。</p><router-link to="/admin"><el-button type="primary">管理授权</el-button></router-link></el-card>
  </section>
</template>
