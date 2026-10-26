import type { BusinessOperation } from './useSharedPlan'

export const operationNames:Record<string,string>={
  patch_plan:'计划编辑',
  replace_plan:'完整计划调整',
  restore_history:'恢复历史为新草稿',
  record_execution:'记录执行事实',
  confirm_day_capacity:'确认当日剩余容量',
  confirm_line_states:'确认线体状态',
  confirm_switch_continuity:'确认剩余切机连续性',
  weekly_reset:'每周重置',
  lock_cells:'批量锁定',
  unlock_cells:'显式解锁',
  set_preferences:'稳定性偏好',
  change_quantity:'改量顺延',
  append_fill:'追加填量',
  supplement_demand:'按需补排',
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
  if(operation.kind==='confirm_line_states' && Array.isArray(value.rows))parts.push(value.rows.map((row:Record<string,unknown>)=>`${row.line_code} / ${row.effective_at} / ${lineStateDescription(row)}`).join('；'))
  if(operation.kind==='supplement_demand')parts.push(`需求周 ${value.demand_week}`,value.mode==='late'?'显式迟交方案':'按时覆盖')
  if(operation.kind==='confirm_switch_continuity')parts.push(`原作业 ${value.job_id}`,String(value.source ?? ''))
  if(operation.kind==='patch_plan' && Array.isArray(value.switches))parts.push(value.switches.filter((row:Record<string,unknown>)=>row.residual_record_id).map((row:Record<string,unknown>)=>`剩余作业 ${row.id} / ${row.from_recipe} → ${row.to_recipe}`).join('；'))
  return parts.filter(Boolean).join(' / ')
}

export function lineStateDescription(value:unknown):string{
  if(!value || typeof value!=='object')return '尚未确认'
  const row=value as Record<string,unknown>
  if(row.unfinished_switch && typeof row.unfinished_switch==='object'){
    const job=row.unfinished_switch as Record<string,unknown>
    return [`未完成切机 ${job.from_recipe} → ${job.to_recipe}`,`原作业 ${job.job_id}`,`实际开始 ${job.started_at}`,`现场剩余 ${job.remaining_hours} 小时`,job.standard_hours==null?'':`整次标准 ${job.standard_hours} 小时`,job.completed_hours==null?'':`现场已完成 ${job.completed_hours} 小时`].filter(Boolean).join('；')
  }
  return row.recipe_code ? `保留 Recipe ${row.recipe_code}` : '尚未确认'
}

export function displayPlanValue(value:unknown):string{
  if(value===null||value===undefined)return '无'
  if(typeof value==='boolean')return value?'是':'否'
  if(Array.isArray(value))return value.map(displayPlanValue).join('；')
  if(typeof value==='object')return Object.entries(value as Record<string,unknown>).map(([key,item])=>`${key} ${displayPlanValue(item)}`).join('；')
  return String(value)
}
