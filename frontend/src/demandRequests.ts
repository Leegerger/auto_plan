import { principal } from './api'
import type { useSharedPlan } from './useSharedPlan'

type SharedPlan = ReturnType<typeof useSharedPlan>

export function demandRequestFields(shared: SharedPlan) {
  const form = shared.workspace.demandTrial, scope = shared.workspace.scope
  const { operation_id: _, pending: __, ...command } = shared.command()
  return {
    ...command, action_id: form.actionId, goal_id: form.goalId || null,
    model_code: form.modelCode, demand_week: form.demandWeek,
    target_output: form.targetOutput === '' ? null : Number(form.targetOutput),
    start_date: form.startDate, end_date: form.endDate,
    scope: { start_date: scope.start_date, end_date: scope.end_date,
      line_codes: [...scope.line_codes], model_codes: [...scope.model_codes] },
  }
}

export function demandRequestKey(shared: SharedPlan, payload: () => unknown) {
  const head = shared.head.value
  return JSON.stringify({ actor: principal.value?.user_id, request: head ? payload() : null,
    head: head ? { revision: head.revision, input_version: head.input_version,
      latest_adoption: head.latest_adoption, as_of: head.time.as_of } : null })
}
