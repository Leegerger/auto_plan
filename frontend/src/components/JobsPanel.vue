<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api, ApiError, basePath, online, principal } from '../api'
import type { useSharedPlan } from '../useSharedPlan'
import type { CandidateView, ComputationSource, Job, JobCandidateRef, JobList, SolveRequest, SubmissionResult } from '../jobTypes'
import type { InputSnapshot } from '../inputFields'
import OutputResults from './OutputResults.vue'
import ComputationSourceReview from './ComputationSourceReview.vue'

const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
const router=useRouter()
const jobs=ref<Job[]>([]),queueCapacity=ref(0),workerLimit=ref(0)
const busy=ref(false),error=ref(''),message=ref(''),storageIssue=ref('')
const viewed=ref<CandidateView|null>(null),selected=ref<string[]>([])
interface PendingSubmission {submission_id:string;kind:'submit'|'rerun';source_job_id?:string}
const outstanding=ref<PendingSubmission|null>(null)
const storageKey=`auto-plan:${encodeURIComponent(basePath)}:${encodeURIComponent(principal.value?.user_id ?? '')}:job-submission`
const lines=ref<{code:string;name:string}[]>([]),models=ref<{code:string;name:string}[]>([])
const request=reactive<SolveRequest>({scope:{start_date:'',end_date:'',line_codes:[],model_codes:[]},mode:'local',strategy:'hybrid',time_limit_seconds:20,max_candidates:1,native_threads:1})
const blocked=computed(()=>props.shared.readonly.value || busy.value || !!outstanding.value || !!storageIssue.value)
const unsaved=computed(()=>props.shared.operations.value.length>0)
const computationSource=shallowRef<ComputationSource|null>(null),reviewedInputs=shallowRef<InputSnapshot|null>(null)
const sourceRequired=ref(false),sourceReviewed=ref(false),fullReviewRequired=ref(false)
const sourceMismatch=computed(()=>!!computationSource.value && !!props.shared.head.value && (
  computationSource.value.source_revision!==props.shared.head.value.revision ||
  computationSource.value.latest_adoption!==props.shared.head.value.latest_adoption ||
  computationSource.value.snapshot.time.window_start!==props.shared.head.value.time.window_start))
const sourceBlocked=computed(()=>fullReviewRequired.value || sourceMismatch.value || sourceRequired.value && (!computationSource.value || !sourceReviewed.value))
const viewOnly=computed(()=>viewed.value?.validity?.state==='view_only')
const candidateChanges=computed(()=>{
  const operation=viewed.value?.candidate.operation
  if(!operation)return []
  const names:Record<string,string>={patch_plan:'计划编辑',replace_plan:'完整计划',segments:'生产段',remove_segments:'移除生产段',switches:'切机安排',remove_switches:'移除切机',initial_states:'起始 Recipe',ordinary_idle:'普通空闲',remove_idle:'移除普通空闲'}
  return Object.keys(names).flatMap(kind=>{
    const values=operation[kind]
    if(!Array.isArray(values))return []
    return values.map(value=>{
      const row=typeof value==='object' && value ? value as Record<string,unknown> : null
      const identity=kind==='initial_states' ? row?.line_code : ['ordinary_idle','remove_idle'].includes(kind) ? `${row?.date}:${row?.line_code}` : row?.id ?? value
      return {identity:`${kind}:${identity}`,description:`${names[kind]} · ${row ? [row.date,row.line_code,row.model_code,row.recipe_code,row.quantity===undefined ? '' : `${row.quantity}片`,row.hours===undefined ? '' : `${row.hours}小时`].filter(Boolean).join(' / ') : value}`}
    })
  })
})
let alive=true,pollTimer:ReturnType<typeof setTimeout>|undefined,reading=false

watch(()=>props.shared.head.value,async head=>{
  if(!head)return
  computationSource.value=null;sourceReviewed.value=false;fullReviewRequired.value=false
  request.scope.start_date ||= head.weeks[0]?.dates[0] ?? ''
  request.scope.end_date ||= head.weeks[0]?.dates[6] ?? ''
  try {
    const data=await api<InputSnapshot>(`/inputs?version=${head.input_version}`)
    if(!alive || head!==props.shared.head.value)return
    reviewedInputs.value=data
    lines.value=data.masters.lines.map(row=>({code:String(row.code),name:String(row.name)}));models.value=data.masters.models.map(row=>({code:String(row.code),name:String(row.name)}))
    if(!request.scope.line_codes.length)request.scope.line_codes=lines.value.map(row=>row.code)
  } catch(e){showError(e)}
},{immediate:true})
function showError(value:unknown){error.value=value instanceof Error ? value.message : '计算任务操作失败'}
function restoreSubmission(){
  try {
    const raw=localStorage.getItem(storageKey)
    if(!raw)return
    const value=JSON.parse(raw)
    if(typeof value.submission_id!=='string' || !['submit','rerun'].includes(value.kind))throw new Error('提交编号记录无法读取，请保留本机记录并核对任务')
    outstanding.value=value
  } catch(e){storageIssue.value=e instanceof Error ? e.message : '无法读取前次计算提交编号'}
}
function remember(value:PendingSubmission){
  // A lost HTTP response must leave a verifiable key across reloads.
  localStorage.setItem(storageKey,JSON.stringify(value));outstanding.value=value
}
function clearSubmission(){localStorage.removeItem(storageKey);outstanding.value=null}
async function refresh(){
  if(reading || !online.value || !principal.value)return
  reading=true
  try {
    const result=await api<JobList>('/jobs')
    if(alive){jobs.value=result.jobs;queueCapacity.value=result.queue_capacity;workerLimit.value=result.worker_limit}
  } catch(e){if(alive)showError(e)} finally {reading=false}
}
async function poll(){
  await refresh()
  if(alive)pollTimer=setTimeout(poll,2000)
}
function accepted(result:SubmissionResult,verified=false){
  clearSubmission()
  message.value=result.status==='ACCEPTED' ? `${verified ? '已核实计算任务已接收' : '计算任务已接收'}：${result.job_id}` : '已核实前次计算提交作废。不会自动重发，请核对后由你重新提交。'
}
async function readSource(){
  if(blocked.value || unsaved.value)return
  busy.value=true;error.value='';sourceReviewed.value=false;computationSource.value=null;sourceRequired.value=true
  try{computationSource.value=await api<ComputationSource>('/jobs/source')}
  catch(e){showError(e)}finally{busy.value=false}
}
async function submit(){
  if(blocked.value || unsaved.value || sourceBlocked.value || !props.shared.head.value)return
  error.value='';message.value=''
  if(!request.scope.start_date || !request.scope.end_date || !request.scope.line_codes.length){error.value='请选择计算日期和至少一条线体';return}
  if(request.mode==='all_replan'){
    try{await ElMessageBox.confirm(`全部重排范围 ${request.scope.start_date} 至 ${request.scope.end_date}，${request.scope.line_codes.length} 条线体。提交前请确认已核对计算来源。`,'确认全部重排',{type:'warning',confirmButtonText:'提交全部重排',cancelButtonText:'返回核对',autofocus:false})}catch{return}
  }
  busy.value=true
  try {
    restoreSubmission()
    if(outstanding.value || storageIssue.value)return
    const pending:PendingSubmission={submission_id:crypto.randomUUID(),kind:'submit'}
    remember(pending)
    const head=props.shared.head.value
    const source=computationSource.value
    const result=await api<SubmissionResult>('/jobs',{method:'POST',body:JSON.stringify({submission_id:pending.submission_id,base_revision:source?.source_revision ?? head.revision,input_version:source?.input_version ?? head.input_version,latest_adoption:source?.latest_adoption ?? head.latest_adoption,request,reviewed_as_of:source?.snapshot.time.as_of ?? head.time.as_of})})
    accepted(result);await refresh()
  } catch(e){
    showError(e)
    if(e instanceof ApiError && e.status===409){sourceRequired.value=true;sourceReviewed.value=false;computationSource.value=null;if(e.data.review_required===true)fullReviewRequired.value=true}
    if(e instanceof ApiError && e.status>=400 && e.status<500 && e.status!==408)try{clearSubmission()}catch{storageIssue.value='无法清除已结束的提交编号，请重试核实'}
  } finally {busy.value=false}
}
async function verify(){
  if(!outstanding.value || busy.value || !online.value || !props.shared.editor.value)return
  busy.value=true;error.value=''
  try {
    const result=await api<SubmissionResult>('/jobs/verify',{method:'POST',body:JSON.stringify({submission_id:outstanding.value.submission_id})})
    accepted(result,true);await refresh()
  } catch(e){showError(e)} finally {busy.value=false}
}
async function rerun(job:Job){
  if(blocked.value)return
  busy.value=true;error.value='';message.value=''
  try {
    restoreSubmission()
    if(outstanding.value || storageIssue.value)return
    const pending:PendingSubmission={submission_id:crypto.randomUUID(),kind:'rerun',source_job_id:job.job_id}
    remember(pending)
    const result=await api<SubmissionResult>(`/jobs/${job.job_id}/rerun`,{method:'POST',body:JSON.stringify({submission_id:pending.submission_id})})
    accepted(result);await refresh()
  } catch(e){
    showError(e)
    if(e instanceof ApiError && e.status>=400 && e.status<500 && e.status!==408)try{clearSubmission()}catch{storageIssue.value='无法清除已结束的提交编号，请重试核实'}
  } finally {busy.value=false}
}
function canStop(job:Job){return ['queued','running'].includes(job.state) && (job.actor===principal.value?.user_id || principal.value?.roles.includes('administrator'))}
async function stop(job:Job){
  if(busy.value || !online.value || !canStop(job))return
  busy.value=true;error.value='';message.value=''
  try {
    await api(`/jobs/${job.job_id}/stop`,{method:'POST'})
    message.value=`停止请求已接收：${job.job_id}。已发布的候选仍可查看。`
    await refresh()
  } catch(e){showError(e)} finally {busy.value=false}
}
async function viewCandidate(job:Job,reference:JobCandidateRef){
  if(busy.value || !online.value)return
  busy.value=true;error.value='';viewed.value=null;selected.value=[]
  try {
    const result=await api<CandidateView>(`/jobs/${job.job_id}/candidates/${reference.candidate_id}`)
    if(alive)viewed.value=result
  } catch(e){showError(e)} finally {busy.value=false}
}
async function stageCandidate(partial:boolean){
  if(blocked.value || unsaved.value || viewOnly.value || !viewed.value || partial && !selected.value.length)return
  const candidate=viewed.value
  // The local draft contains only the server reference and optional selection.
  props.shared.restoreDraft({data_version:1,...candidate.proposal,operations:[{kind:'apply_candidate',job_id:candidate.job_id,candidate_id:candidate.candidate_id,payload_sha:candidate.payload_sha,...(partial ? {selection:[...selected.value]} : {})}]})
  await props.shared.check()
  if(!props.shared.error.value||props.shared.conflict.value)await router.push('/schedule')
}
const states:Record<Job['state'],string>={queued:'排队中',running:'计算中',completed:'已完成',stopped:'已停止',interrupted:'已中断',failed:'失败'}
const outcomes:Record<string,string>={feasible:'已找到可行候选',stopped:'已停止',time_limit:'达到计算时限',no_candidate:'未找到候选',incomplete:'输入不完整',scope_conflict:'调整范围冲突'}
const failures:Record<string,string>={worker_exited_without_result:'计算进程已退出，未返回完整结果',stop_deadline:'停止等待超时，已强制中断',malformed_worker_output:'计算进程返回内容不完整',invalid_worker_output:'计算进程返回结果未通过检查',spawn_failed:'计算进程未能启动'}
watch(()=>request.mode,mode=>{request.time_limit_seconds=mode==='local' ? 20 : 90})
onMounted(()=>{restoreSubmission();void poll()})
onUnmounted(()=>{alive=false;clearTimeout(pollTimer)})
</script>
<template>
  <section aria-label="计算任务" class="jobs-panel">
    <h2>计算任务</h2>
    <p>计算使用已保存的共享草稿和完整输入。离开页面或退出登录后，任务继续执行。</p>
    <p>最多等待 {{queueCapacity}} 个任务，同时计算 {{workerLimit}} 个任务；每两秒更新任务状态。状态更新不延长候选内容保留期。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert v-if="storageIssue" :title="storageIssue" type="warning" :closable="false" />
    <p v-if="message" role="status">{{message}}</p>
    <div v-if="outstanding">
      <el-alert title="前次计算提交结果未知，请先核实原提交编号，避免重复计算。" type="warning" :closable="false" />
      <p>原提交编号 {{outstanding.submission_id}}</p>
      <el-button v-if="shared.editor.value" @click="verify" :disabled="busy || !online">核实计算提交</el-button>
    </div>
    <p v-if="unsaved && shared.editor.value" role="status">本机仍有未共享动作，请先共享保存或处理本机动作后再提交计算。</p>
    <el-button v-if="shared.editor.value" @click="readSource" :disabled="blocked || unsaved || fullReviewRequired">重新读取计算来源</el-button>
    <p v-if="fullReviewRequired || sourceMismatch" role="alert">共享计划、采用版或生产日期窗口已变化。请先重新载入并核对完整共享计划，再读取计算来源。</p>
    <p v-if="sourceRequired && !computationSource && !fullReviewRequired" role="status">请重新读取并核对完整计算来源，再明确提交。计算范围和策略仍保留。</p>
    <ComputationSourceReview v-if="computationSource" :source="computationSource" :inputs="reviewedInputs" :context="shared.head.value?.context ?? null">
      <label><input type="checkbox" v-model="sourceReviewed" :disabled="blocked || sourceMismatch || fullReviewRequired" aria-label="已核对本次计算来源" />已核对本次计算来源</label>
    </ComputationSourceReview>
    <form v-if="shared.editor.value" @submit.prevent="submit">
      <fieldset :disabled="blocked || unsaved" class="job-request">
        <legend>提交已保存的共享计划</legend>
        <label>计算模式 <select v-model="request.mode" aria-label="计算模式"><option value="local">局部调整</option><option value="new_week">新周排程</option><option value="all_replan">全部重排</option></select></label>
        <label>计算范围起日 <input type="date" v-model="request.scope.start_date" aria-label="计算范围起日" required /></label>
        <label>计算范围终日 <input type="date" v-model="request.scope.end_date" aria-label="计算范围终日" required /></label>
        <label>计算线体 <select multiple v-model="request.scope.line_codes" aria-label="计算线体"><option v-for="line in lines" :key="line.code" :value="line.code">{{line.code}} {{line.name}}</option></select></label>
        <label>计算机种，不选表示全部 <select multiple v-model="request.scope.model_codes" aria-label="计算机种"><option v-for="model in models" :key="model.code" :value="model.code">{{model.code}} {{model.name}}</option></select></label>
        <label>求解策略 <select v-model="request.strategy" aria-label="求解策略"><option value="hybrid">混合策略</option><option value="cp_sat">CP-SAT</option><option value="highs">HiGHS</option></select></label>
        <label>计算时限，秒 <input type="number" v-model.number="request.time_limit_seconds" min="1" max="120" step="1" aria-label="计算时限" required /></label>
        <label>候选数量 <input type="number" v-model.number="request.max_candidates" min="1" max="3" step="1" aria-label="候选数量" required /></label>
        <p>每个任务使用单线程求解。</p>
      </fieldset>
      <el-button native-type="submit" type="primary" :disabled="blocked || unsaved || sourceBlocked">提交计算任务</el-button>
    </form>
    <ul aria-label="持久计算任务" class="job-list">
      <li v-for="job in jobs" :key="job.job_id">
        <p>{{job.job_id}} · 执行状态 {{states[job.state]}} · 提交人 {{job.actor}} · 原共享修订 {{job.source_revision}} · 输入版本 {{job.input_version}} · 最近采用版 {{job.latest_adoption}}</p>
        <p v-if="job.stop_requested_at && job.state==='running'">已请求停止，等待计算进程结束。</p>
        <p v-if="job.progress">{{job.progress.stage ?? job.progress.status}} {{job.progress.detail ?? job.progress.message}}</p>
        <p v-if="job.result?.status || job.result?.solve_status">计算结果 {{outcomes[job.result.solve_status ?? job.result.status ?? ''] ?? job.result.solve_status ?? job.result.status}} · 用时 {{job.result.elapsed_seconds}} 秒 · {{job.result.global_optimal ? '已证明全局最优' : '未证明全局最优'}}</p>
        <p v-if="job.result?.reason">{{failures[job.result.reason] ?? job.result.reason}}。已完整发布的候选仍可查看。</p>
        <details v-if="job.result?.stage_bounds?.length"><summary>计算阶段与界限</summary><p v-for="(bound,index) in job.result.stage_bounds" :key="index">{{bound.stage}} · {{bound.neighborhood}} · {{bound.status}} · {{bound.detail}} · 目标 {{bound.objective?.join(' / ') ?? '未取得'}} · 下界 {{bound.lower_bound?.join(' / ') ?? '未取得'}} · {{bound.global_proof ? '全局证明' : '仅当前阶段范围'}}</p></details>
        <p v-for="(issue,index) in job.result?.issues ?? []" :key="index">{{issue.date}} {{issue.line_code}} {{issue.model_code}} {{issue.reason}}</p>
        <div v-for="candidate in job.candidates" :key="candidate.candidate_id">
          <span>候选 {{candidate.candidate_id}} · {{candidate.expired ? '内容已过期，可按原输入重新计算' : '内容保留至最后一次授权查看后 30 天'}}</span>
          <el-button @click="viewCandidate(job,candidate)" :disabled="busy || !online || candidate.expired">查看候选内容</el-button>
        </div>
        <el-button v-if="canStop(job)" @click="stop(job)" :disabled="busy || !online || !!job.stop_requested_at">停止任务</el-button>
        <el-button v-if="shared.editor.value && !['queued','running'].includes(job.state)" @click="rerun(job)" :disabled="blocked">按原输入重新计算</el-button>
        <p v-if="shared.editor.value && !['queued','running'].includes(job.state)">重新计算沿用该任务冻结的原始计划、输入和调整范围。</p>
      </li>
    </ul>
    <p v-if="!jobs.length">暂无计算任务。</p>
    <section v-if="viewed" aria-label="候选内容">
      <h3>候选 {{viewed.candidate_id}}</h3>
      <p>原共享修订 {{viewed.proposal.base_revision}} · 输入版本 {{viewed.proposal.input_version}} · 最近采用版 {{viewed.proposal.latest_adoption}}</p>
      <p>原始检查 {{viewed.candidate.eligibility==='executable' ? '可执行候选' : '待业务确认'}} · {{viewed.candidate.tradeoff}}</p>
      <el-alert v-if="viewOnly" :title="`完整输入已变化，候选仅可查看。当前输入版本 ${viewed.validity.current_input_version}。若需调整当前计划，请提交当前共享计划的新计算任务。按原输入重新计算仍保留历史输入。`" type="warning" :closable="false" />
      <p v-else>有效性 需要重新检查。原始候选的可执行状态不代替本次完整业务预览。</p>
      <p>完整或部分候选均先重新预览。预览后可共享保存，正式采用需要另行点击。部分选择会重新检查保护范围和业务规则。</p>
      <details><summary>候选目标与阶段界限</summary>
        <p>目标值 {{viewed.candidate.objective.join(' / ')}}</p>
        <p v-for="(bound,index) in viewed.candidate.stage_bounds" :key="index">{{bound.stage}} · {{bound.neighborhood}} · {{bound.status}} · {{bound.detail}} · 目标 {{bound.objective?.join(' / ') ?? '未取得'}} · 下界 {{bound.lower_bound?.join(' / ') ?? '未取得'}} · {{bound.global_proof ? '全局证明' : '仅当前阶段范围'}}</p>
      </details>
      <div aria-label="候选变更选择">
        <label v-for="change in candidateChanges" :key="change.identity" class="candidate-change"><input type="checkbox" v-model="selected" :value="change.identity" :aria-label="`选择候选变更 ${change.identity}`" :disabled="!shared.editor.value || blocked || unsaved || viewOnly" />{{change.description}}</label>
      </div>
      <template v-if="shared.editor.value">
        <p v-if="viewed.candidate.eligibility==='pending_confirmation'">待确认候选可保存为待处理草稿，完成业务确认后再正式采用。</p>
        <el-button @click="stageCandidate(false)" :disabled="blocked || unsaved || viewOnly">预览完整候选</el-button>
        <el-button @click="stageCandidate(true)" :disabled="blocked || unsaved || viewOnly || !selected.length">预览所选候选变更</el-button>
      </template>
      <details v-if="!shared.proposal.value"><summary>查看原始候选业务检查</summary><OutputResults :result="viewed.candidate.evaluation" /></details>
    </section>
  </section>
</template>
<style scoped>
.jobs-panel {margin:24px 0;padding:20px;border:1px solid #d9e1e8;border-radius:8px}
.job-request {display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:12px 0;padding:16px}
.job-request label {display:flex;flex-direction:column;gap:6px}
.job-request input,.job-request select {padding:6px}
.job-list {padding-left:20px}
.job-list li {padding-bottom:12px;border-bottom:1px solid #e6eaee}
.candidate-change {display:block;margin:8px 0}
</style>
