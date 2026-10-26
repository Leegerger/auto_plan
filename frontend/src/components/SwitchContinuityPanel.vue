<script setup lang="ts">
import { computed, ref } from 'vue'
import type { LineStateRecord } from '../lineStateTypes'
import type { useSharedPlan } from '../useSharedPlan'
import ExactHours from './ExactHours.vue'
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>;record:LineStateRecord}>()
const detail=computed(()=>props.shared.matrixView.value?.switch_continuity?.find(row=>row.line_code===props.record.line_code && row.job_id===props.record.unfinished_switch?.job_id && row.source_record_id===props.record.id))
const form=computed(()=>{
  const key=`${props.record.line_code}/${props.record.unfinished_switch?.job_id}`
  return props.shared.workspace.switchContinuity[key] ??= {source:detail.value?.confirmation?.source ?? '',reason:''}
})
const localConfirmations=computed(()=>props.shared.operations.value.filter(operation=>operation.kind==='confirm_switch_continuity' && operation.line_code===props.record.line_code && operation.job_id===props.record.unfinished_switch?.job_id && props.shared.selectedActions.value.includes(operation.action_id!)))
async function refreshBasis(){
  if(props.shared.readonly.value)return
  const withdrawn=new Set(localConfirmations.value.map(operation=>operation.action_id!))
  if(!withdrawn.size)return
  props.shared.selectedActions.value=props.shared.selectedActions.value.filter(identity=>!withdrawn.has(identity))
  props.shared.changed()
  await props.shared.check(true)
}
const error=ref('')
const labels={unarranged:'剩余作业尚未完整安排',missing:'尚未确认',valid:'确认有效',stale:'原确认已失效，需重新核对'}
async function stage(){
  error.value=''
  const value=detail.value
  if(!value || !value.can_confirm || props.shared.needsPreview.value)return
  if(!form.value.source.trim()){error.value='请填写连续性确认的现场来源';return}
  const replacing=value.confirmation && (value.confirmation.basis_digest!==value.basis_digest || value.confirmation.source!==form.value.source.trim())
  if(replacing && !form.value.reason.trim()){error.value='重新核对连续性必须填写原因';return}
  props.shared.appendOperation({kind:'confirm_switch_continuity',job_id:value.job_id,line_code:value.line_code,source_record_id:value.source_record_id,expected_basis_digest:value.basis_digest,source:form.value.source.trim(),...(value.confirmation ? {expected_confirmation_id:value.confirmation.id} : {}),reason:form.value.reason.trim() || null})
  await props.shared.check()
}
</script>
<template>
  <section aria-label="剩余切机连续性" class="continuity-detail">
    <h4>剩余切机连续性</h4>
    <p>连续性确认只补充现场依据，计划仍须通过容量、日历和保护校核。按生产日、工时及先后顺序表达，不表示具体完成钟点。</p>
    <template v-if="detail">
      <p role="status"><strong>{{labels[detail.status]}}</strong></p>
      <p>原作业 {{detail.job_id}} · {{detail.line_code}} · {{detail.from_recipe}} → {{detail.to_recipe}} · 现场剩余 <ExactHours :value="detail.remaining_hours" /></p>
      <ul v-if="detail.changed_groups.length"><li v-for="group in detail.changed_groups" :key="group">已变化：{{group}}</li></ul>
      <ul v-if="detail.reasons.length"><li v-for="reason in detail.reasons" :key="reason">{{reason}}</li></ul>
      <div class="input-table-scroll"><table class="input-table" aria-label="待确认剩余作业安排"><thead><tr><th>生产日</th><th>日内顺序</th><th>分配精确工时</th><th>其中普通空闲</th></tr></thead><tbody><tr v-for="(row,index) in detail.allocations" :key="index"><td>{{row.date}}</td><td>{{row.order}}</td><td>{{row.hours}} 小时</td><td>{{row.idle_hours}} 小时</td></tr></tbody></table></div>
      <details><summary>本次核对的现场依据</summary><section v-for="group in detail.evidence" :key="group.key"><h5>{{group.title}}</h5><ul><li v-for="(item,index) in group.items" :key="index">{{item}}</li></ul></section></details>
      <p v-if="detail.confirmation">确认来源 {{detail.confirmation.source}}<br>确认人 {{detail.confirmation.actor}}<br>录入时间 {{detail.confirmation.recorded_at}}</p>
    </template>
    <p v-else role="status">{{shared.needsPreview.value ? '连续性依据待重新预览，请先核对最新安排。' : '此记录不是当前待完成作业，请选择最新现场记录核对。'}}</p>
    <section v-if="shared.editor.value && localConfirmations.length && shared.needsPreview.value" aria-label="本机连续性重新核对">
      <p>本作业的本机确认仍使用原来的安排与资料。可取消选取这些旧确认，保留其余本机动作和安排，按最新资料重新预览后再核对现场条件。</p>
      <ul><li v-for="operation in localConfirmations" :key="operation.action_id">本机确认来源 {{operation.source}}</li></ul>
      <el-button :disabled="shared.readonly.value" @click="refreshBasis">取消选取旧本机确认并重新预览</el-button>
    </section>
    <form v-if="shared.editor.value" @submit.prevent="stage">
      <fieldset :disabled="shared.readonly.value" class="line-state-form"><legend>现场连续性核对</legend>
        <label>确认来源 <input v-model="form.source" aria-label="连续性确认来源" placeholder="现场核对的连续作业条件" required></label>
        <label>重新核对原因 <textarea v-model="form.reason" aria-label="连续性重新核对原因" placeholder="替换原确认时填写" /></label>
        <p v-if="error" role="alert">{{error}}</p>
        <el-button native-type="submit" type="primary" :disabled="!detail?.can_confirm || shared.needsPreview.value">{{detail?.confirmation ? '重新核对连续性并预览' : '确认剩余切机可连续执行'}}</el-button>
      </fieldset>
    </form>
  </section>
</template>
