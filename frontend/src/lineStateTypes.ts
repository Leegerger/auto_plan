export interface LineStateRecord {
  id: string
  line_code: string
  effective_at: string
  recipe_code: string
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
