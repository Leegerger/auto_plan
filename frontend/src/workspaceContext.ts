import type { LineStateRecord } from './lineStateTypes'
export function createWorkspaceContext() {
  return {
    week: 1,
    day: '',
    line: '',
    lineFilter: '',
    modelFilter: '',
    selected: [] as string[],
    panelOpen: true,
    panelObject: 'day' as 'day' | 'line_state',
    lineStates: { effectiveAt: '', source: '', batchRecipe: '', reason: '', selectedLines: [] as string[], recipes: {} as Record<string,string>, expected: {} as Record<string,LineStateRecord> },
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
