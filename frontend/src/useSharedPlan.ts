
import { computed, onMounted, ref } from 'vue'
import { api, ApiError, principal, basePath, online } from './api'
import type { OutputEvaluation } from './outputTypes'
import type { PlanningContext } from './stabilityTypes'
export interface Segment { id: string; date: string; line_code: string; model_code: string; order: number; quantity: number; hours: string }
export interface BusinessOperation {kind:string; action_id?:string; [key:string]:unknown}
export interface Plan {segments:Segment[]; switches:{id:string;line_code:string;from_recipe:string;to_recipe:string;allocations:{date:string;order:number;hours:string;idle_hours:string}[]}[];ordinary_idle:{date:string;line_code:string;hours:string}[]}
export interface Unallocated {id:string;model_code:string;quantity:number;reason:string; [key:string]:unknown}
interface HourDisplay {segments:Record<string,{exact:string;display:string}>;switches:Record<string,{hours:{exact:string;display:string};idle_hours:{exact:string;display:string}}[]>}
interface Head {hour_display:HourDisplay; pending: boolean; kind: string; revision: number; input_version: number; latest_adoption: number; plan: Plan; evaluation: OutputEvaluation; unallocated:Unallocated[]; operation_issues:Record<string,unknown>[]; weeks:{number:number;label:string;start_date:string;dates:string[]}[];context:PlanningContext }
interface PreviewResult {context:PlanningContext;context_changes:Record<string,unknown>[];hour_display:HourDisplay;plan:Plan;evaluation:OutputEvaluation;changes:Record<string,unknown>[];effects:unknown[][];unallocated:Unallocated[];operation_issues:Record<string,unknown>[]}
export interface Draft {data_version:number;base_revision:number;input_version:number;latest_adoption:number;operations:BusinessOperation[];selected_actions?:string[]}
interface Command { base_revision: number; input_version: number; latest_adoption: number; operations: BusinessOperation[]; operation_id: string; pending: boolean }
interface Stored { command: Command; kind: 'save' | 'adopt'; remaining_operations?:BusinessOperation[] }
interface Result { status: 'COMMITTED' | 'ABORTED_VOID'; revision?: number; latest_adoption?: number }

export function useSharedPlan() {
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
const readonly = computed(() => !editor.value || busy.value || unknown.value || !online.value)
const storageKey = `${basePath}:${principal.value?.user_id}:shared-plan:unknown`
function changed() { proposal.value = null; message.value = '本机修改尚未共享，请重新预览' }
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
    if (latest) { await load(false); draftBase.value={base_revision:head.value!.revision,input_version:head.value!.input_version,latest_adoption:head.value!.latest_adoption} }
    const {operation_id: _, pending: __, ...payload} = command()
    const result = await api<PreviewResult>('/plans/preview', {method: 'POST', body: JSON.stringify(payload)})
    proposal.value = result; message.value = '预览完成，尚未共享'
  } catch (e) { showError(e) } finally { busy.value = false }
}
function remember(value: Stored) {
  outstanding.value = value
  // Persist only command operations and versions; no whole-plan copy.
  localStorage.setItem(storageKey, JSON.stringify(value))
}
function clearOutstanding() { localStorage.removeItem(storageKey); outstanding.value = null; unknown.value = false }
async function submit(kind: 'save' | 'adopt') {
  if (readonly.value) return
  busy.value = true; error.value = ''; voided.value = false; conflict.value = null; differences.value = []
  try {
    const request: Stored = {command: command(), kind,remaining_operations:operations.value.filter(operation=>!selectedActions.value.includes(operation.action_id!))}
    // Save the operation ID before sending so a reload can verify a lost response.
    remember(request)
    unknown.value = true
    const result = await api<Result>(`/plans/${kind}`, {method: 'POST', body: JSON.stringify(request.command)})
    clearOutstanding(); await load(); retainUnselected(request.remaining_operations)
    message.value = kind === 'adopt' ? `已正式采用第 ${result.latest_adoption} 版` : `已共享保存修订 ${result.revision}，最近采用版未改变`
  } catch (e) {
    showError(e)
    if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408) clearOutstanding()
    if (e instanceof ApiError && e.data.state === 'busy') clearOutstanding()
  } finally { busy.value = false }
}
async function verify() {
  if (!outstanding.value || busy.value || !online.value) return
  busy.value = true; error.value = ''
  try {
    const result = await api<Result>('/plans/verify', {method: 'POST', body: JSON.stringify({operation_id: outstanding.value.command.operation_id})})
    if (result.status === 'COMMITTED') {
      const remaining=outstanding.value.remaining_operations
      clearOutstanding(); await load(); retainUnselected(remaining); message.value = `已核实前次操作成功，共享修订 ${result.revision}`
    } else {
      clearOutstanding(); voided.value = true; message.value = '已核实前次操作作废，原操作不会再写入。请核对保留的修改后重新预览，再由你发起保存。'
    }
  } catch (e) { showError(e) } finally { busy.value = false }
}
onMounted(async () => {
  busy.value = true
  try {
    const saved = localStorage.getItem(storageKey)
    if (saved) { outstanding.value = JSON.parse(saved) as Stored; unknown.value = true }
    await load()
    if (outstanding.value) {
      const value=outstanding.value
      restoreDraft({data_version:1,...value.command,operations:[...value.command.operations,...(value.remaining_operations ?? [])],selected_actions:value.command.operations.map(operation=>operation.action_id!)})
      pending.value=value.command.pending
    }
  } catch (e) { showError(e) } finally { busy.value = false }
})

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
  if (draft.operations.some(operation=>operation.kind==='replace_plan')) throw new Error('本机临时稿须保存操作，不可保存整份计划')
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
  editor,readonly,load,command,changed,check,submit,verify,appendOperation,operations,localDraft}

}
