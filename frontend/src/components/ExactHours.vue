<script setup lang="ts">
defineProps<{ value: { exact: string; display: string } }>()
</script>
<template>
  <span>{{ value.display }} 小时</span>
  <details><summary>查看精确值</summary><code>{{ value.exact }}</code> 小时</details>
</template>
