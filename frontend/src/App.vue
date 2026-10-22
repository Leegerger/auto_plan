<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CalendarRange, Database, GitBranch, LayoutDashboard, LogOut, Settings, Wifi, WifiOff } from '@lucide/vue'
import { api, online, principal, refreshPrincipal } from './api'
import { useSharedPlan } from './useSharedPlan'
import PlanStatusBar from './components/PlanStatusBar.vue'

const router=useRouter(),route=useRoute(),shared=useSharedPlan()
const loading=ref(true),error=ref('')
const roles=computed(()=>principal.value?.roles??[])
const business=computed(()=>roles.value.some(role=>role==='viewer'||role==='editor'))
const administrator=computed(()=>roles.value.includes('administrator'))
const showShell=computed(()=>!!principal.value&&route.path!=='/login')
const section=computed(()=>typeof route.meta.section==='string'?route.meta.section:'')
const sectionLinks=computed(()=>{
  if(section.value==='schedule')return [
    {to:'/schedule',label:'计划工作区'},
    {to:'/schedule/jobs',label:'计算任务'},
    {to:'/schedule/reset',label:'执行与重置'},
    {to:'/schedule/day',label:'日内工时'},
  ]
  if(section.value==='data')return [
    {to:'/data/demand',label:'需求管理'},
    {to:'/data/base',label:'基础资料'},
    ...(shared.head.value?.revision===0?[{to:'/data/initialization',label:'首次接入'}]:[]),
  ]
  return []
})
async function load(){
  error.value='';loading.value=true
  try{
    await api('/ready');await refreshPrincipal()
    if(!principal.value)await router.replace('/login')
    else await shared.start()
  }catch(e){error.value=e instanceof Error?e.message:'无法连接应用'}
  finally{loading.value=false}
}
async function logout(){
  try{
    await api('/auth/logout',{method:'POST'})
    shared.stop();principal.value=null;await router.replace('/login')
  }catch(e){error.value=e instanceof Error?e.message:'退出失败'}
}
watch(principal,value=>{
  if(value)void shared.start()
  else{
    shared.stop()
    if(!loading.value)void router.replace('/login')
  }
},{immediate:true})
onMounted(load)
</script>

<template>
  <div v-if="loading" class="boot-screen" role="status">
    <span class="boot-mark">BP</span>
    <p>正在连接排程系统…</p>
  </div>

  <div v-else-if="showShell" class="app-shell">
    <aside class="app-sidebar">
      <router-link to="/" class="brand" aria-label="Bonding 生产排程工作台">
        <span class="brand-mark">BP</span>
        <span><strong>Bonding</strong><small>生产排程</small></span>
      </router-link>
      <nav class="primary-nav" aria-label="主要导航">
        <router-link to="/" class="nav-item"><LayoutDashboard :size="19" /><span>工作台</span></router-link>
        <template v-if="business">
          <router-link to="/schedule" class="nav-item"><CalendarRange :size="19" /><span>排程</span></router-link>
          <router-link to="/data/demand" class="nav-item"><Database :size="19" /><span>需求与资料</span></router-link>
          <router-link to="/versions" class="nav-item"><GitBranch :size="19" /><span>版本与采用</span></router-link>
        </template>
        <router-link v-if="administrator" to="/system" class="nav-item"><Settings :size="19" /><span>系统管理</span></router-link>
      </nav>
      <div class="sidebar-account">
        <span class="account-avatar">{{ principal?.user_id.slice(0,2).toUpperCase() }}</span>
        <span class="account-copy"><strong>{{ principal?.user_id }}</strong><small>{{ business?'业务用户':'系统管理员' }}</small></span>
      </div>
    </aside>

    <div class="app-stage">
      <header class="utility-bar">
        <div class="utility-context">
          <span class="connection-state" :class="online?'online':'offline'">
            <Wifi v-if="online" :size="16" /><WifiOff v-else :size="16" />{{ online?'已连接':'网络断开' }}
          </span>
          <span class="utility-divider" />
          <span>{{ business?'共享计划工作区':'系统管理' }}</span>
        </div>
        <div class="utility-actions">
          <span class="account-inline">{{ principal?.user_id }}</span>
          <el-button text :disabled="!online" @click="logout"><LogOut :size="16" />退出登录</el-button>
        </div>
      </header>

      <PlanStatusBar v-if="business" />

      <nav v-if="sectionLinks.length" class="section-nav" aria-label="分区导航">
        <router-link v-for="item in sectionLinks" :key="item.to" :to="item.to">{{ item.label }}</router-link>
      </nav>

      <main class="page-main">
        <el-alert v-if="!online" title="网络已断开，恢复连接后才能继续操作" type="warning" :closable="false" show-icon />
        <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
        <el-button v-if="error" @click="load" :disabled="!online">重试连接</el-button>
        <router-view v-else />
      </main>
    </div>
  </div>

  <main v-else class="public-main">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-button v-if="error" @click="load" :disabled="!online">重试连接</el-button>
    <router-view v-else />
  </main>
</template>
