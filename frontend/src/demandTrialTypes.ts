import type { BusinessOperation, PreviewResult, Segment } from './useSharedPlan'
import type { ConfirmationUsage, ExactHoursValue, WeeklyOutput } from './outputTypes'
export interface DemandScope {start_date:string;end_date:string;line_codes:string[];model_codes:string[]}
export interface DemandGoal {
  id:string;model_code:string;demand_week:string;requested_output:number;baseline_attributed:number;desired_attributed:number
  origin_revision:number;origin_input_version:number;mode:string;status:string;current_demand:number|null;current_attributed:number|null
  on_time:number|null;late:number|null;pending_material:number|null;pending_shipping:number|null;remaining_output_target:number|null
  accepted_actions:Record<string,{line_code:string;start_date:string;end_date:string;mode:string;placements:{date:string;quantity:number}[];actual_placements:Segment[];suggested_input:number}>
}
export interface DemandLineage {demand_goals?:Record<string,DemandGoal>;[key:string]:unknown}
export interface SwitchImpact {id:string;line_code:string;from_recipe:string;to_recipe:string;kind:string;total_hours:ExactHoursValue;allocated_hours:ExactHoursValue;idle_hours:ExactHoursValue;extra_hours:ExactHoursValue;standard_hours:ExactHoursValue|null;completed_hours:ExactHoursValue|null}
export interface DemandTrialResult {
  status:'complete'|'partial'|'blocked'|'incomplete'
  basis:{revision:number;input_version:number;latest_adoption:number;as_of:string;window_start:string;digest:string}
  request:{action_id:string;model_code:string;demand_week:string;target_output:number|null;mode:string;line_code:string;start_date:string;end_date:string;scope:DemandScope;placement_scope:DemandScope}
  suggested_input:number|null;scheduled_on_time_output:number|null;valid_on_time_output:number|null;remaining_output_target:number|null
  placements:Segment[];switch_effects:{before:SwitchImpact[];after:SwitchImpact[]}
  week_effects:{model_code:string;week:string;before:WeeklyOutput;after:WeeklyOutput;physical_on_time_before:number;physical_on_time_after:number;physical_late_before:number;physical_late_after:number}[]
  required_confirmations:{model_code:string;week:string;kind:string;pending_output:number;required_input:number|null;earliest_input_date:string|null;latest_output_date:string|null;banned:boolean;usage:ConfirmationUsage[]}[]
  blockers:{code:string;reason:string;date?:string;line_code?:string;model_code?:string}[]
  operation:BusinessOperation|null;retain_operation:BusinessOperation|null;preview:PreviewResult
}
