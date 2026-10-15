<script setup lang="ts">
import { computed } from 'vue'
import { online } from '../api'
import OutputResults from '../components/OutputResults.vue'
import ManualEditor from '../components/ManualEditor.vue'
import LocalRecovery from '../components/LocalRecovery.vue'
import StabilityPanel from '../components/StabilityPanel.vue'
import ExecutionResetPanel from '../components/ExecutionResetPanel.vue'
import JobsPanel from '../components/JobsPanel.vue'
import HistoryPanel from '../components/HistoryPanel.vue'
import { useSharedPlan } from '../useSharedPlan'
const shared=useSharedPlan()
const {head,preview,proposal,operations,selectedActions,undo,busy,unknown,pending,message,error,differences,conflict,voided,
  editor,readonly,changed,check,submit,verify}=shared
const conflictRows=computed(()=>Array.isArray(conflict.value?.conflicts) ? conflict.value.conflicts.map((key: unknown)=> {
  if (!Array.isArray(key)) return key
  if (key[0]==='CAP') return {kind:'每日容量及 Recipe 衔接',date:key[2],line_code:key[1]}
  if (key[0]==='CELL') return {kind:'同日投入及跨线产出来源',date:key[1],line_code:key[2],model_code:key[3]}
  return {kind:'需求归属及确认额度',model_code:key[1],week:key[2]}
}) : [])
const changeRows=computed(()=>Array.isArray(conflict.value?.changes) ? conflict.value.changes : [])
const operationNames:Record<string,string>={restore_history:'恢复历史为新草稿',record_execution:'累计执行事实及纠错',confirm_day_capacity:'当日剩余容量确认',weekly_reset:'每周重置及更正',lock_cells:'批量锁定',unlock_cells:'显式解锁',set_preferences:'稳定性偏好',change_quantity:'改量顺延',append_fill:'追加填量',swap_segments:'完整交换',move_segment:'移动',split_segment:'拆分',clear_segments:'清空',reorder_segments:'日内排序',copy_segments:'复制新增',overwrite_copy_segments:'覆盖复制',discard_unallocated:'舍弃指定余额'}
const fields:Record<string,string>={date:'生产日',line_code:'线体',model_code:'机种',recipe_code:'Recipe',quantity:'数量',hours:'精确工时',order:'顺序',from_recipe:'原Recipe',to_recipe:'目标Recipe',allocations:'切机分配',idle_hours:'使用普通空闲',id:'记录',reason:'原因',week:'需求周',batch_id:'批次',code:'检查项'}
operationNames.apply_candidate='计算候选'
function operationDescription(value:Record<string,unknown>):string {
  const parts=[value.model_code,value.quantity===undefined ? '' : `${value.quantity}片`,value.line_code,value.start_date,value.end_date,value.target_date,value.target_start_date,value.target_line]
  if(value.kind==='apply_candidate')parts.push(`任务 ${value.job_id}`,`候选 ${value.candidate_id}`,Array.isArray(value.selection) ? `所选 ${value.selection.length} 项` : '完整候选')
  if(Array.isArray(value.segment_ids))parts.push(`所选${value.segment_ids.length}段`)
  if(value.segment_id)parts.push('所选生产段')
  const source=value.source as {kind:string;id:number}|undefined
  if(value.kind==='restore_history'&&source)parts.push(`历史${source.kind==='adoption' ? '采用版' : '修订'} ${source.id}`)
  const scope=value.scope as {start_date:string;end_date:string;line_codes:string[]}|undefined
  if(scope)parts.push(`范围 ${scope.start_date} 至 ${scope.end_date}，${scope.line_codes.join('、')}`)
  return parts.filter(Boolean).join(' / ')
}
function display(value: unknown): string {
  if (value===null || value===undefined) return '无'
  if (typeof value==='boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.map(display).join('；') || '无'
  if (typeof value==='object') {
    const row=value as Record<string,unknown>
    return Object.entries(row).map(([key,value])=>`${fields[key] ?? key} ${display(value)}`).join('；') || '无'
  }
  return String(value)
}
</script>
<template>
  <section>
    <h1>共享计划</h1>
    <p v-if="head">{{ head.pending ? '待处理共享草稿' : head.kind === 'adopt' ? '已采用计划' : '共享草稿' }} · 共享修订 {{ head.revision }} · 输入版本 {{ head.input_version }} · 最近采用版 {{ head.latest_adoption }}</p>
    <el-alert v-if="unknown" title="前次保存结果未知，当前只读。请先核实，避免重复保存或采用。" type="warning" :closable="false" />
    <el-button v-if="unknown" @click="verify" :disabled="busy || !online">核实前次操作</el-button>
    <p v-if="busy" role="status">正在处理，请等待完整结果…</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <p v-if="message" role="status">{{ message }}</p>
    <LocalRecovery :shared="shared" />
    <JobsPanel :shared="shared" />
    <HistoryPanel :shared="shared" />
    <ManualEditor :shared="shared" />
    <StabilityPanel :shared="shared" />
    <ExecutionResetPanel :shared="shared" />
    <section v-if="editor" aria-label="本机完整动作队列">
      <h2>本机动作</h2><p>按完整动作选取后重新预览；交换两端、拆分与复制组保持完整。取消选取会使旧预览失效。</p>
      <label v-for="operation in operations" :key="operation.action_id" class="queued-action"><input type="checkbox" v-model="selectedActions" :value="operation.action_id" :disabled="readonly" @change="changed">{{operationNames[operation.kind] ?? '计划调整'}} · {{operationDescription(operation)}}</label>
      <el-button @click="undo" :disabled="readonly || !operations.length">撤销最后完整动作</el-button>
    </section>
    <div v-if="editor" class="actions">
      <el-checkbox v-model="pending" :disabled="readonly">保存为待处理草稿</el-checkbox>
      <el-button @click="check()" :disabled="readonly || !head">预览本机修改</el-button>
      <el-button type="primary" @click="submit('save')" :disabled="readonly || !preview">共享保存</el-button>
      <el-button type="success" @click="submit('adopt')" :disabled="readonly || !preview || !preview.executable || proposal?.adoption_blocked">正式采用</el-button>
    </div>
    <div v-if="conflict">
      <p>修改已保留。请核对关联变更后，按最新共享状态与资料重新预览。</p>
      <el-table v-if="differences.length" :data="differences"><el-table-column prop="path" label="变更资料" /><el-table-column prop="before" label="原值" /><el-table-column prop="after" label="最新值" /></el-table>
      <el-table v-if="changeRows.length" :data="changeRows" role="table" aria-label="其他用户的计划变更">
        <el-table-column prop="date" label="生产日" /><el-table-column prop="line_code" label="线体" /><el-table-column prop="model_code" label="机种" /><el-table-column prop="field" label="变更项目" />
        <el-table-column label="原值"><template #default="{row}">{{ display(row.before) }}</template></el-table-column>
        <el-table-column label="最新值"><template #default="{row}">{{ display(row.after) }}</template></el-table-column>
      </el-table>
      <el-table v-if="conflictRows.length" :data="conflictRows" role="table" aria-label="关联冲突范围">
        <el-table-column prop="kind" label="关联事项" /><el-table-column prop="date" label="生产日" /><el-table-column prop="line_code" label="线体" /><el-table-column prop="model_code" label="机种" /><el-table-column prop="week" label="需求周" />
      </el-table>
      <el-button @click="check(true)" :disabled="readonly">按最新资料重新预览</el-button>
    </div>
    <p v-if="voided">作废后不会自动重发。下一次点击共享保存或正式采用将使用新的操作编号。</p>
    <section v-if="proposal" aria-label="完整动作影响预览">
      <h2>完整影响预览</h2>
      <el-table role="table" :data="proposal.changes" empty-text="本次没有生产安排变化" aria-label="本次完整计划变更"><el-table-column prop="date" label="生产日"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="field" label="变更事项"/><el-table-column label="调整前"><template #default="{row}">{{display(row.before)}}</template></el-table-column><el-table-column label="调整后"><template #default="{row}">{{display(row.after)}}</template></el-table-column></el-table>
      <details><summary>关联检查范围 ({{proposal.effects.length}})</summary><ul><li v-for="(effect,index) in proposal.effects" :key="index">{{display(effect)}}</li></ul></details>
      <h3>冲突及待确认定位</h3>
      <el-table role="table" :data="preview?.issues ?? []" empty-text="未发现规则问题" aria-label="完整业务问题"><el-table-column prop="date" label="生产日"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="demand_week" label="原需求周"/><el-table-column prop="related_batch_ids" label="相关批次"/><el-table-column prop="reason" label="原因"/></el-table>
      <p v-for="(issue,index) in proposal.operation_issues" :key="index">{{display(issue)}}</p>
    </section>
    <OutputResults v-if="preview" :result="preview" />
  </section>
</template>
