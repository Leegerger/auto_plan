import type { DemandTrialResult } from './demandTrialTypes'
import type { DemandComparisonResult } from './demandComparisonTypes'
import type { LineStateRecord } from './lineStateTypes'
export function createWorkspaceContext() {
  return {
    week: 1,
    day: '',
    line: '',
    lineFilter: '',
    modelFilter: '',
    demand: { expanded: false, modelFilter: '', weekFilter: '', reasonFilter: '', model_code: '', week: '' },
    demandTrial: {open:false,goalsExpanded:false,goalId:'',mode:'on_time' as 'on_time'|'late',modelCode:'',demandWeek:'',targetOutput:'' as string|number,startDate:'',endDate:'',lineCode:'',actionId:'',result:null as DemandTrialResult|null,requestKey:'',notice:''},
    demandComparison: {result:null as DemandComparisonResult|null,selectedLine:'',requestKey:'',notice:''},
    selected: [] as string[],
    panelOpen: true,
    panelObject: 'day' as 'day' | 'line_state',
    lineStates: { state: 'retained_recipe' as 'retained_recipe' | 'unfinished_switch', unfinished: {} as Record<string,{jobId:string;startedAt:string;fromRecipe:string;toRecipe:string;remainingHours:string;standardHours:string;completedHours:string}>, effectiveAt: '', source: '', batchRecipe: '', reason: '', selectedLines: [] as string[], recipes: {} as Record<string,string>, expected: {} as Record<string,LineStateRecord> },
    switchContinuity: {} as Record<string,{source:string;reason:string}>,
    residualSwitch: {recordId:'',allocations:[] as {date:string;order:number;hours:string;idle_hours:string}[]},
    exactHours: '',
    exactHoursSelection: '',
    scopeInitialized: false,
    form: { kind: 'append_fill', model_code: '', quantity: 1, line_code: '', start_date: '', end_date: '', target_date: '', target_line: '', target_order: 0, continue_id: '' },
    scope: { start_date: '', end_date: '', line_codes: [] as string[], model_codes: [] as string[] },
    mapping: {} as Record<string, string>,
    quantityChange: { quantity: 0, deadline: '', selection: '' },
    scroll: { left: 0, top: 0, page: 0 },
  }
}
