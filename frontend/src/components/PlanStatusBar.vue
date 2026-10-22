<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { BadgeCheck, CircleAlert, ListChecks, PanelRightOpen, RefreshCw, Save, Undo2 } from '@lucide/vue'
import { useRoute } from 'vue-router'
import { principal } from '../api'
import { displayPlanValue as display, operationDescription, operationNames } from '../planPresentation'
import { useSharedPlan } from '../useSharedPlan'
import LocalRecovery from './LocalRecovery.vue'
import OutputResults from './OutputResults.vue'

const shared=useSharedPlan()
const route=useRoute()
const {head,proposal,preview,operations,selectedActions,pending,busy,unknown,message,error,differences,conflict,outstanding,voided,editor,readonly,recoveryCandidate,localState}=shared
const drawer=ref(false),tab=ref('actions')
const business=computed(()=>principal.value?.roles.some(role=>role==='viewer'||role==='editor')??false)
const onPlanWorkspace=computed(()=>route.path==='/schedule')
const canPreview=computed(()=>onPlanWorkspace.value&&editor.value&&!readonly.value&&!!head.value)
const canSave=computed(()=>onPlanWorkspace.value&&editor.value&&!readonly.value&&!!proposal.value)
const canAdopt=computed(()=>canSave.value&&!proposal.value?.adoption_blocked&&!!preview.value?.executable)
const selectedHighRisk=computed(()=>operations.value.filter(operation=>selectedActions.value.includes(operation.action_id!)&&['clear_segments','overwrite_copy_segments','discard_unallocated','weekly_reset'].includes(operation.kind)))
const stateLabel=computed(()=>{
  if(unknown.value)return '保存结果未知'
  if(proposal.value)return '预览完成'
  if(operations.value.length)return '本机临时稿'
  if(head.value?.pending)return '待处理共享草稿'
  if(head.value?.kind==='adopt')return '采用版计划'
  return head.value?'共享草稿':'尚未读取计划'
})
const stateTone=computed(()=>unknown.value?'danger':proposal.value?'ready':operations.value.length?'local':'shared')
const conflictRows=computed(()=>Array.isArray(conflict.value?.conflicts) ? conflict.value.conflicts.map((key:unknown)=>Array.isArray(key)&&key[0]==='CAP'?`每日容量及 Recipe 衔接 · ${key[2]} · ${key[1]}`:Array.isArray(key)&&key[0]==='CELL'?`同日投入及跨线产出来源 · ${key[1]} · ${key[2]} · ${key[3]}`:Array.isArray(key)&&key[0]==='DEM'?`需求归属及确认额度 · ${key[1]} · ${key[2]}`:String(key)) : [])
const changeRows=computed(()=>Array.isArray(conflict.value?.changes)?conflict.value.changes as Record<string,unknown>[]:[])
const proposalChanges=computed(()=>proposal.value?.changes??[])
const proposalIssues=computed(()=>preview.value?.issues??[])
const changeGroups=computed(()=>{
  const groups=new Map<string,Record<string,unknown>[]>()
  for(const change of proposalChanges.value){
    const key=[change.date,change.line_code,change.model_code].filter(Boolean).join(' / ')||'计划全局'
    const rows=groups.get(key)??[]
    rows.push(change)
    groups.set(key,rows)
  }
  return [...groups.entries()].map(([label,rows])=>({label,rows}))
})
async function check(latest=false){
  if(!onPlanWorkspace.value)return
  await shared.check(latest)
  if(proposal.value){tab.value='preview';drawer.value=true}
}
async function submit(kind:'save'|'adopt'){
  if(!onPlanWorkspace.value)return
  if(kind==='adopt'||selectedHighRisk.value.length){
    try{
      const riskNames=selectedHighRisk.value.map(operation=>operationNames[operation.kind]??operation.kind)
      await ElMessageBox.confirm(
        kind==='adopt'
          ? `正式采用后，共享修订 ${head.value?.revision??'—'} 将成为新的稳定性比较基线。${riskNames.length?`本次包含：${riskNames.join('、')}。`:''}该动作不会覆盖历史版本。`
          : `本次完整预览包含：${riskNames.join('、')}。请确认已核对影响范围，再共享保存。`,
        kind==='adopt'?'确认正式采用':'确认高风险变更',
        {type:'warning',confirmButtonText:kind==='adopt'?'正式采用':'共享保存',cancelButtonText:'返回核对',autofocus:false},
      )
    }catch{return}
  }
  await shared.submit(kind)
  if(!unknown.value&&!error.value)drawer.value=false
}
watch(unknown,value=>{if(value){tab.value='actions';drawer.value=true}},{immediate:true})
watch(unknown,(value,before)=>{if(before&&!value&&!recoveryCandidate.value)drawer.value=false})
watch(conflict,value=>{if(value){tab.value='actions';drawer.value=true}})
</script>

<template>
  <section v-if="business" class="plan-status" aria-label="计划状态">
    <div class="plan-status-main">
      <span class="state-pill" :class="`state-${stateTone}`"><span class="state-dot" />{{ stateLabel }}</span>
      <dl class="plan-facts">
        <div><dt>采用版</dt><dd>{{ head?.latest_adoption || '尚未建立' }}</dd></div>
        <div><dt>共享修订</dt><dd>{{ head?.revision ?? '—' }}</dd></div>
        <div><dt>输入版本</dt><dd>{{ head?.input_version ?? '—' }}</dd></div>
        <div><dt>本机动作</dt><dd>{{ operations.length }}</dd></div>
      </dl>
      <span class="local-state">{{ localState }}</span>
    </div>
    <div class="plan-status-actions">
      <el-button v-if="unknown && !drawer" type="warning" @click="shared.verify()" :disabled="busy || !shared.ownsDraft.value">
        <RefreshCw :size="16" />核实前次操作
      </el-button>
      <el-button v-if="editor" @click="tab='actions';drawer=true">
        <PanelRightOpen :size="16" />本机临时稿<span v-if="operations.length" class="action-count">{{ operations.length }}</span>
      </el-button>
      <router-link v-if="editor && !onPlanWorkspace && operations.length" to="/schedule"><el-button>前往计划工作区</el-button></router-link>
      <el-button v-if="editor && onPlanWorkspace && !drawer" :disabled="!canPreview" @click="check()">
        <ListChecks :size="16" />{{ operations.length ? '预览本机修改' : '预览当前计划' }}
      </el-button>
      <el-button v-if="proposal && onPlanWorkspace && !drawer" @click="drawer=true;tab='preview'">查看预览</el-button>
      <el-button v-if="editor && proposal && onPlanWorkspace && !drawer" type="primary" :disabled="!canSave" @click="submit('save')">
        <Save :size="16" />共享保存
      </el-button>
      <el-button v-if="editor && proposal && onPlanWorkspace && !drawer" type="success" :disabled="!canAdopt" @click="submit('adopt')">
        <BadgeCheck :size="16" />正式采用
      </el-button>
    </div>
    <div v-if="unknown || error || message || voided" class="plan-feedback" role="status">
      <CircleAlert v-if="unknown || error" :size="16" />
      <span v-if="unknown">前次保存结果未知，当前只读。请先核实，避免重复保存或采用。</span>
      <span v-if="error">{{ error }}</span>
      <span v-if="message">{{ message }}</span>
      <span v-if="voided">前次操作已作废，原修改仍保留；请重新预览后再保存。</span>
    </div>
  </section>

  <el-drawer v-model="drawer" size="720px" :with-header="true" :title="tab==='preview'?'变更预览':'本机临时稿'" append-to-body>
    <el-tabs v-model="tab" class="plan-drawer-tabs">
      <el-tab-pane label="本机动作" name="actions">
        <el-alert v-if="unknown" title="前次保存结果未知，当前只读。请先核实原操作编号。" type="warning" :closable="false" />
        <el-button v-if="unknown" type="warning" @click="shared.verify()" :disabled="busy || !shared.ownsDraft.value"><RefreshCw :size="16" />核实前次操作</el-button>
        <LocalRecovery :shared="shared" />
        <section v-if="conflict" class="conflict-review" aria-label="共享修订冲突">
          <el-alert title="其他用户已修改关联范围，请核对差异" type="warning" :closable="false" />
          <p>修改已保留。请核对关联变更后，按最新共享状态与资料重新预览。</p>
          <div v-if="differences.length" role="table" aria-label="资料差异">
            <el-table :data="differences"><el-table-column prop="path" label="变更资料"/><el-table-column prop="before" label="原值"/><el-table-column prop="after" label="最新值"/></el-table>
          </div>
          <div v-if="changeRows.length" role="table" aria-label="其他用户的计划变更">
            <el-table :data="changeRows">
              <el-table-column prop="date" label="生产日"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="field" label="变更项目"/>
              <el-table-column label="原值"><template #default="{row}">{{ display(row.before) }}</template></el-table-column>
              <el-table-column label="最新值"><template #default="{row}">{{ display(row.after) }}</template></el-table-column>
            </el-table>
          </div>
          <ul v-if="conflictRows.length" class="conflict-ranges"><li v-for="row in conflictRows" :key="row">{{ row }}</li></ul>
          <router-link v-if="!onPlanWorkspace" to="/schedule"><el-button type="primary">前往计划工作区</el-button></router-link>
          <el-button v-else @click="check(true)" :disabled="readonly">按最新资料重新预览</el-button>
        </section>
        <section v-if="editor" class="operation-queue" aria-label="本机完整动作队列">
          <div class="section-heading">
            <div><p class="eyebrow">尚未写入共享版本</p><h2>本机动作</h2></div>
            <el-button :disabled="readonly || !onPlanWorkspace || !operations.length" @click="shared.undo()"><Undo2 :size="16" />撤销最后完整动作</el-button>
          </div>
          <p class="muted">完整动作按顺序组合。取消选取会使旧预览失效。</p>
          <label v-for="operation in operations" :key="operation.action_id" class="queued-action">
            <input v-model="selectedActions" :value="operation.action_id" type="checkbox" :disabled="readonly || !onPlanWorkspace" @change="shared.changed()">
            <span><strong>{{ operationNames[operation.kind] ?? '计划调整' }}</strong><small>{{ operationDescription(operation) || '调整完整计划' }}</small></span>
          </label>
          <p v-if="!operations.length" class="empty-inline">当前没有本机动作。</p>
        </section>
        <div class="drawer-actions">
          <el-checkbox v-if="onPlanWorkspace" v-model="pending" :disabled="readonly">保存为待处理草稿</el-checkbox>
          <el-button @click="drawer=false">继续编辑</el-button>
          <router-link v-if="!onPlanWorkspace && operations.length" to="/schedule"><el-button type="primary">前往计划工作区</el-button></router-link>
          <el-button v-if="onPlanWorkspace" type="primary" :disabled="!canPreview" @click="check()">预览本机修改</el-button>
        </div>
      </el-tab-pane>
      <el-tab-pane label="变更预览" name="preview">
        <div v-if="!onPlanWorkspace" class="empty-state compact">
          <ListChecks :size="28" />
          <h2>预览仅在计划工作区处理</h2>
          <router-link to="/schedule"><el-button type="primary">前往计划工作区</el-button></router-link>
        </div>
        <div v-else-if="!proposal" class="empty-state compact">
          <ListChecks :size="28" />
          <h2>尚无有效预览</h2>
          <p>选择本机动作并完成预览后，这里会显示影响摘要、冲突与完整差异。</p>
          <el-button @click="tab='actions'">返回本机动作</el-button>
        </div>
        <template v-else>
          <div class="preview-summary">
            <div><span>计划变化</span><strong>{{ proposalChanges.length }}</strong></div>
            <div><span>业务问题</span><strong>{{ proposalIssues.length }}</strong></div>
            <div><span>关联范围</span><strong>{{ proposal.effects?.length ?? 0 }}</strong></div>
            <div><span>采用状态</span><strong>{{ proposal.adoption_blocked ? '存在阻断' : '可继续核对' }}</strong></div>
          </div>
          <el-alert v-if="proposal.adoption_blocked" title="当前预览存在阻断正式采用的事项。可保存为待处理共享草稿并继续核对。" type="warning" :closable="false" />
          <section aria-label="完整动作影响预览">
            <h2>计划变更</h2>
            <div role="table" aria-label="本次完整计划变更">
              <p v-if="!changeGroups.length" class="empty-inline">本次没有生产安排变化。</p>
              <details v-for="group in changeGroups" :key="group.label" class="change-group" open>
                <summary>{{ group.label }} · {{ group.rows.length }} 项</summary>
                <el-table :data="group.rows">
                  <el-table-column prop="field" label="变更事项"/>
                  <el-table-column label="调整前"><template #default="{row}">{{ display(row.before) }}</template></el-table-column>
                  <el-table-column label="调整后"><template #default="{row}">{{ display(row.after) }}</template></el-table-column>
                </el-table>
              </details>
            </div>
          </section>
          <section aria-label="完整业务问题">
            <h2>冲突及待确认事项</h2>
            <div role="table" aria-label="完整业务问题">
              <el-table :data="proposalIssues" empty-text="未发现规则问题">
                <el-table-column prop="date" label="生产日"/><el-table-column prop="line_code" label="线体"/><el-table-column prop="model_code" label="机种"/><el-table-column prop="demand_week" label="原需求周"/><el-table-column prop="related_batch_ids" label="相关批次"/><el-table-column prop="reason" label="原因"/>
              </el-table>
            </div>
          </section>
          <section v-if="proposal.reset_preview" aria-label="重置周目标">
            <h2>每周重置影响</h2>
            <p class="muted">计算来源采用版 {{proposal.reset_preview.source_adoption}} · 稳定性比较采用版 {{proposal.reset_preview.comparison_adoption}} · 快照批次 {{proposal.reset_preview.snapshot.batch_id}} 修订 {{proposal.reset_preview.batch_revision}}</p>
            <div role="table" aria-label="重置周目标与未完成调整">
              <el-table :data="proposal.reset_preview.targets">
                <el-table-column prop="model_code" label="机种"/><el-table-column prop="week" label="原归属周"/><el-table-column prop="source_input" label="来源投入"/><el-table-column prop="incoming_balance" label="净余额或自然结转"/><el-table-column prop="target" label="周目标"/><el-table-column prop="actual" label="实际投入"/><el-table-column prop="remaining_target" label="剩余目标"/><el-table-column prop="planned_remaining" label="有效待执行"/><el-table-column prop="natural_carry" label="自然结转"/><el-table-column prop="unmet_increase" label="未完成应补"/><el-table-column prop="unmet_reduction" label="未完成应减"/><el-table-column prop="actual_over_target" label="实际超目标"/>
              </el-table>
            </div>
          </section>
          <details v-if="proposal.effects?.length" class="audit-details"><summary>关联检查范围（{{ proposal.effects.length }}）</summary><ul><li v-for="(effect,index) in proposal.effects" :key="index">{{ display(effect) }}</li></ul></details>
          <details v-if="proposal.operation_issues?.length" class="audit-details"><summary>动作处理说明（{{ proposal.operation_issues.length }}）</summary><p v-for="(issue,index) in proposal.operation_issues" :key="index">{{ display(issue) }}</p></details>
          <OutputResults v-if="preview" :result="preview" />
          <div class="drawer-actions sticky-actions">
            <el-checkbox v-model="pending" :disabled="readonly">保存为待处理草稿</el-checkbox>
            <el-button @click="drawer=false">继续编辑</el-button>
            <el-button type="primary" :disabled="!canSave" @click="submit('save')"><Save :size="16" />共享保存</el-button>
            <el-button type="success" :disabled="!canAdopt" @click="submit('adopt')"><BadgeCheck :size="16" />正式采用</el-button>
          </div>
        </template>
      </el-tab-pane>
    </el-tabs>
  </el-drawer>
</template>
