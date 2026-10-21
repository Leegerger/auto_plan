import type { Page } from '@playwright/test'
export interface NavigationRow { id: string; date: string; line_code: string; model_code: string; quantity: number }
export interface WeekNavigation { from_week: number; to_week: number; from_date: string; target_date: string; line_code: string }
export interface DayNavigation { from: { date: string; line_code: string; segment: string }; to: { date: string; line_code: string; segment: string } }
export function prepareWeekNavigation(page: Page, head: { weeks: { dates: string[] }[] }, lineCode: string, targetWeek?: number): Promise<WeekNavigation>
export function navigateWeek(page: Page, navigation: WeekNavigation): Promise<void>
export function prepareDayNavigation(page: Page, source: { time: { window_start: string }; plan: { segments: NavigationRow[] } }, row: NavigationRow): Promise<DayNavigation>
export function navigateDay(page: Page, navigation: DayNavigation): Promise<void>
export function assertSelected(page: Page, row: NavigationRow): Promise<void>
export function unselectSegment(page: Page, row: NavigationRow): Promise<void>
