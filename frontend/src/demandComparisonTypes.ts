import type { DemandTrialDetails } from './demandTrialTypes'
import type { ExactHoursValue } from './outputTypes'

export interface DemandComparisonRow extends Omit<DemandTrialDetails, 'basis'> {
  line_code:string
  line_name:string
  switch_totals:{before:{total_hours:ExactHoursValue;extra_hours:ExactHoursValue};after:{total_hours:ExactHoursValue;extra_hours:ExactHoursValue}}|null
}
export interface DemandComparisonResult {
  basis:DemandTrialDetails['basis']
  request:Omit<DemandTrialDetails['request'], 'line_code'|'placement_scope'>
  rows:DemandComparisonRow[]
}
