import type { ExactHoursValue } from './outputTypes'

export interface SwitchContinuityRecord {
  id:string
  job_id:string
  line_code:string
  source_record_id:string
  basis_digest:string
  basis:Record<string,unknown>
  source:string
  actor:string
  recorded_at:string
}
export interface SwitchContinuityContext {
  records:SwitchContinuityRecord[]
  audit:{action_id:string;actor:string;recorded_at:string;reason:string|null;changes:{before:SwitchContinuityRecord|null;after:SwitchContinuityRecord}[]}[]
}
export interface SwitchContinuityView {
  job_id:string
  line_code:string
  source_record_id:string
  from_recipe:string
  to_recipe:string
  remaining_hours:ExactHoursValue
  allocations:{date:string;order:number;hours:string;idle_hours:string}[]
  basis_digest:string
  basis:Record<string,unknown>
  status:'unarranged'|'missing'|'valid'|'stale'
  can_confirm:boolean
  reasons:string[]
  changed_groups:string[]
  evidence:{key:string;title:string;items:string[]}[]
  confirmation:SwitchContinuityRecord|null
}
