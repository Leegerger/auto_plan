import type { UnfinishedSwitch } from './lineStateTypes'
import type { ExactHoursValue } from './outputTypes'

export interface WorkspaceDay {
  date: string
  line_code: string
  calendar_hours: ExactHoursValue
  available_hours: ExactHoursValue
  remaining_hours: ExactHoursValue
  production_hours: ExactHoursValue
  switch_hours: ExactHoursValue
  occupied_hours: ExactHoursValue
  holiday: boolean
  opening_exception: boolean
  maintenance_hours: number
  experiment_hours: number
  basis: 'calendar' | 'confirmed_remaining_day'
  locks: { model_code: string; quantity: number }[]
  issues: string[]
  unfinished_switch?: (UnfinishedSwitch & {record_id:string}) | null
  residual_switches?: {id:string;job_id:string;residual_record_id:string;from_recipe:string;to_recipe:string;allocated_hours:ExactHoursValue}[]
}

export interface WorkspaceLineWeek {
  week: string
  line_code: string
  recipe_code: string | null
  state: 'retained_recipe' | 'unknown' | 'planned_switch' | 'unfinished_switch'
  source: 'window_plan_start' | 'plan_projection' | 'confirmed_state'
  unfinished_switch?: (UnfinishedSwitch & {record_id:string}) | null
  new_switch_count?: number
  carried_switch_count?: number
  switch_hours?: ExactHoursValue
  planned_switch: { id: string; from_recipe: string; to_recipe: string } | null
  current_confirmation: { observed_at: string; recipe_code: string|null; state?:'retained_recipe'|'unfinished_switch';unfinished_switch?:UnfinishedSwitch|null; valid: boolean; effective_at?:string;source?:string;actor?:string;recorded_at?:string;record_id?:string } | null
  first_production: {
    segment_id: string
    date: string
    order: number
    recipe_code: string
    from_recipe: string | null
    duration_state: 'known' | 'default_missing' | 'same_recipe' | 'unknown_initial'
    required_hours: ExactHoursValue
    planned_switch_ids: string[]
    arranged: boolean
    issues: string[]
  } | null
}

export interface WorkspaceCapacity {
  month: string
  line_code: string
  recipe_code: string
  state: 'known' | 'zero' | 'missing' | 'forbidden'
  full_day_capacity: number
}

export interface WorkspaceView {
  days: WorkspaceDay[]
  line_weeks: WorkspaceLineWeek[]
  capacities: WorkspaceCapacity[]
}
