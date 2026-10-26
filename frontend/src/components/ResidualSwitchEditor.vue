<script setup lang="ts">
import { ref } from 'vue'
import type { LineStateRecord } from '../lineStateTypes'
import type { useSharedPlan } from '../useSharedPlan'
import SwitchContinuityPanel from './SwitchContinuityPanel.vue'
import UnfinishedSwitchState from './UnfinishedSwitchState.vue'
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>;record:LineStateRecord}>()
const form=props.shared.workspace.residualSwitch
const error=ref('')
function add(){form.allocations.push({date:'',order:0,hours:'',idle_hours:'0'})}
async function stage(){
  error.value=''
  const job=props.record.unfinished_switch
  if(!job)return
  if(!form.allocations.length || form.allocations.some(row=>!row.date || !row.hours.trim())){error.value='请填写剩余作业的生产日和精确工时';return}
  const scope=props.shared.workspace.scope
  if(!scope.line_codes.includes(props.record.line_code) || form.allocations.some(row=>row.date<scope.start_date || row.date>scope.end_date)){error.value='剩余切机安排位于授权调整范围之外，请返回日内详情明确扩大范围后预览';return}
  props.shared.appendOperation({kind:'patch_plan',switches:[{id:job.job_id,line_code:props.record.line_code,from_recipe:job.from_recipe,to_recipe:job.to_recipe,residual_record_id:props.record.id,allocations:form.allocations.map(row=>({...row}))}]})
  await props.shared.check()
}
</script>
<template>
  <section aria-label="剩余切机安排">
    <h4>安排剩余切机</h4>
    <UnfinishedSwitchState v-if="record.unfinished_switch" :job="record.unfinished_switch" :observed-at="record.effective_at" />
    <p>依据 {{record.line_code}} 线体现场记录，状态对应 {{record.effective_at}}。按生产日、工时和先后顺序安排，不表示具体完成钟点。日级容量足够仍需核对连续性。</p>
    <SwitchContinuityPanel :shared="shared" :record="record" />
    <form v-if="shared.editor.value" @submit.prevent="stage">
      <fieldset :disabled="shared.readonly.value" class="line-state-form"><legend>原作业剩余日分配</legend>
        <fieldset v-for="(row,index) in form.allocations" :key="index" class="line-state-form"><legend>分配 {{index+1}}</legend>
          <label>生产日 <input v-model="row.date" type="date" :aria-label="`剩余切机日期 ${index+1}`" required></label>
          <label>日内顺序，从 0 起 <input v-model.number="row.order" type="number" min="0" step="1" :aria-label="`剩余切机顺序 ${index+1}`" required></label>
          <label>分配工时 <input v-model="row.hours" :aria-label="`剩余切机工时 ${index+1}`" placeholder="精确小时，可填分数" required></label>
          <label>其中使用普通空闲 <input v-model="row.idle_hours" :aria-label="`剩余切机空闲工时 ${index+1}`" required></label>
          <el-button :aria-label="`移除剩余切机分配 ${index+1}`" @click="form.allocations.splice(index,1)">移除此日分配</el-button>
        </fieldset>
        <el-button @click="add">增加剩余切机分配日</el-button>
        <p v-if="error" role="alert">{{error}}</p><el-button v-if="error.includes('授权调整范围')" @click="shared.workspace.panelObject='day'">前往调整范围</el-button>
        <el-button native-type="submit" type="primary">加入剩余切机并预览</el-button>
      </fieldset>
    </form>
  </section>
</template>
