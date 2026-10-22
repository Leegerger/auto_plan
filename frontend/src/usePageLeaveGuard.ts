import { onUnmounted } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

const guards = new Set<() => Promise<boolean>>()

export function usePageLeaveGuard(guard: () => Promise<boolean>) {
  guards.add(guard)
  onBeforeRouteLeave(guard)
  onUnmounted(() => { guards.delete(guard) })
}

// Logout ends the session before routing, so it must ask the active page first.
export async function confirmPageLeave() {
  for (const guard of guards) if (!await guard()) return false
  return true
}
