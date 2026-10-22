<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { categories, fieldName, type InputPreview as Preview } from '../inputFields'
import InputPagination from './InputPagination.vue'

const props = defineProps<{ preview: Preview }>()
const selected = ref('')
const page = ref(1)
const pageSize = ref(20)
const errorPage = ref(1)
const errorPageSize = ref(20)
const tables = computed(() => Object.entries(props.preview.changes).filter(([, rows]) => rows.length > 0))
const category = computed(() => categories.find(item => item.kind === selected.value))
const rows = computed(() => props.preview.changes[selected.value] ?? [])
const visibleRows = computed(() => rows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
const visibleErrors = computed(() => props.preview.errors.slice((errorPage.value - 1) * errorPageSize.value, errorPage.value * errorPageSize.value))

function title(kind: string) { return categories.find(item => item.kind === kind)?.title ?? kind }

watch(() => props.preview, () => {
  selected.value = tables.value[0]?.[0] ?? ''
  page.value = 1
  errorPage.value = 1
}, { immediate: true })
watch(selected, () => { page.value = 1 })
</script>

<template>
  <div class="input-preview">
    <h2>整批预览</h2>
    <p v-if="preview.valid">预览通过，尚未提交</p>
    <p v-else>发现 {{ preview.errors.length }} 个错误，整批未提交。</p>
    <template v-if="preview.errors.length">
      <InputPagination v-model:page="errorPage" v-model:page-size="errorPageSize" :total="preview.errors.length" label="导入错误上方分页" />
      <div class="input-table-scroll">
        <table aria-label="导入错误" class="input-table">
          <thead><tr><th>工作表</th><th>源行</th><th>字段</th><th>原因</th></tr></thead>
          <tbody><tr v-for="(item,index) in visibleErrors" :key="index"><td>{{ title(item.sheet) }}</td><td>{{ item.row }}</td><td>{{ fieldName(item.field) }}</td><td>{{ item.reason }}</td></tr></tbody>
        </table>
      </div>
      <InputPagination v-model:page="errorPage" v-model:page-size="errorPageSize" :total="preview.errors.length" label="导入错误下方分页" />
    </template>
    <p v-if="preview.valid && tables.length === 0">本批没有资料行，当前资料保持原值。</p>
    <template v-if="tables.length">
      <ul class="preview-summary" aria-label="整批变更摘要">
        <li v-for="[kind, changed] in tables" :key="kind">{{ title(kind) }} ({{ changed.length }} 行)</li>
      </ul>
      <label class="preview-category">预览资料类别
        <select v-model="selected" aria-label="预览资料类别">
          <option v-for="[kind, changed] in tables" :key="kind" :value="kind">{{ title(kind) }} ({{ changed.length }} 行)</option>
        </select>
      </label>
      <h3>{{ title(selected) }} ({{ rows.length }} 行)</h3>
      <InputPagination v-model:page="page" v-model:page-size="pageSize" :total="rows.length" label="整批预览明细上方分页" />
      <div class="input-table-scroll">
        <table :aria-label="`${selected} 预览`" class="input-table">
          <thead><tr><th v-for="field in category?.fields" :key="field">{{ fieldName(field) }}</th></tr></thead>
          <tbody><tr v-for="(row,index) in visibleRows" :key="index"><td v-for="field in category?.fields" :key="field">{{ typeof row[field] === 'boolean' ? (row[field] ? '是' : '否') : row[field] }}</td></tr></tbody>
        </table>
      </div>
      <InputPagination v-model:page="page" v-model:page-size="pageSize" :total="rows.length" label="整批预览明细下方分页" />
    </template>
  </div>
</template>

<style scoped>
.preview-summary { display: flex; flex-wrap: wrap; gap: 8px 24px; padding-left: 20px; }
.preview-category { display: flex; align-items: center; gap: 8px; }
</style>
