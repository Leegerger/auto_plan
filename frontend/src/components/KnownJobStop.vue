<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, online, principal } from '../api'

const jobId=ref(''),busy=ref(false),error=ref(''),message=ref('')
const valid=computed(()=>/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(jobId.value.trim()))
async function stop(){
  if(busy.value || !online.value || !valid.value || !principal.value?.roles.includes('administrator'))return
  busy.value=true;error.value='';message.value=''
  const identity=jobId.value.trim()
  try {
    await api(`/jobs/${identity}/stop`,{method:'POST'})
    message.value=`停止请求已接收：${identity}。运行中的任务需要等待进程结束。`
  } catch(e){error.value=e instanceof Error ? e.message : '停止请求失败'} finally {busy.value=false}
}
</script>
<template>
  <section v-if="principal?.roles.includes('administrator')" aria-label="管理员停止已知任务" class="work-card">
    <h2>停止已知计算任务</h2>
    <p>输入提交人提供的任务编号，停止排队或运行中的任务。管理员权限可停止任务，查看业务计划与候选内容需要相应的业务权限。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <p v-if="message" role="status">{{message}}</p>
    <form @submit.prevent="stop" class="actions">
      <label>已知任务编号 <input v-model="jobId" aria-label="已知任务编号" :disabled="busy || !online" size="38" spellcheck="false" /></label>
      <el-button native-type="submit" :disabled="!valid || busy || !online">停止指定任务</el-button>
    </form>
  </section>
</template>
