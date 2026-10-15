
import { computed, onMounted, ref } from 'vue'
import { api, ApiError, principal, basePath, online } from './api'
import type { OutputEvaluation } from './outputTypes'
interface Segment { id: string; date: string; line_code: string; model_code: string; order: number; quantity: number; hours: string }
interface Head { pending: boolean; kind: string; revision: number; input_version: number; latest_adoption: number; plan: { segments: Segment[] }; evaluation: OutputEvaluation }
interface Command { base_revision: number; input_version: number; latest_adoption: number; operations: {kind: string; segments: Segment[]}[]; operation_id: string; pending: boolean }
interface Stored { command: Command; kind: 'save' | 'adopt' }
interface Result { status: 'COMMITTED' | 'ABORTED_VOID'; revision?: number; latest_adoption?: number }

export function useSharedPlan() {
const head = ref<Head | null>(null), rows = ref<Segment[]>([]), preview = ref<OutputEvaluation | null>(null)
const busy = ref(false), unknown = ref(false), pending = ref(false), message = ref(''), error = ref('')
const differences = ref<Record<string, unknown>[]>([]), conflict = ref<Record<string, unknown> | null>(null)
const outstanding = ref<Stored | null>(null), voided = ref(false)
const operations = ref<Record<string, unknown>[]>([])
const editor = computed(() => principal.value?.roles.includes('editor'))
const readonly = computed(() => !editor.value || busy.value || unknown.value || !online.value)
const storageKey = `${basePath}:${principal.value?.user_id}:shared-plan:unknown`
function changed() { preview.value = null; message.value = '本机修改尚未共享' }
async function load(replace = true) {
  const current = await api<Head>('/plans/current')
  head.value = current
  if (replace) { rows.value = structuredClone(current.plan.segments); operations.value = [] }
  preview.value = null
}
function command(): Command {
  if (!head.value) throw new Error('请先读取共享计划')
  const original = new Map(head.value.plan.segments.map(row => [row.id, row]))
  const segments = rows.value.filter(row => JSON.stringify(row) !== JSON.stringify(original.get(row.id)))
  return { base_revision: head.value.revision, input_version: head.value.input_version,
    latest_adoption: head.value.latest_adoption, operations: [...operations.value, ...(segments.length ? [{kind: 'patch_plan', segments}] : [])] as Command['operations'],
    operation_id: crypto.randomUUID(), pending: pending.value }
}
function showError(e: unknown) {
  error.value = e instanceof Error ? e.message : '操作失败'
  if (e instanceof ApiError) {
    differences.value = Array.isArray(e.data.input_diff) ? e.data.input_diff : []
    conflict.value = e.status === 409 ? e.data : null
  }
}
async function check(latest = false) {
  if (readonly.value) return
  busy.value = true; error.value = ''; conflict.value = null; differences.value = []
  try {
    if (latest) await load(false)
    const {operation_id: _, pending: __, ...payload} = command()
    const result = await api<{evaluation: OutputEvaluation}>('/plans/preview', {method: 'POST', body: JSON.stringify(payload)})
    preview.value = result.evaluation; message.value = '预览完成，尚未共享'
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
    const request: Stored = {command: command(), kind}
    // Save the operation ID before sending so a reload can verify a lost response.
    remember(request)
    unknown.value = true
    const result = await api<Result>(`/plans/${kind}`, {method: 'POST', body: JSON.stringify(request.command)})
    clearOutstanding(); await load()
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
      clearOutstanding(); await load(); message.value = `已核实前次操作成功，共享修订 ${result.revision}`
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
      const patches = new Map(outstanding.value.command.operations.flatMap(op => op.segments ?? []).map(row => [row.id,row]))
      rows.value = rows.value.map(row => patches.get(row.id) ?? row)
    }
  } catch (e) { showError(e) } finally { busy.value = false }
})

function appendOperation(operation: Record<string, unknown>) {
  operations.value.push(operation); changed()
}
function localDraft() {
  const {operation_id: _, pending: __, ...draft}=command()
  if (draft.operations.some(operation=>operation.kind==='replace_plan')) throw new Error('本机临时稿须保存操作，不可保存整份计划')
  return {data_version:1,...draft}
}
return {head,rows,preview,busy,unknown,pending,message,error,differences,conflict,outstanding,voided,
  editor,readonly,load,command,changed,check,submit,verify,appendOperation,operations,localDraft}

}
