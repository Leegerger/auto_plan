<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, ArrowRight, CalendarClock, CheckCircle2, ClipboardList, Clock3, Database, GitBranch, RefreshCw, Settings } from '@lucide/vue'
import { api, online, principal } from '../api'
import type { Job, JobList } from '../jobTypes'
import { useSharedPlan } from '../useSharedPlan'

interface WorkItem {title:string;detail:string;to:string;tone:'danger'|'warning'|'info'}
const shared=useSharedPlan()
const {head,operations,unknown,pending,conflict,error,localWarning,recoveryCandidate}=shared
const jobs=ref<Job[]>([]),jobsError=ref(''),jobsBusy=ref(false)
const roles=computed(()=>principal.value?.roles??[])
const business=computed(()=>roles.value.some(role=>role==='viewer'||role==='editor'))
const administrator=computed(()=>roles.value.includes('administrator'))
const totals=computed(()=>head.value?.evaluation.output_totals??null)
const windowLabel=computed(()=>{
  const weeks=head.value?.weeks
  if(!weeks?.length)return '尚未读取排程窗口'
  return `${weeks[0]!.start_date} 至 ${weeks[weeks.length-1]!.dates[6]} · ${weeks.length} 周`
})
const resetDue=computed(()=>{
  if(!head.value?.latest_adoption||!head.value.weeks[0])return false
  return head.value.context.reset?.production_week!==head.value.weeks[0].start_date
})
const workItems=computed<WorkItem[]>(()=>{
  const items:WorkItem[]=[]
  if(head.value?.revision===0)items.push({title:'完成首次接入',detail:'尚无可用的共享计划与十五周需求。',to:'/data/initialization',tone:'info'})
  if(unknown.value)items.push({title:'核实前次保存结果',detail:'保存响应未知，当前处于只读状态。',to:'/schedule',tone:'danger'})
  if(conflict.value)items.push({title:'处理共享修订冲突',detail:'本机动作与其他用户的修改发生关联冲突。',to:'/schedule',tone:'danger'})
  if(localWarning.value||recoveryCandidate.value)items.push({title:'处理本机临时稿',detail:localWarning.value||'本机临时稿尚未恢复或共享。',to:'/schedule',tone:'warning'})
  else if(operations.value.length)items.push({title:'预览本机临时稿',detail:`${operations.value.length} 个完整动作尚未共享。`,to:'/schedule',tone:'info'})
  if(head.value?.pending)items.push({title:'核对待处理共享草稿',detail:'当前共享草稿已标记为待处理，尚不能直接作为正式采用版。',to:'/versions',tone:'warning'})
  if(totals.value?.gap)items.push({title:'检查未补足缺口',detail:`十五周缺口 ${totals.value.gap} 片。`,to:'/schedule',tone:'warning'})
  if(totals.value?.pending)items.push({title:'处理待确认产出',detail:`待确认产出 ${totals.value.pending} 片。`,to:'/data/base',tone:'warning'})
  if(resetDue.value)items.push({title:'处理本周重置',detail:'当前生产周尚无已生效的计划重置。',to:'/schedule/reset',tone:'warning'})
  const failed=jobs.value.filter(job=>['failed','interrupted'].includes(job.state))
  if(failed.length)items.push({title:'检查失败或中断的计算任务',detail:`${failed.length} 个任务需要处理。`,to:'/schedule/jobs',tone:'danger'})
  return items
})
const recentJobs=computed(()=>jobs.value.slice(0,5))
async function loadJobs(){
  if(!business.value||!online.value)return
  jobsBusy.value=true;jobsError.value=''
  try{jobs.value=(await api<JobList>('/jobs')).jobs.slice(0,20)}
  catch(e){jobsError.value=e instanceof Error?e.message:'计算任务读取失败'}
  finally{jobsBusy.value=false}
}
function jobState(state:Job['state']){return ({queued:'排队中',running:'计算中',completed:'已完成',stopped:'已停止',interrupted:'已中断',failed:'失败'} as Record<Job['state'],string>)[state]}
onMounted(loadJobs)
</script>

<template>
  <section class="workbench">
    <header class="page-heading">
      <div><p class="eyebrow">当前排程脉络</p><h1>工作台</h1><p class="page-subtitle">{{ windowLabel }}</p></div>
      <div class="heading-actions">
        <router-link v-if="business" to="/schedule"><el-button type="primary">进入排程<ArrowRight :size="16" /></el-button></router-link>
        <router-link v-if="administrator" to="/system"><el-button><Settings :size="16" />账号授权</el-button></router-link>
      </div>
    </header>

    <template v-if="business">
      <section class="attention-band" aria-label="需要处理">
        <div class="section-heading">
          <div><p class="eyebrow">下一步</p><h2>需要处理</h2></div>
          <span class="section-count">{{ workItems.length }} 项</span>
        </div>
        <div v-if="workItems.length" class="work-list">
          <router-link v-for="item in workItems" :key="`${item.title}-${item.detail}`" :to="item.to" class="work-item" :class="`tone-${item.tone}`">
            <AlertTriangle v-if="item.tone!=='info'" :size="20" />
            <ClipboardList v-else :size="20" />
            <span><strong>{{ item.title }}</strong><small>{{ item.detail }}</small></span>
            <ArrowRight :size="18" />
          </router-link>
        </div>
        <div v-else class="empty-state compact">
          <CheckCircle2 :size="28" />
          <h2>当前没有待处理事项</h2>
          <p>计划、需求和计算任务均未报告需要立即处理的状态。</p>
        </div>
      </section>

      <section class="metric-band" aria-label="计划摘要">
        <div class="metric-heading"><p class="eyebrow">计划摘要</p><h2>{{ head?.kind==='adopt'?'采用版计划':'共享草稿' }}</h2></div>
        <dl class="metrics">
          <div><dt>按时满足率</dt><dd>{{ totals?.on_time_rate_display??'未完成计算' }}</dd></div>
          <div><dt>未补足缺口</dt><dd>{{ totals?.gap??'—' }}</dd></div>
          <div><dt>待确认产出</dt><dd>{{ totals?.pending??'—' }}</dd></div>
          <div><dt>切机次数</dt><dd>{{ head?.evaluation.switch_count??'—' }}</dd></div>
          <div><dt>未分配产出</dt><dd>{{ totals?.unallocated??'—' }}</dd></div>
        </dl>
      </section>

      <div class="workbench-grid">
        <section class="content-band">
          <div class="section-heading"><div><p class="eyebrow">最近活动</p><h2>计算任务</h2></div><router-link to="/schedule/jobs">全部任务</router-link></div>
          <div v-if="jobsError" class="inline-error"><AlertTriangle :size="18" />{{ jobsError }}</div>
          <div v-else-if="jobsBusy" class="inline-loading"><RefreshCw :size="18" class="spin" />正在读取任务</div>
          <el-table v-else-if="recentJobs.length" :data="recentJobs" aria-label="最近计算任务">
            <el-table-column prop="job_id" label="任务" min-width="180" />
            <el-table-column label="状态" width="110"><template #default="{row}"><span class="job-state" :class="`job-${row.state}`">{{ jobState(row.state) }}</span></template></el-table-column>
            <el-table-column prop="created_at" label="提交时间" min-width="180" />
          </el-table>
          <p v-else class="empty-inline">暂无计算任务。</p>
        </section>

        <aside class="quick-links" aria-label="常用入口">
          <p class="eyebrow">常用入口</p>
          <router-link to="/schedule"><CalendarClock :size="20" /><span><strong>计划工作区</strong><small>查看单周矩阵与日内顺序</small></span></router-link>
          <router-link to="/data/demand"><Database :size="20" /><span><strong>需求管理</strong><small>维护十五周需求与变更</small></span></router-link>
          <router-link to="/versions"><GitBranch :size="20" /><span><strong>版本与采用</strong><small>查看共享修订与采用版</small></span></router-link>
        </aside>
      </div>
    </template>

    <section v-else class="content-band">
      <p class="eyebrow">系统管理</p><h2>账号与运行控制</h2>
      <p class="muted">管理员权限不包含业务计划编辑权限。</p>
      <router-link to="/system"><el-button type="primary"><Settings :size="16" />进入系统管理</el-button></router-link>
    </section>
  </section>
</template>
