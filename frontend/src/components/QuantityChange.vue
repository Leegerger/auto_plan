<script setup lang="ts">
import { computed, ref, toRefs, watch } from 'vue'
import { useSharedPlan } from '../useSharedPlan'

const props=defineProps<{
  shared:ReturnType<typeof useSharedPlan>
  selected:string[]
  scope:{start_date:string;end_date:string;line_codes:string[];model_codes:string[]}
}>()
const values=props.shared.workspace.quantityChange
const {quantity,deadline}=toRefs(values), error=ref('')
const chosen=computed(()=>props.selected.length===1 ? props.shared.rows.value.find(row=>row.id===props.selected[0]) : undefined)
watch(()=>props.selected.join(','),()=>{
  const key=props.selected.join(',')
  if(key!==values.selection){if(chosen.value)quantity.value=chosen.value.quantity;values.selection=key}
  error.value=''
},{immediate:true})
watch(()=>props.scope.end_date,value=>{if(!deadline.value)deadline.value=value},{immediate:true})
async function stage(){
  error.value=''
  if(!chosen.value){error.value='请选择一段生产安排后改量';return}
  if(!Number.isSafeInteger(quantity.value) || quantity.value<0){error.value='改量后总量必须为非负整数';return}
  if(deadline.value<chosen.value.date || deadline.value>props.scope.end_date){error.value='顺延截止日必须从当前段日期开始，并位于授权范围内';return}
  props.shared.appendOperation({kind:'change_quantity',segment_id:chosen.value.id,quantity:quantity.value,end_date:deadline.value,
    scope:{...props.scope,line_codes:[...props.scope.line_codes],model_codes:[...props.scope.model_codes]}})
  await props.shared.check()
}
</script>
<template>
  <form v-if="shared.editor.value" @submit.prevent="stage" aria-label="改量与完整顺延链">
    <fieldset :disabled="shared.readonly.value"><legend>改量顺延</legend>
      <p v-if="chosen">当前段：{{chosen.date}} / {{chosen.line_code}} / {{chosen.model_code}}，原总量 {{chosen.quantity}} 片。</p>
      <p v-else>在日内详情中选择一段生产安排。其他已选段须先取消选择。</p>
      <div class="input-toolbar">
        <label>改量后总量 <input type="number" min="0" step="1" v-model.number="quantity" aria-label="改量后总量" required></label>
        <label>同线顺延截止日 <input type="date" v-model="deadline" :min="chosen?.date" :max="scope.end_date" aria-label="顺延截止日" required></label>
        <el-button native-type="submit" type="primary" :disabled="shared.readonly.value || !chosen">预览完整改量顺延</el-button>
      </div>
      <p>总量包含已执行部分。增量优先安排当前段，后续未锁定安排只在原线体与截止日内顺延；保护数量保持不变。减量不提前后续计划。</p>
      <p>整条顺延链为一个动作。下方完整预览列出逐日变更和待确认产出，未排下的投入单列保留。</p>
      <p v-if="error" role="alert">{{error}}</p>
    </fieldset>
  </form>
</template>
