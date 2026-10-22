import type { Page, Locator, Request, Response } from '@playwright/test'
import type { NavigationRow } from './navigation.mjs'

export interface UiTarget { page: Page; appUrl: string }
export interface Attempt { request: Request; response: Response | null; command: any; result: any; bytes: Buffer | null; status: number | null }
export interface Observation {
  beforeInput?: () => void | Promise<void>
  afterInput?: () => void | Promise<void>
  submitted?: (request: Request) => void | Promise<void>
  responded?: (attempt: Attempt) => void | Promise<void>
  feedback?: () => void | Promise<void>
  step?: <T>(phase: string, work: () => T | Promise<T>) => T | Promise<T>
}
export function openWorkspace(ui: UiTarget): Promise<void>
export function expectLocalActions(ui: UiTarget, count: number): Promise<void>
export function selectRows(ui: UiTarget, options: { source: { time: { window_start: string } }; rows: NavigationRow[]; scope?: any }): Promise<void>
export function undoLocalDraft(ui: UiTarget, options?: { count?: number }, observation?: Observation): Promise<Attempt[]>
export function previewQuantity(ui: UiTarget, options: { source: { time: { window_start: string } }; row: NavigationRow; quantity: number }, observation?: Observation): Promise<Attempt>
export function assertDaily(ui: UiTarget, row: NavigationRow, quantity: number): Promise<void>
export function assertOutput(ui: UiTarget, evaluation: any, parent?: Locator): Promise<void>
export function prepareComputation(ui: UiTarget, request: any): Promise<void>
export function submitComputation(ui: UiTarget, observation?: Observation): Promise<Attempt>
export function hasCandidate(ui: UiTarget, jobId: string): Promise<boolean>
export function viewCandidate(ui: UiTarget, options: { jobId: string; submitted?: { base_revision: number; input_version: number; latest_adoption: number }; reload?: boolean }, observation?: Observation): Promise<Attempt>
export function previewDraft(ui: UiTarget, options?: { latest?: boolean }, observation?: Observation): Promise<Attempt>
export function previewBatch(ui: UiTarget, options: { source: any; rows: NavigationRow[]; scope?: any }, observation?: Observation): Promise<Attempt>
export function undoLastAction(ui: UiTarget, observation?: Observation): Promise<Attempt>
export function waitForEditing(ui: UiTarget): Promise<void>
export function saveLocalDraft(ui: UiTarget, options?: { pending?: boolean }, observation?: Observation): Promise<Attempt>
export function verifyLocalSave(ui: UiTarget, observation?: Observation): Promise<Attempt>
export function reviewComputationSource(ui: UiTarget): Promise<{ status: number; body: Promise<{ bytes: Buffer } | { error: unknown }> }>
