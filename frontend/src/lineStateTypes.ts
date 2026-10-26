export interface UnfinishedSwitch {
  job_id: string
  started_at: string
  from_recipe: string
  to_recipe: string
  remaining_hours: string
  standard_hours?: string | null
  completed_hours?: string | null
}
export interface ObservedUnfinishedSwitch extends UnfinishedSwitch {
  record_id?: string
  effective_at?: string
  source?: string
  actor?: string
  recorded_at?: string
  remaining_at_week_start_known?: boolean
}
export interface LineStateRecord {
  id: string
  line_code: string
  effective_at: string
  state?: 'retained_recipe' | 'unfinished_switch'
  recipe_code: string | null
  unfinished_switch?: UnfinishedSwitch | null
  source: string
  actor: string
  recorded_at: string
}
export interface LineStateAudit {
  action_id: string
  actor: string
  recorded_at: string
  reason: string | null
  changes: { before: LineStateRecord | null; after: LineStateRecord }[]
}
export interface LineStateContext { records: LineStateRecord[]; audit: LineStateAudit[] }
