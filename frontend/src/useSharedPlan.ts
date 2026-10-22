import type { ExecutionCell, ResetPreview } from './executionTypes'

import { computed, ref, watch } from 'vue'
import { api, ApiError, principal, basePath, online } from './api'
import type { OutputEvaluation } from './outputTypes'
import { draftLease, draftStorage, validatedDraft } from './localDrafts'
import type { LocalRecord, Outstanding, RecoveryRecord } from './localDrafts'
import type { PlanningContext } from './stabilityTypes'
export interface Segment { id: string; date: string; line_code: string; model_code: string; order: number; quantity: number; hours: string }
export interface BusinessOperation {kind:string; action_id?:string; [key:string]:unknown}
export interface Plan {current_day_witnesses?:Record<string,unknown>[];segments:Segment[]; switches:{id:string;line_code:string;from_recipe:string;to_recipe:string;allocations:{date:string;order:number;hours:string;idle_hours:string}[]}[];ordinary_idle:{date:string;line_code:string;hours:string}[]}
export interface Unallocated {id:string;model_code:string;quantity:number;reason:string; [key:string]:unknown}
interface HourDisplay {segments:Record<string,{exact:string;display:string}>;switches:Record<string,{hours:{exact:string;display:string};idle_hours:{exact:string;display:string}}[]>}
interface Head {time:{as_of:string;window_start:string};reset_preview:ResetPreview|null;execution_segments:{segment_id:string;executed:number;remaining:number}[];execution_surplus:ExecutionCell[];hour_display:HourDisplay; pending: boolean; kind: string; revision: number; input_version: number; latest_adoption: number; plan: Plan; evaluation: OutputEvaluation; unallocated:Unallocated[]; operation_issues:Record<string,unknown>[]; weeks:{number:number;label:string;start_date:string;dates:string[]}[];context:PlanningContext }
interface PreviewResult {reset_reference?:{effective_adoption:number;effective_revision:number}|null;adoption_blocked:boolean;reset_preview:ResetPreview|null;execution_segments:{segment_id:string;executed:number;remaining:number}[];execution_surplus:ExecutionCell[];context:PlanningContext;context_changes:Record<string,unknown>[];hour_display:HourDisplay;plan:Plan;evaluation:OutputEvaluation;changes:Record<string,unknown>[];effects:unknown[][];unallocated:Unallocated[];operation_issues:Record<string,unknown>[]}
export interface Draft {data_version:number;base_revision:number;input_version:number;latest_adoption:number;operations:BusinessOperation[];selected_actions?:string[]}
export interface ResetParticipation {source_adoption:number;model_codes:string[]}
interface Head {reset_participants:{current:ResetParticipation;correction:ResetParticipation|null}}
interface PreviewResult {reset_participants:{current:ResetParticipation;correction:ResetParticipation|null}}
interface Command { base_revision: number; input_version: number; latest_adoption: number; operations: BusinessOperation[]; operation_id: string; pending: boolean }
type Stored = Outstanding
interface Result { status: 'COMMITTED' | 'ABORTED_VOID'; revision?: number; latest_adoption?: number }

let instance: ReturnType<typeof createSharedPlan> | null = null

export function useSharedPlan() {
  instance ??= createSharedPlan()
  return instance
}

function createSharedPlan() {
const head = ref<Head | null>(null), proposal=ref<PreviewResult|null>(null)
const preview=computed(()=>proposal.value?.evaluation ?? null), plan=computed(()=>proposal.value?.plan ?? head.value?.plan), rows=computed(()=>plan.value?.segments ?? [])
const hourDisplay=computed(()=>proposal.value?.hour_display ?? head.value?.hour_display)
const unallocated=computed(()=>proposal.value?.unallocated ?? head.value?.unallocated ?? [])
const draftBase=ref<{base_revision:number;input_version:number;latest_adoption:number}|null>(null)
const busy = ref(false), unknown = ref(false), pending = ref(false), message = ref(''), error = ref('')
const differences = ref<Record<string, unknown>[]>([]), conflict = ref<Record<string, unknown> | null>(null)
const outstanding = ref<Stored | null>(null), voided = ref(false)
const operations = ref<BusinessOperation[]>([]), selectedActions=ref<string[]>([])
const editor = computed(() => principal.value?.roles.includes('editor'))
const localDraftId=ref('main'), localState=ref('尚无本机修改'), localWarning=ref(''), localBytes=ref(0)
const recoveryRecords=ref<RecoveryRecord[]>([]), recoveryCandidate=ref<RecoveryRecord|null>(null), recovered=ref(false)
const recoveryChanges=ref<Record<string,unknown>[]>([]),recoveryInputChanges=ref<Record<string,unknown>[]>([]),recoveryCompareError=ref('')
const ownsDraft=ref(false), leaseIssue=ref(''), storage=ref(draftStorage(basePath,''))
let lease:ReturnType<typeof draftLease>|null=null,stopLease:(()=>void)|undefined,initialized=false,suspendLocal=false,started=false,startPromise:Promise<void>|null=null
const readonly = computed(() => !editor.value || !head.value || busy.value || unknown.value || !online.value || !ownsDraft.value || !!recoveryCandidate.value || (recovered.value && !!recoveryCompareError.value))
const recoveryDifferences=computed(()=>recovered.value && draftBase.value && head.value ? [
  `原共享修订 ${draftBase.value.base_revision} / 当前 ${head.value.revision}`,
  `原输入版本 ${draftBase.value.input_version} / 当前 ${head.value.input_version}`,
  `原最近采用版 ${draftBase.value.latest_adoption} / 当前 ${head.value.latest_adoption}`,
] : [])
function refreshRecords(){try{recoveryRecords.value=storage.value.list()}catch{localWarning.value='无法读取本机记录。页面修改仍可明确保存 Oracle，刷新或关闭后无法保证恢复。'}}
function openLease(){
  stopLease?.();lease?.close();ownsDraft.value=false
  lease=draftLease(storage.value.key(localDraftId.value))
  stopLease=watch([lease.owned,lease.issue],([owned,issue])=>{ownsDraft.value=owned;leaseIssue.value=issue},{immediate:true})
}
function persistLocal(){
  if(suspendLocal || !initialized || !ownsDraft.value || recoveryCandidate.value || !draftBase.value)return false
  try{
    const value:LocalRecord={data_version:2,draft_id:localDraftId.value,updated_at:new Date().toISOString(),draft:localDraft(),pending:pending.value,outstanding:outstanding.value}
    const raw=JSON.stringify(value);localBytes.value=raw.length*2
    storage.value.write(value)
    const prior=recoveryRecords.value.find(row=>row.id===localDraftId.value)
    if(prior && prior.key!==storage.value.key(localDraftId.value))storage.value.remove(prior)
    localWarning.value=localBytes.value>=4*1024*1024 ? '本机临时稿已接近或超过 4 MB，请先共享保存 Oracle，避免浏览器额度不足。' : ''
    localState.value=operations.value.length || outstanding.value ? '已暂存本机，尚未共享' : '尚无本机修改'
    refreshRecords();return true
  }catch(e){
    localState.value='未暂存，页面修改仍保留'
    localWarning.value=`本机暂存失败。${e instanceof Error ? e.message : ''} 请重试暂存或明确共享保存 Oracle。页面内容仍在，刷新或关闭后无法保证恢复。`
    return false
  }
}
function changed() { proposal.value = null; message.value = '本机修改尚未共享，请重新预览'; persistLocal() }
function takeOver(){
  if(busy.value)return
  refreshRecords()
  const row=recoveryRecords.value.find(value=>value.id===localDraftId.value)
  if(row && (!row.record || row.record.draft.operations.length || row.record.outstanding))recoveryCandidate.value=row
  lease?.takeOver()
}
function exportLocal(id:string){
  const row=recoveryRecords.value.find(value=>value.id===id);if(!row)return
  const url=URL.createObjectURL(new Blob([row.raw],{type:'application/json'})),link=document.createElement('a')
  link.href=url;link.download=`auto-plan-${id}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
function resetComparison(){recovered.value=false;recoveryChanges.value=[];recoveryInputChanges.value=[];recoveryCompareError.value=''}
async function retryComparison(){
  if(!ownsDraft.value || busy.value || !online.value)return
  busy.value=true;try{await compareRecovery()}finally{busy.value=false}
}
async function compareRecovery(){
  if(!draftBase.value)return
  recoveryCompareError.value='';recoveryChanges.value=[];recoveryInputChanges.value=[]
  try{
    const result=await api<{plan_changes:Record<string,unknown>[];input_changes:Record<string,unknown>[]}>(`/plans/compare?base_revision=${draftBase.value.base_revision}&input_version=${draftBase.value.input_version}`)
    recoveryChanges.value=result.plan_changes;recoveryInputChanges.value=result.input_changes
  }catch(e){recoveryCompareError.value=e instanceof Error ? e.message : '原基准无法读取。完整操作仍保留，可导出 JSON。'}
}
async function recoverLocal(){
  if(!ownsDraft.value || !recoveryCandidate.value?.record || busy.value || !online.value)return
  const row=recoveryCandidate.value,value=row.record!
  busy.value=true
  try{
    await load(false);suspendLocal=true;recoveryCandidate.value=null;restoreDraft(value.draft);pending.value=value.pending
    outstanding.value=value.outstanding;unknown.value=!!value.outstanding;recovered.value=true;proposal.value=null;suspendLocal=false
    persistLocal()
    await compareRecovery()
    message.value='已恢复原始完整动作。请对照最新共享状态，重新预览检查后再共享。'
  }catch(e){showError(e)}finally{suspendLocal=false;busy.value=false}
}
async function discardLocal(id=localDraftId.value){
  if(!ownsDraft.value || busy.value || unknown.value)return
  const row=recoveryRecords.value.find(value=>value.id===id)
  // An unknown key must be verified even when the rest of a draft is discarded.
  if(row?.record?.outstanding){error.value='前次保存结果须先核实，不能丢弃操作编号';return}
  try{
    if(row)storage.value.remove(row)
    if(id===localDraftId.value){recoveryCandidate.value=null;await load();resetComparison();localState.value='尚无本机修改'}
    refreshRecords()
  }catch(e){showError(e)}
}
function selectLocalDraft(id:string){
  if(busy.value || unknown.value || id===localDraftId.value)return
  if(operations.value.length && !recoveryCandidate.value && !persistLocal()){error.value='当前页面修改尚未暂存，请先重试或共享保存后再切换';return}
  localDraftId.value=id;operations.value=[];selectedActions.value=[];proposal.value=null;resetComparison()
  draftBase.value=head.value ? {base_revision:head.value.revision,input_version:head.value.input_version,latest_adoption:head.value.latest_adoption}:null
  refreshRecords();recoveryCandidate.value=recoveryRecords.value.find(value=>value.id===id) ?? null;openLease()
}
function newLocalDraft(){selectLocalDraft(crypto.randomUUID())}
function retryLocal(){persistLocal()}
watch(pending,()=>{if(initialized)persistLocal()})
async function load(replace = true) {
  const current = await api<Head>('/plans/current')
  head.value = current
  if (replace) { operations.value = []; selectedActions.value=[]; draftBase.value={base_revision:current.revision,input_version:current.input_version,latest_adoption:current.latest_adoption} }
  proposal.value = null
}
function command(): Command {
  if (!head.value) throw new Error('请先读取共享计划')
  return { ...(draftBase.value ?? {base_revision:head.value.revision,input_version:head.value.input_version,latest_adoption:head.value.latest_adoption}),
    operations:operations.value.filter(operation=>selectedActions.value.includes(operation.action_id!)),
    operation_id: crypto.randomUUID(), pending: pending.value }
}
function showError(e: unknown) {
  error.value = e instanceof Error ? e.message : '操作失败'
  if (e instanceof ApiError) {
    differences.value = Array.isArray(e.data.input_diff) ? e.data.input_diff : []
    conflict.value = e.status === 409 ? e.data : null
    if (e.status===409) proposal.value=null
  }
}
async function check(latest = false) {
  if (readonly.value) return
  busy.value = true; error.value = ''; conflict.value = null; differences.value = []; proposal.value=null
  try {
    if (latest) {
      await load(false)
      // Retained candidates stay pinned to their frozen source, including retries.
      if(!operations.value.some(operation=>operation.kind==='apply_candidate'))draftBase.value={base_revision:head.value!.revision,input_version:head.value!.input_version,latest_adoption:head.value!.latest_adoption}
      persistLocal()
    }
    const {operation_id: _, pending: __, ...payload} = command()
    const result = await api<PreviewResult>('/plans/preview', {method: 'POST', body: JSON.stringify(payload)})
    proposal.value = result; message.value = '预览完成，尚未共享'
  } catch (e) { showError(e) } finally { busy.value = false }
}
function remember(value: Stored) { outstanding.value = value; persistLocal() }
function clearOutstanding() { outstanding.value = null; unknown.value = false; persistLocal() }
async function submit(kind: 'save' | 'adopt') {
  if (readonly.value) return
  busy.value = true; error.value = ''; voided.value = false; conflict.value = null; differences.value = []
  try {
    const request: Stored = {command: command(), kind,remaining_operations:operations.value.filter(operation=>!selectedActions.value.includes(operation.action_id!))}
    // Save the operation ID before sending so a reload can verify a lost response.
    remember(request)
    unknown.value = true
    const result = await api<Result>(`/plans/${kind}`, {method: 'POST', body: JSON.stringify(request.command)})
    await load(); retainUnselected(request.remaining_operations); clearOutstanding();resetComparison()
    localState.value=operations.value.length ? localState.value : kind==='adopt' ? '已采用，Oracle 已保存' : '已共享，Oracle 已保存'
    message.value = kind === 'adopt' ? `已正式采用第 ${result.latest_adoption} 版` : `已共享保存修订 ${result.revision}，最近采用版未改变`
  } catch (e) {
    showError(e)
    if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408) clearOutstanding()
    if (e instanceof ApiError && e.data.state === 'busy') clearOutstanding()
  } finally { busy.value = false }
}
async function verify() {
  if (!outstanding.value || busy.value || !online.value || !ownsDraft.value) return
  busy.value = true; error.value = ''
  try {
    const result = await api<Result>('/plans/verify', {method: 'POST', body: JSON.stringify({operation_id: outstanding.value.command.operation_id})})
    if (result.status === 'COMMITTED') {
      const remaining=outstanding.value.remaining_operations
      await load(); retainUnselected(remaining); clearOutstanding();resetComparison(); message.value = `已核实前次操作成功，共享修订 ${result.revision}`
    } else {
      clearOutstanding();proposal.value=null; voided.value = true; message.value = '已核实前次操作作废，原操作不会再写入。请核对保留的修改后重新预览，再由你发起保存。'
    }
  } catch (e) { showError(e) } finally { busy.value = false }
}
function start() {
  if(started)return Promise.resolve()
  if(startPromise)return startPromise
  startPromise=startInternal().finally(()=>{startPromise=null})
  return startPromise
}
async function startInternal() {
  if(!principal.value || !principal.value.roles.some(role=>role==='viewer'||role==='editor'))return
  started=true
  busy.value = true
  storage.value=draftStorage(basePath,principal.value.user_id)
  try {
    refreshRecords()
    const unresolved=recoveryRecords.value.find(value=>value.record?.outstanding)
    if(unresolved)localDraftId.value=unresolved.id
    openLease()
    const row=recoveryRecords.value.find(value=>value.id===localDraftId.value)
    if(row?.record?.outstanding){
      // Preserve the unresolved key immediately; verification is an explicit recovery choice.
      restoreDraft(row.record.draft);pending.value=row.record.pending;outstanding.value=row.record.outstanding;unknown.value=true;recovered.value=true;localState.value='已暂存本机，前次保存结果待核实'
    }else if(row && (!row.record || row.record.draft.operations.length))recoveryCandidate.value=row
    await load(!outstanding.value)
    if(outstanding.value)await compareRecovery()
  } catch (e) { showError(e) } finally { initialized=true;busy.value = false }
}

async function refresh() {
  if(startPromise)await startPromise
  if(!started||busy.value||!online.value||unknown.value||proposal.value||operations.value.length||recoveryCandidate.value)return
  try{await load(true)}catch(e){showError(e)}
}

function stop() {
  stopLease?.();lease?.close();stopLease=undefined;lease=null
  ownsDraft.value=false
  started=false;initialized=false;busy.value=false
  head.value=null;proposal.value=null;operations.value=[];selectedActions.value=[]
  outstanding.value=null;unknown.value=false;pending.value=false
  error.value='';message.value='';conflict.value=null;differences.value=[]
  recoveryCandidate.value=null;recoveryRecords.value=[];recoveryChanges.value=[];recoveryInputChanges.value=[];recoveryCompareError.value=''
  localDraftId.value='main';localState.value='尚无本机修改';localWarning.value='';localBytes.value=0
}

function appendOperation(operation: BusinessOperation) {
  if (readonly.value) return
  const value={...JSON.parse(JSON.stringify(operation)),action_id:operation.action_id ?? crypto.randomUUID()}
  operations.value.push(value); selectedActions.value.push(value.action_id); changed()
}
async function undo() {
  if (readonly.value || !operations.value.length) return
  const last=operations.value.pop()!; selectedActions.value=selectedActions.value.filter(id=>id!==last.action_id)
  changed(); await check()
}
function restoreDraft(draft:Draft) {
  draft=validatedDraft(draft)
  draftBase.value={base_revision:draft.base_revision,input_version:draft.input_version,latest_adoption:draft.latest_adoption}
  operations.value=(JSON.parse(JSON.stringify(draft.operations)) as BusinessOperation[]).map(operation=>({...operation,action_id:operation.action_id ?? crypto.randomUUID()}))
  selectedActions.value=draft.selected_actions ?? operations.value.map(operation=>operation.action_id!); changed()
}
function retainUnselected(values:BusinessOperation[]|undefined) {
  operations.value=values ?? []; selectedActions.value=[]
}
function localDraft() {
  const {operation_id: _, pending: __, ...draft}=command()
  if (operations.value.some(operation=>operation.kind==='replace_plan')) throw new Error('本机临时稿须保存操作，不可保存整份计划')
  return {data_version:1,...draft,operations:JSON.parse(JSON.stringify(operations.value)) as BusinessOperation[],selected_actions:[...selectedActions.value]}
}
return {head,rows,plan,hourDisplay,proposal,unallocated,selectedActions,draftBase,undo,restoreDraft,preview,busy,unknown,pending,message,error,differences,conflict,outstanding,voided,
  editor,readonly,load,command,changed,check,submit,verify,appendOperation,operations,localDraft,
  localDraftId,localState,localWarning,localBytes,recoveryRecords,recoveryCandidate,recoveryDifferences,ownsDraft,leaseIssue,
  recoverLocal,discardLocal,retryLocal,newLocalDraft,selectLocalDraft,exportLocal,takeOver,retryComparison,recoveryChanges,recoveryInputChanges,recoveryCompareError,
  start,stop,refresh}

}
