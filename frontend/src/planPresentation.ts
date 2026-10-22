import type { BusinessOperation } from './useSharedPlan'

export const operationNames:Record<string,string>={
  patch_plan:'计划编辑',
  replace_plan:'完整计划调整',
  restore_history:'恢复历史为新草稿',
  record_execution:'记录执行事实',
  confirm_day_capacity:'确认当日剩余容量',
  weekly_reset:'每周重置',
  lock_cells:'批量锁定',
  unlock_cells:'显式解锁',
  set_preferences:'稳定性偏好',
  change_quantity:'改量顺延',
  append_fill:'追加填量',
  swap_segments:'交换生产段',
  move_segment:'移动生产段',
  split_segment:'拆分生产段',
  clear_segments:'清空生产段',
  reorder_segments:'日内排序',
  copy_segments:'复制新增',
  overwrite_copy_segments:'覆盖复制',
  discard_unallocated:'舍弃未分配投入',
  apply_candidate:'套用计算候选',
}

export function operationDescription(operation:BusinessOperation):string{
  const value=operation as Record<string,unknown>
  const parts=[value.model_code,value.quantity===undefined?'':`${value.quantity}片`,value.line_code,value.date,value.start_date,value.end_date,value.target_date]
  if(Array.isArray(value.segment_ids))parts.push(`所选 ${value.segment_ids.length} 段`)
  if(value.segment_id)parts.push('所选生产段')
  return parts.filter(Boolean).join(' / ')
}

export function displayPlanValue(value:unknown):string{
  if(value===null||value===undefined)return '无'
  if(typeof value==='boolean')return value?'是':'否'
  if(Array.isArray(value))return value.map(displayPlanValue).join('；')
  if(typeof value==='object')return Object.entries(value as Record<string,unknown>).map(([key,item])=>`${key} ${displayPlanValue(item)}`).join('；')
  return String(value)
}
