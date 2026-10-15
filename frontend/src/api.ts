import { ref } from 'vue'
export type Role = 'viewer' | 'editor' | 'administrator'
export interface Principal { user_id: string; roles: Role[] }
export interface Authorization extends Principal { active: boolean }
declare global { interface Window { __AUTO_PLAN_CONFIG__?: { basePath: string } } }
export const basePath = window.__AUTO_PLAN_CONFIG__?.basePath ?? '/auto_plan'
export const principal = ref<Principal | null>(null)
export const online = ref(navigator.onLine)
window.addEventListener('online', () => { online.value = true })
window.addEventListener('offline', () => { online.value = false })
export class ApiError extends Error {
  constructor(message: string, public status: number, public data: Record<string, unknown> = {}) { super(message) }
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (!online.value) throw new ApiError('网络已断开，请恢复连接后继续', 0)
  let response: Response
  try {
    response = await fetch(`${basePath}/api${path}`, {
      ...options, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Auto-Plan-Request': '1', ...options.headers },
    })
  } catch { throw new ApiError('网络连接失败，请稍后重试', 0) }
  if (!response.ok) {
    if (response.status === 401) principal.value = null
    let message = '操作失败，请稍后重试'
    let data: Record<string, unknown> = {}
    try { const body = await response.json(); data = body; if (typeof body.detail === 'string') message = body.detail } catch { /* Preserve the useful fallback. */ }
    throw new ApiError(message, response.status, data)
  }
  return response.json() as Promise<T>
}
export async function refreshPrincipal() {
  try { principal.value = await api<Principal>('/auth/me') }
  catch (error) { if (!(error instanceof ApiError) || error.status !== 401) throw error }
}
