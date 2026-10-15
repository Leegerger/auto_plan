export interface PlanningPreferences {near_weeks:number;near_tolerance_percent:string;far_tolerance_percent:string;near_week_weight:string;far_week_weight:string}
export interface LockedCell {date:string;line_code:string;model_code:string;quantity:number}
export interface PlanningContext {locks:LockedCell[];preferences:PlanningPreferences}
export interface StabilityWeek {week:string;number:number;band:'near'|'far';state:'new_plan'|'compared'|'zero_baseline'|'partial'|'unknown_execution';old_remaining:number;new_remaining:number;absolute_change:number;added:number;rate:string|null;rate_display:string;tolerance_percent:string;exceeds_tolerance:boolean;baseline_complete:boolean;unknown_baseline_cells:number;unknown_execution_cells:number}
export interface StabilityResult {baseline_version:number|null;weeks:StabilityWeek[];weighted_cost:string;order_change_count:number;comparison_complete:boolean}
