<script setup lang="ts">
import { computed } from 'vue'
import { online } from '../api'
import OutputResults from '../components/OutputResults.vue'
import { useSharedPlan } from '../useSharedPlan'
const {head,rows,preview,busy,unknown,pending,message,error,differences,conflict,voided,
  editor,readonly,changed,check,submit,verify}=useSharedPlan()
const conflictRows=computed(()=>Array.isArray(conflict.value?.conflicts) ? conflict.value.conflicts.map((key: unknown)=> {
  if (!Array.isArray(key)) return key
  if (key[0]==='CAP') return {kind:'每日容量及 Recipe 衔接',date:key[2],line_code:key[1]}
  if (key[0]==='CELL') return {kind:'同日投入及跨线产出来源',date:key[1],line_code:key[2],model_code:key[3]}
  return {kind:'需求归属及确认额度',model_code:key[1],week:key[2]}
}) : [])
const changeRows=computed(()=>Array.isArray(conflict.value?.changes) ? conflict.value.changes : [])
function display(value: unknown): string {
  if (value===null || value===undefined) return '无'
  if (typeof value==='boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.map(display).join('；') || '无'
  if (typeof value==='object') {
    const row=value as Record<string,unknown>
    return [row.date,row.line_code,row.model_code,row.quantity===undefined ? null : `${row.quantity}片`,row.hours===undefined ? null : `${row.hours}小时`].filter(v=>v!==null && v!==undefined).join(' / ') || '相关安排已变化'
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
    <el-table :data="rows" empty-text="尚无共享生产段，请先完成现行计划接入。">
      <el-table-column prop="date" label="生产日" />
      <el-table-column prop="line_code" label="线体" />
      <el-table-column prop="model_code" label="机种" />
      <el-table-column label="投入数量"><template #default="{row}"><input type="number" v-model.number="row.quantity" min="0" step="1" :disabled="readonly" @change="changed" :aria-label="`${row.id}投入数量`" /></template></el-table-column>
      <el-table-column label="生产工时"><template #default="{row}"><el-input v-model="row.hours" :disabled="readonly" @input="changed" :aria-label="`${row.id}生产工时`" /></template></el-table-column>
    </el-table>
    <div v-if="editor" class="actions">
      <el-checkbox v-model="pending" :disabled="readonly">保存为待处理草稿</el-checkbox>
      <el-button @click="check()" :disabled="readonly || !head">预览本机修改</el-button>
      <el-button type="primary" @click="submit('save')" :disabled="readonly || !preview">共享保存</el-button>
      <el-button type="success" @click="submit('adopt')" :disabled="readonly || !preview">正式采用</el-button>
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
    <OutputResults v-if="preview" :result="preview" />
  </section>
</template>
