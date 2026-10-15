<script setup lang="ts">
import type { useSharedPlan } from '../useSharedPlan'
function display(value:unknown):string{
  if(value===null || value===undefined)return '无'
  if(typeof value==='boolean')return value ? '是' : '否'
  if(Array.isArray(value))return value.map(display).join('；')
  if(typeof value==='object')return Object.entries(value).map(([key,item])=>`${key} ${display(item)}`).join('；')
  return String(value)
}
const props=defineProps<{shared:ReturnType<typeof useSharedPlan>}>()
const {recoveryChanges,recoveryInputChanges,recoveryCompareError,localDraftId,localState,localWarning,recoveryRecords,recoveryCandidate,recoveryDifferences,ownsDraft,leaseIssue,busy,unknown,editor}=props.shared
</script>
<template>
  <section v-if="editor" aria-label="本机临时稿恢复">
    <h2>本机临时稿</h2>
    <p>本机编号 {{localDraftId}}。记录按当前路径、账号及草稿号隔离，一般保留 30 天，待核实操作号会保留到核实。只保存操作与原始版本。换电脑前请共享保存 Oracle。</p>
    <p role="status">{{localState}}</p>
    <el-alert v-if="localWarning" :title="localWarning" type="warning" :closable="false" />
    <el-alert v-if="!ownsDraft" :title="leaseIssue || '本机临时稿由其他标签页编辑，当前只读。可明确接管。'" type="warning" :closable="false" />
    <el-button v-if="!ownsDraft" @click="shared.takeOver" :disabled="busy">接管本机临时稿</el-button>
    <template v-if="recoveryCandidate">
      <p>发现本人本机临时稿。选择恢复后会保留原始共享修订与输入版本，须与最新共享状态比较并重新检查。</p>
      <p v-if="recoveryCandidate.problem" role="alert">{{recoveryCandidate.problem}}。原始 JSON 已保留，可导出后处理。</p>
      <el-button @click="shared.recoverLocal" :disabled="busy || !ownsDraft || !recoveryCandidate.record">恢复本机临时稿</el-button>
      <el-button @click="shared.discardLocal()" :disabled="busy || !ownsDraft || unknown">丢弃本机临时稿</el-button>
    </template>
    <el-button v-if="!recoveryCandidate" @click="shared.discardLocal()" :disabled="busy || !ownsDraft || unknown">丢弃本机临时稿</el-button>
    <p v-for="row in recoveryDifferences" :key="row">{{row}}</p>
    <el-alert v-if="recoveryCompareError" :title="`原基准比较未完成：${recoveryCompareError}。完整操作仍保留，可导出 JSON，不会自动套用最新版本。`" type="warning" :closable="false" />
    <el-button v-if="recoveryCompareError" @click="shared.retryComparison" :disabled="busy || !ownsDraft">重试原基准比较</el-button>
    <el-table v-if="recoveryChanges.length" :data="recoveryChanges" role="table" aria-label="本机恢复期间的共享变更">
      <el-table-column prop="date" label="生产日"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="field" label="变更事项"/>
      <el-table-column label="原共享值"><template #default="{row}">{{display(row.before)}}</template></el-table-column><el-table-column label="最新共享值"><template #default="{row}">{{display(row.after)}}</template></el-table-column>
    </el-table>
    <el-table v-if="recoveryInputChanges.length" :data="recoveryInputChanges" role="table" aria-label="本机恢复期间的资料变更">
      <el-table-column prop="path" label="资料或上下文"/><el-table-column label="原值"><template #default="{row}">{{display(row.before)}}</template></el-table-column><el-table-column label="最新值"><template #default="{row}">{{display(row.after)}}</template></el-table-column>
    </el-table>
    <p v-if="recoveryDifferences.length">恢复后尚未共享。预览使用原版本，遇到关联变更会保留动作并列出差异；按最新资料重新预览须由你明确选择。</p>
    <div class="actions">
      <el-button @click="shared.retryLocal" :disabled="busy || !ownsDraft || !!recoveryCandidate">重试本机暂存</el-button>
      <el-button @click="shared.newLocalDraft" :disabled="busy || unknown">新建本机临时稿</el-button>
      <label>切换本机草稿 <select :value="localDraftId" aria-label="本机草稿编号" :disabled="busy || unknown" @change="shared.selectLocalDraft(($event.target as HTMLSelectElement).value)"><option :value="localDraftId">{{localDraftId}}</option><option v-for="row in recoveryRecords.filter(value=>value.id!==localDraftId)" :key="row.id" :value="row.id">{{row.id}}{{row.problem ? '（无法迁移）' : ''}}</option></select></label>
    </div>
    <ul><li v-for="row in recoveryRecords" :key="row.key">{{row.id}} {{row.problem || '本机记录'}} <el-button size="small" @click="shared.exportLocal(row.id)" :aria-label="`导出本机稿 ${row.id} JSON`">导出 JSON</el-button></li></ul>
    <p>未暂存表示仅保留在当前页面；已暂存本机尚未写入 Oracle；共享保存与正式采用分别由服务器确认。断网与保存结果未知时停止编辑。</p>
  </section>
</template>
