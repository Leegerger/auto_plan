<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ total: number; label: string; disabled?: boolean }>()
const page = defineModel<number>('page', { required: true })
const pageSize = defineModel<number>('pageSize', { required: true })
const first = computed(() => props.total ? (page.value - 1) * pageSize.value + 1 : 0)
const last = computed(() => Math.min(page.value * pageSize.value, props.total))
</script>
<template>
  <nav class="input-pagination" :aria-label="label">
    <span>第 {{ first }} 至 {{ last }} 笔，共 {{ total }} 笔</span>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[20, 50, 100]" :disabled="disabled" layout="sizes,prev,pager,next,jumper" @size-change="page = 1" />
  </nav>
</template>
<style scoped>
.input-pagination { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; margin: 16px 0; }
.input-pagination > span { color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }
</style>
